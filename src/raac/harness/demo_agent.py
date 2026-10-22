"""Minimal Registry → 9router → tools adapter for Chain; not full Layer 1."""

import asyncio
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import traceback
import uuid

from raac.common.errors import AgentExecutionError, RunGuardError, WorkspaceSnapshotError
from raac.collaboration.levels.l3_collaboration.chain_state import (
    RoleHandoff, parse_role_output,
)
from raac.harness.layer2_core.prompt_hydrator import hydrate_messages
from raac.harness.layer2_core.memory.short_term import ShortTermMemory, ShortTermMemoryPolicy, action_receipt
from raac.harness.layer2_core.context_manager import RoleContextPolicy, compact_tools, prepare_messages


class DemoAgentRunner:
    def __init__(self, registry, client, tools, *, provenance: dict, token_limit=50000,
                 context_policy: RoleContextPolicy | None = None,
                 memory_policy: ShortTermMemoryPolicy | None = None):
        client.config.resolve_role_models({role.id: role.model.ref for role in registry.roles.values()})
        self.registry, self.client, self.tools = registry, client, tools
        self.provenance = provenance
        self.token_limit = token_limit
        self.context_policy = context_policy or RoleContextPolicy()
        self.memory_policy = memory_policy or ShortTermMemoryPolicy()
        self._memory_sessions = {}

    def role_timeout(self, name, default):
        timeout = self.registry.get(name).spec.timeout_seconds
        return default if timeout == "inherit" else timeout

    def budget_guard(self, state: dict) -> bool:
        usage = self.client.usage.snapshot(run_id=state["work"]["run_id"])
        return usage["usage_complete"] and usage["total_tokens"] < self.token_limit

    def _check_budget(self, request):
        usage = self.client.usage.snapshot(run_id=request["run_id"])
        if not usage["usage_complete"]:
            raise RunGuardError("usage_unavailable")
        if usage["total_tokens"] >= self.token_limit:
            raise RunGuardError("budget_exceeded")

    def _remember(self, context, kind, **data):
        memory = self._memory_sessions.get(context.get("agent_call_id"))
        if memory is None:
            return
        metrics = memory.append(kind, round_number=context.get("round", 0),
                                snapshot_transform=action_receipt if kind != "working_notes" else None, **data)
        # Memory is an audit artifact even when full LLM content tracing is off.
        self.client.trace.emit(context, event="memory_saved", **metrics)
        if metrics["compacted"]:
            self.client.trace.emit(context, event="memory_compacted", memory=memory.view(), **metrics)
        return metrics

    def _write_working_notes(self, context, notes):
        metrics = self._remember(context, "working_notes", notes=notes)
        return {"saved": True, "revision": metrics["revision"], "notes": metrics["stored_entry"]["notes"]}

    async def _execute_tool(self, role, name, arguments, *, context, **kwargs):
        try:
            observation = await self.tools.execute(role, name, arguments, context=context, **kwargs)
        except (Exception, asyncio.CancelledError) as exc:
            self._remember(context, "tool", tool=name, tool_call_id=kwargs.get("tool_call_id"),
                           arguments=arguments, observation={"ok": False, "error_kind": type(exc).__name__})
            raise
        if name in {"read_files", "run_commands"}:
            batch = observation.get("result", {})
            children = batch.get("results", [])
            for item in children:
                self._remember(context, "tool", tool="read_file" if name == "read_files" else "run_command",
                               tool_call_id=item.get("tool_call_id"), arguments=item.get("request"),
                               observation=item.get("observation"))
            if not children or batch.get("skipped"):
                self._remember(context, "tool", tool=name, arguments=arguments,
                               observation={"ok": observation.get("ok"),
                                            "error_kind": observation.get("error_kind"),
                                            "result": {key: batch[key] for key in
                                                       ("status", "stop_reason", "skipped") if key in batch}})
        else:
            self._remember(context, "tool", tool=name, tool_call_id=kwargs.get("tool_call_id"),
                           arguments=arguments, observation=observation)
        return observation

    def _file_matches(self, path, digest):
        try:
            workspace = self.tools.workspace
            text = workspace._read(workspace._path(path))
            return hashlib.sha256(text.encode()).hexdigest() == digest
        except (OSError, ValueError, UnicodeError):
            return False

    async def __call__(self, name: str, request: dict) -> dict | RoleHandoff:
        role = self.registry.get(name)
        request = deepcopy(request)
        context = {
            "run_id": request["run_id"], "task_id": request["task_id"],
            "work_id": request["work_id"], "scope": request["scope"],
            "level": "L3", "agent_id": role.spec.id,
            "registry_version": self.registry.version,
            "prompt_version": role.spec.prompt_version, "prompt_hash": role.prompt_hash,
            "provenance": self.provenance,
            "model_ref": role.spec.model.ref,
            "role": name, "round": request.get("round", 0),
            "topology": request.get("topology"), "topology_version": request.get("topology_version"),
            "agent_call_id": uuid.uuid4().hex,
            "unlimited_timeout": role.spec.id == "environment",
        }
        try:
            listing = self.tools.workspace.list_files(limit=200)
        except WorkspaceSnapshotError as exc:
            self.client.trace_event(context, "agent_failed", error_kind=type(exc).__name__,
                                    code=exc.code, stage="workspace_preflight")
            raise
        request["repository_files"] = [entry["path"] for entry in listing["files"]]
        request["repository_listing_truncated"] = listing["truncated"]
        docker_mode = getattr(self.tools.workspace, "execution_mode", "local") == "docker"
        default_python = self.tools.workspace.python_candidates()[0] if docker_mode else sys.executable
        request["execution_environment"] = {
            "mode": "docker" if docker_mode else "local", "platform": "linux" if docker_mode else sys.platform,
            "repo_dir": str(self.tools.workspace.root),
            "python_executable": (self.tools.workspace.environment.configuration or {}).get("python_executable", default_python),
            "prepared": deepcopy(self.tools.workspace.environment.configuration),
        }
        if docker_mode:
            request["execution_environment"]["policy"] = "Prebuilt offline container; verify its installed runtime. No host access or dependency downloads."
            request["execution_environment"]["benchmark_contract"] = (
                "implementation-only-v1: Coder edits implementation only; never create, edit or delete test files, "
                "including via shell or final patch. Tester creates separate NEW development test files only, "
                "never edits baseline tests or implementation. All roles may read existing tests. "
                "Coder candidate excludes Tester development tests. This benchmark contract overrides local demo test-edit guidance.")
        if name == "Environment":
            request["execution_environment"]["python_candidates"] = self.tools.workspace.python_candidates()
        if name == "Coder" and self.memory_policy.enabled:
            identity = {key: context[key] for key in ("run_id", "task_id", "work_id", "scope", "agent_id",
                        "registry_version", "prompt_version", "prompt_hash")}
            identity["baseline_ref"] = request.get("baseline_ref", self.tools.workspace.baseline_ref)
            memory = ShortTermMemory(self.client.log_path.parent / "memory", identity,
                                     self.memory_policy, redact=self.client.trace.redact)
            if memory.revision:
                request["role_memory"] = memory.view()
                self.client.trace.emit(context, event="memory_loaded", path=str(memory.path), revision=memory.revision,
                                 memory=request["role_memory"])
            self._memory_sessions[context["agent_call_id"]] = memory
            self.tools.bind_memory(context, lambda notes, call_context: self._write_working_notes(call_context, notes))
            report = request.get("artifacts", {}).get("test_report") or {}
            self._remember(context, "round_started", feedback={key: report[key] for key in
                           ("status", "tests_run", "failed", "blocking_issues") if key in report})
        self.client.trace_event(context, "agent_started", request=request)
        try:
            output = await self._run(role, request, context)
            if isinstance(output, RoleHandoff):
                self._remember(context, "handoff", summary=output.runtime.get("summary"),
                               repository_changes=output.runtime.get("repository_changes", []),
                               patch_hash=hashlib.sha256((request.get("baseline_ref", self.tools.workspace.baseline_ref)
                                                         + "\0" + str(output.output)).encode()).hexdigest())
        except (Exception, asyncio.CancelledError) as exc:
            self._remember(context, "failure", error_kind=type(exc).__name__,
                           code=getattr(exc, "code", getattr(exc, "reason", None)))
            frames = [{"file": Path(frame.filename).name, "line": frame.lineno, "function": frame.name}
                      for frame in traceback.extract_tb(exc.__traceback__)]
            self.client.trace_event(context, "agent_failed", error_kind=type(exc).__name__,
                                    code=exc.code if isinstance(exc, AgentExecutionError) else (
                                        exc.reason if isinstance(exc, RunGuardError) else None),
                                    stack=frames)
            raise
        finally:
            self.tools.unbind_memory(context)
            self._memory_sessions.pop(context["agent_call_id"], None)
        self.client.trace_event(context, "agent_completed",
                                output=output.output if isinstance(output, RoleHandoff) else output,
                                runtime=output.runtime if isinstance(output, RoleHandoff) else {})
        return output

    async def _run(self, role, request, context):
        name = role.spec.name
        code_edits_before = self.tools.workspace.code_edit_count
        environment_revision = self.tools.workspace.environment.revision
        messages = hydrate_messages(role, request, compact=self.context_policy.enabled)
        declarations = self.tools.declarations(role)
        if self.context_policy.enabled:
            declarations = compact_tools(declarations)
        for turn in range(1, role.spec.max_llm_calls + 1):
            self._check_budget(request)
            call_context = {**context, "turn": turn}
            memory = self._memory_sessions.get(context["agent_call_id"])
            if memory is not None:
                notes = next((item for item in memory.entries if item.get("kind") == "working_notes"), None)
                if notes is not None:
                    # Refresh the current notes only. Existing round-start receipts
                    # stay unchanged; live actions already have tool messages.
                    task = deepcopy(request)
                    previous = task.get("role_memory", {})
                    task["role_memory"] = {"version": memory.VERSION, "identity": deepcopy(memory.identity),
                        "revision": memory.revision,
                        "entries": [deepcopy(notes), *[entry for entry in previous.get("entries", []) if entry.get("kind") != "working_notes"]]}
                    messages[1] = hydrate_messages(role, task, compact=self.context_policy.enabled)[1]
            prepared, metrics = prepare_messages(messages, self.context_policy, file_validator=self._file_matches)
            if metrics["after_chars"] < metrics["before_chars"]:
                self.client.trace_event(call_context, "context_compacted", **metrics)
            remaining = role.spec.max_llm_calls - turn
            if remaining <= 1:
                prepared[0]["content"] += (
                    f"\nYou have {remaining + 1} model call(s) left including this call. "
                    "Prioritize a truthful structured handoff. Report unfinished work and blockers."
                )
            final_call = remaining == 0
            if final_call:
                prepared[0]["content"] += (
                    " This final call has no tools. Return the required output now: "
                    + ("unified diff only (empty text for no changes)." if name == "Coder" else
                       "one JSON object using observed results; do not claim unexecuted tests passed.")
                )
                self.client.trace_event(call_context, "agent_final_call", max_llm_calls=role.spec.max_llm_calls)
            completion = await self.client.complete(
                prepared, context=call_context, tools=[] if final_call else declarations, allow_empty=name == "Coder",
                model_ref=role.spec.model.ref,
            )
            call_context["llm_call_id"] = completion.call_id
            self._check_budget(request)
            message = completion.message
            if message.get("content"):
                self._remember(call_context, "assistant", content=message["content"])
            calls = message.get("tool_calls") or []
            if calls:
                if final_call:
                    raise AgentExecutionError("agent_call_limit")
                if not isinstance(calls, list) or len(calls) > 4:
                    raise AgentExecutionError("too_many_tool_calls")
                messages.append({"role": "assistant", "content": message.get("content"),
                                 "tool_calls": calls})
                for call in calls:
                    if (not isinstance(call, dict) or not isinstance(call.get("id"), str)
                            or call.get("type") != "function"
                            or not isinstance(call.get("function"), dict)):
                        raise AgentExecutionError("invalid_tool_call")
                    function = call.get("function", {})
                    try:
                        arguments = json.loads(function["arguments"])
                    except (ValueError, KeyError, TypeError):
                        observation = {"ok": False, "error": "Tool arguments must be a JSON object"}
                        self._remember(call_context, "tool", tool=function.get("name"),
                                       arguments=function.get("arguments"), observation=observation)
                        self.client.trace_event(call_context, "tool_arguments_invalid",
                                                tool_call_id=call["id"], tool_name=function.get("name"),
                                                arguments=function.get("arguments"), observation=observation)
                    else:
                        observation = await self._execute_tool(role, function.get("name"), arguments,
                                                                context=call_context, tool_call_id=call["id"])
                    messages.append({"role": "tool", "tool_call_id": call["id"],
                                     "content": json.dumps(observation, ensure_ascii=False)})
                continue
            content = message.get("content") or ""
            if name == "Coder":
                saved_edits = self.tools.workspace.code_edit_count != code_edits_before
                rebuild_reason = None
                try:
                    patch = parse_role_output(name, content).root
                except ValueError:
                    if not saved_edits:
                        raise AgentExecutionError("invalid_coder_patch") from None
                    # File tools already saved the authoritative changes. Do not
                    # discard them because the model miscounted a summary hunk.
                    # The actual workspace patch is generated and validated below.
                    patch, rebuild_reason = "", "invalid_model_diff"
                if not saved_edits and patch:
                    try:
                        selected, already_applied = self.tools.workspace.select_coder_patch(
                            patch, git_cwds=self.tools.git_diff_directories(call_context))
                    except ValueError:
                        raise AgentExecutionError("invalid_coder_patch") from None
                    if selected != patch:
                        self.client.trace_event(call_context, "coder_patch_rebased", source="observed_git_worktree")
                    patch = selected
                    if already_applied:
                        # A retry may repeat a Git HEAD diff for code already on
                        # disk. Verify its new side; never reapply that old base.
                        rebuild_reason = "already_applied_diff"
                    else:
                        applied = await self._execute_tool(role, "apply_patch", {"patch": patch, "base": "baseline"},
                                                           context=call_context, origin="adapter_coder_patch")
                        if not applied["ok"]:
                            raise AgentExecutionError("invalid_coder_patch")
                try:
                    patch = parse_role_output(name, self.tools.workspace.current_patch()).root
                except (ValueError, OSError):
                    raise AgentExecutionError("patch_generation_failed") from None
                final_hash = hashlib.sha256((self.tools.workspace.baseline_ref + "\0" + patch).encode()).hexdigest()
                if rebuild_reason:
                    self.client.trace_event(call_context, "coder_patch_rebuilt", source="workspace",
                                            reason=rebuild_reason, patch_hash=final_hash,
                                            model_patch_chars=len(content), patch_chars=len(patch))
                return RoleHandoff(patch, {
                    "summary": "Workspace changes saved" if patch else "No workspace changes",
                    "execution_evidence": self.tools.execution_evidence(context, final_hash),
                    "repository_changes": [{"path": path, "deleted": not (self.tools.workspace.root / path).is_file()}
                                           for path in sorted(self.tools.workspace.coder_paths)],
                })
            output = parse_role_output(name, content).model_dump()
            if name == "Environment":
                environment = self.tools.workspace.environment
                verified = environment.ready and environment.revision > environment_revision
                if output["status"] == "ready" and not verified:
                    output = {"status": "blocked", "summary": "No successful environment verification in this role invocation",
                              "blocking_issues": ["Call configure_environment successfully before reporting ready."]}
                return RoleHandoff(output, {"verified": verified,
                    "configuration": deepcopy(environment.configuration),
                    "verification": deepcopy(environment.verification), "smoke_report": deepcopy(environment.smoke_report),
                    "observation_ref": getattr(environment, "observation_ref", None)})
            if name == "Tester":
                checked = await self._execute_tool(role, "run_tests", {}, context=call_context,
                                                   origin="adapter_validation")
                observed = checked["result"] if checked["ok"] else {
                    "status": "error", "tests_run": 0, "failed": 0, "test_cases": [], "summary": checked["error"],
                }
                # Per-test results come from the runner, never the model. Runners
                # with aggregate reports get one explicitly labelled suite result.
                results = observed.get("results") or [{"test": "<observed test execution>",
                    "status": observed["status"], "details": observed["summary"]}]
                output["results"] = results
                output["tests_to_run"] = observed["test_cases"] or ["<observed test execution>"]
                output["verdict"] = "fail" if any(item["status"] in {"failed", "error"} for item in results) else "pass"
                if observed["status"] != "passed":
                    output["blocking_issues"].append("Observed local tests did not pass")
                output = parse_role_output(name, output).model_dump()
                runtime = {key: observed[key] for key in ("status", "tests_run", "failed", "test_cases", "summary")}
                runtime.update({key: observed[key] for key in ("skipped", "expected_failures") if key in observed})
                runtime["observation_ref"] = observed.get("observation_ref")
                runtime["summary"] = runtime["summary"] or "Observed local test execution"
                runtime["patch_hash"] = request["artifacts"]["patch_hash"]
                return RoleHandoff(output, runtime)
            if name == "Reviewer":
                return RoleHandoff(output, {"patch_hash": request["artifacts"]["patch_hash"]})
            return output
        raise AgentExecutionError("agent_call_limit")
