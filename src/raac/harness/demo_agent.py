"""Minimal Registry → 9router → tools adapter for Chain; not full Layer 1."""

import asyncio
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import traceback
import uuid

from raac.common.errors import AgentExecutionError, RunGuardError
from raac.collaboration.levels.l3_collaboration.chain_state import (
    RoleHandoff, parse_role_output,
)
from raac.harness.layer2_core.prompt_hydrator import hydrate_messages
from raac.harness.layer2_core.context_manager import RoleContextPolicy, compact_tools, prepare_messages


class DemoAgentRunner:
    def __init__(self, registry, client, tools, *, provenance: dict, token_limit=50000,
                 context_policy: RoleContextPolicy | None = None):
        client.config.resolve_role_models({role.id: role.model.ref for role in registry.roles.values()})
        self.registry, self.client, self.tools = registry, client, tools
        self.provenance = provenance
        self.token_limit = token_limit
        self.context_policy = context_policy or RoleContextPolicy()

    def budget_guard(self, state: dict) -> bool:
        usage = self.client.usage.snapshot(run_id=state["work"]["run_id"])
        return usage["usage_complete"] and usage["total_tokens"] < self.token_limit

    def _check_budget(self, request):
        usage = self.client.usage.snapshot(run_id=request["run_id"])
        if not usage["usage_complete"]:
            raise RunGuardError("usage_unavailable")
        if usage["total_tokens"] >= self.token_limit:
            raise RunGuardError("budget_exceeded")

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
        listing = self.tools.workspace.list_files(limit=200)
        request["repository_files"] = [entry["path"] for entry in listing["files"]]
        request["repository_listing_truncated"] = listing["truncated"]
        request["execution_environment"] = {
            "mode": "local", "platform": sys.platform,
            "repo_dir": str(self.tools.workspace.root),
            "python_executable": sys.executable,
        }
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
        }
        self.client.trace_event(context, "agent_started", request=request)
        try:
            output = await self._run(role, request, context)
        except (Exception, asyncio.CancelledError) as exc:
            frames = [{"file": Path(frame.filename).name, "line": frame.lineno, "function": frame.name}
                      for frame in traceback.extract_tb(exc.__traceback__)]
            self.client.trace_event(context, "agent_failed", error_kind=type(exc).__name__,
                                    code=exc.code if isinstance(exc, AgentExecutionError) else (
                                        exc.reason if isinstance(exc, RunGuardError) else None),
                                    stack=frames)
            raise
        self.client.trace_event(context, "agent_completed",
                                output=output.output if isinstance(output, RoleHandoff) else output,
                                runtime=output.runtime if isinstance(output, RoleHandoff) else {})
        return output

    async def _run(self, role, request, context):
        name = role.spec.name
        code_edits_before = self.tools.workspace.code_edit_count
        messages = hydrate_messages(role, request, compact=self.context_policy.enabled)
        declarations = self.tools.declarations(role)
        if self.context_policy.enabled:
            declarations = compact_tools(declarations)
        for turn in range(1, role.spec.max_llm_calls + 1):
            self._check_budget(request)
            call_context = {**context, "turn": turn}
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
                        self.client.trace_event(call_context, "tool_arguments_invalid",
                                                tool_call_id=call["id"], tool_name=function.get("name"),
                                                arguments=function.get("arguments"), observation=observation)
                    else:
                        observation = await self.tools.execute(role, function.get("name"), arguments,
                                                                context=call_context, tool_call_id=call["id"])
                    messages.append({"role": "tool", "tool_call_id": call["id"],
                                     "content": json.dumps(observation, ensure_ascii=False)})
                continue
            content = message.get("content") or ""
            if name == "Coder":
                saved_edits = self.tools.workspace.code_edit_count != code_edits_before
                rebuild_invalid = False
                try:
                    patch = parse_role_output(name, content).root
                except ValueError:
                    if not saved_edits:
                        raise AgentExecutionError("invalid_coder_patch") from None
                    # File tools already saved the authoritative changes. Do not
                    # discard them because the model miscounted a summary hunk.
                    # The actual workspace patch is generated and validated below.
                    patch, rebuild_invalid = "", True
                if not saved_edits and patch:
                    applied = await self.tools.execute(role, "apply_patch", {"patch": patch, "base": "baseline"},
                                                       context=call_context, origin="adapter_coder_patch")
                    if not applied["ok"]:
                        raise AgentExecutionError("invalid_coder_patch")
                try:
                    patch = parse_role_output(name, self.tools.workspace.current_patch()).root
                except (ValueError, OSError):
                    raise AgentExecutionError("patch_generation_failed") from None
                final_hash = hashlib.sha256((self.tools.workspace.baseline_ref + "\0" + patch).encode()).hexdigest()
                if rebuild_invalid:
                    self.client.trace_event(call_context, "coder_patch_rebuilt", source="workspace",
                                            reason="invalid_model_diff", patch_hash=final_hash,
                                            model_patch_chars=len(content), patch_chars=len(patch))
                return RoleHandoff(patch, {
                    "summary": "Workspace changes saved" if patch else "No workspace changes",
                    "execution_evidence": self.tools.execution_evidence(context, final_hash),
                    "repository_changes": [{"path": path, "deleted": not (self.tools.workspace.root / path).is_file()}
                                           for path in sorted(self.tools.workspace.coder_paths)],
                })
            output = parse_role_output(name, content).model_dump()
            if name == "Tester":
                checked = await self.tools.execute(role, "run_tests", {}, context=call_context,
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
                runtime["summary"] = runtime["summary"] or "Observed local test execution"
                runtime["patch_hash"] = request["artifacts"]["patch_hash"]
                return RoleHandoff(output, runtime)
            if name == "Reviewer":
                return RoleHandoff(output, {"patch_hash": request["artifacts"]["patch_hash"]})
            return output
        raise AgentExecutionError("agent_call_limit")
