"""Reusable single-agent lifecycle with durable safe-boundary resume."""

import asyncio
import hashlib
import json
import sys
import time
import traceback
import uuid
from copy import deepcopy
from pathlib import Path

from raac.common.agent_contracts import (
    OutputFormatError,
    RoleHandoff,
    parse_role_output,
)
from raac.common.errors import (
    AgentExecutionError,
    RunGuardError,
    WorkspaceSnapshotError,
)
from raac.harness.layer2_core.context_manager import (
    CONCISE_VERSION,
    RoleContextPolicy,
    compact_tools,
    prepare_messages,
)
from raac.harness.layer2_core.context_metrics import request_metrics
from raac.harness.layer2_core.memory.short_term import (
    ShortTermMemory,
    ShortTermMemoryPolicy,
    action_receipt,
)
from raac.harness.layer2_core.prompt_hydrator import hydrate_messages
from raac.llm.client import CompletionFormatError, has_dsml_tool_call

from .approval import AgentPaused, ApprovalDecision, validate_approval
from .checkpoint import CheckpointStore, LifecycleError, digest
from .event_loop import build_event_loop
from .state_machine import transition


class AgentRunner:
    def __init__(
        self,
        registry,
        client,
        tools,
        *,
        provenance: dict,
        token_limit: int | None = 50000,
        context_policy: RoleContextPolicy | None = None,
        memory_policy: ShortTermMemoryPolicy | None = None,
        auto_approve: bool = True,
    ):
        client.config.resolve_role_models(
            {role.id: role.model.ref for role in registry.roles.values()}
        )
        self.registry, self.client, self.tools = registry, client, tools
        self.provenance = provenance
        self.token_limit = token_limit
        self.context_policy = context_policy or RoleContextPolicy()
        self.memory_policy = memory_policy or ShortTermMemoryPolicy()
        self._memory_sessions = {}
        self.auto_approve = auto_approve
        self.checkpoints = CheckpointStore(client.log_path.parent / "lifecycle.sqlite3")
        self._active_states = {}

    def role_timeout(self, name, default):
        timeout = self.registry.get(name).spec.timeout_seconds
        return default if timeout == "inherit" else timeout

    def budget_guard(self, state: dict) -> bool:
        usage = self.client.usage.snapshot(run_id=state["work"]["run_id"])
        return usage["usage_complete"] and (
            self.token_limit is None or usage["total_tokens"] < self.token_limit
        )

    def _check_budget(self, request):
        usage = self.client.usage.snapshot(run_id=request["run_id"])
        if not usage["usage_complete"]:
            raise RunGuardError("usage_unavailable")
        if self.token_limit is not None and usage["total_tokens"] >= self.token_limit:
            raise RunGuardError("budget_exceeded")

    def _remember(self, context, kind, **data):
        memory = self._memory_sessions.get(context.get("agent_call_id"))
        if memory is None:
            return
        metrics = memory.append(
            kind,
            round_number=context.get("round", 0),
            snapshot_transform=action_receipt if kind != "working_notes" else None,
            **data,
        )
        # Memory is an audit artifact even when full LLM content tracing is off.
        self.client.trace.emit(context, event="memory_saved", **metrics)
        if metrics["compacted"]:
            self.client.trace.emit(
                context, event="memory_compacted", memory=memory.view(), **metrics
            )
        return metrics

    def _write_working_notes(self, context, notes):
        metrics = self._remember(context, "working_notes", notes=notes)
        return {
            "saved": True,
            "revision": metrics["revision"],
            "notes": metrics["stored_entry"]["notes"],
        }

    async def _perform_tool(self, role, name, arguments, *, context, **kwargs):
        try:
            observation = await self.tools.execute(
                role, name, arguments, context=context, **kwargs
            )
        except (Exception, asyncio.CancelledError) as exc:
            self._remember(
                context,
                "tool",
                tool=name,
                tool_call_id=kwargs.get("tool_call_id"),
                arguments=arguments,
                observation={"ok": False, "error_kind": type(exc).__name__},
            )
            raise
        if name in {"read_files", "run_commands"}:
            batch = observation.get("result", {})
            children = batch.get("results", [])
            for item in children:
                self._remember(
                    context,
                    "tool",
                    tool="read_file" if name == "read_files" else "run_command",
                    tool_call_id=item.get("tool_call_id"),
                    arguments=item.get("request"),
                    observation=item.get("observation"),
                )
            if not children or batch.get("skipped"):
                self._remember(
                    context,
                    "tool",
                    tool=name,
                    arguments=arguments,
                    observation={
                        "ok": observation.get("ok"),
                        "error_kind": observation.get("error_kind"),
                        "result": {
                            key: batch[key]
                            for key in ("status", "stop_reason", "skipped")
                            if key in batch
                        },
                    },
                )
        else:
            self._remember(
                context,
                "tool",
                tool=name,
                tool_call_id=kwargs.get("tool_call_id"),
                arguments=arguments,
                observation=observation,
            )
        return observation

    def _file_matches(self, path, digest):
        try:
            workspace = self.tools.workspace
            text = workspace._read(workspace._path(path))
            return hashlib.sha256(text.encode()).hexdigest() == digest
        except (OSError, ValueError, UnicodeError):
            return False

    async def __call__(self, name: str, request: dict) -> dict | str | RoleHandoff:
        with self.checkpoints.lock(request["run_id"]):
            previous = self.checkpoints.latest_run(request["run_id"])
            if previous:
                if previous.get("inflight") or previous["status"] not in {
                    "COMPLETED",
                    "FAILED",
                }:
                    raise LifecycleError("lifecycle_requires_resume")
                self.client.usage.restore_run(request["run_id"], previous["usage"])
                self._restore_trace_sequence()
            return await self._start(name, request)

    async def _start(self, name: str, request: dict) -> dict | str | RoleHandoff:
        self.tools.workspace.invalidate_runtime_snapshot()
        role = self.registry.get(name)
        request = deepcopy(request)
        context = {
            "run_id": request["run_id"],
            "task_id": request["task_id"],
            "work_id": request["work_id"],
            "scope": request["scope"],
            "level": request.get("level", "L3"),
            "agent_id": role.spec.id,
            "registry_version": self.registry.version,
            "prompt_version": role.spec.prompt_version,
            "prompt_hash": role.prompt_hash,
            "provenance": self.provenance,
            "model_ref": role.spec.model.ref,
            "role": name,
            "round": request.get("round", 0),
            "topology": request.get("topology"),
            "topology_version": request.get("topology_version"),
            "agent_call_id": uuid.uuid4().hex,
            "unlimited_timeout": role.spec.id == "environment",
        }
        try:
            listing = self.tools.workspace.list_files(limit=200)
        except WorkspaceSnapshotError as exc:
            self.client.trace_event(
                context,
                "agent_failed",
                error_kind=type(exc).__name__,
                code=exc.code,
                stage="workspace_preflight",
            )
            raise
        request["repository_files"] = [entry["path"] for entry in listing["files"]]
        request["repository_listing_truncated"] = listing["truncated"]
        docker_mode = (
            getattr(self.tools.workspace, "execution_mode", "local") == "docker"
        )
        default_python = (
            self.tools.workspace.python_candidates()[0]
            if docker_mode
            else sys.executable
        )
        request["execution_environment"] = {
            "mode": "docker" if docker_mode else "local",
            "platform": "linux" if docker_mode else sys.platform,
            "repo_dir": str(self.tools.workspace.root),
            "python_executable": (
                self.tools.workspace.environment.configuration or {}
            ).get("python_executable", default_python),
            "prepared": deepcopy(self.tools.workspace.environment.configuration),
        }
        if docker_mode:
            request["execution_environment"]["policy"] = (
                "Prebuilt offline container; verify its installed runtime. No host access or dependency downloads."
            )
            request["execution_environment"]["benchmark_contract"] = (
                "implementation-only-v1: Coder edits implementation only; never create, edit or delete test files, "
                "including via shell or final patch. Tester creates separate NEW development test files only, "
                "never edits baseline tests or implementation. All roles may read existing tests. "
                "Coder candidate excludes Tester development tests. This benchmark contract overrides local demo test-edit guidance."
            )
        if name == "Environment":
            request["execution_environment"]["python_candidates"] = (
                self.tools.workspace.python_candidates()
            )
        if name == "Coder" and self.memory_policy.enabled:
            identity = {
                key: context[key]
                for key in (
                    "run_id",
                    "task_id",
                    "work_id",
                    "scope",
                    "agent_id",
                    "registry_version",
                    "prompt_version",
                    "prompt_hash",
                )
            }
            identity["baseline_ref"] = request.get(
                "baseline_ref", self.tools.workspace.baseline_ref
            )
            memory = ShortTermMemory(
                self.client.log_path.parent / "memory",
                identity,
                self.memory_policy,
                redact=self.client.trace.redact,
            )
            if memory.revision:
                request["role_memory"] = memory.view()
                self.client.trace.emit(
                    context,
                    event="memory_loaded",
                    path=str(memory.path),
                    revision=memory.revision,
                    memory=request["role_memory"],
                )
            self._memory_sessions[context["agent_call_id"]] = memory
            self.tools.bind_memory(
                context,
                lambda notes, call_context: self._write_working_notes(
                    call_context, notes
                ),
            )
            report = request.get("artifacts", {}).get("test_report") or {}
            self._remember(
                context,
                "round_started",
                feedback={
                    key: report[key]
                    for key in ("status", "tests_run", "failed", "blocking_issues")
                    if key in report
                },
            )
        self.client.trace_event(context, "agent_started", request=request)
        try:
            output = await self._run(role, request, context)
            if isinstance(output, RoleHandoff):
                self._remember(
                    context,
                    "handoff",
                    summary=output.runtime.get("summary"),
                    repository_changes=output.runtime.get("repository_changes", []),
                    patch_hash=hashlib.sha256(
                        (
                            request.get(
                                "baseline_ref", self.tools.workspace.baseline_ref
                            )
                            + "\0"
                            + str(output.output)
                        ).encode()
                    ).hexdigest(),
                )
        except AgentPaused:
            raise
        except (Exception, asyncio.CancelledError) as exc:
            self._remember(
                context,
                "failure",
                error_kind=type(exc).__name__,
                code=getattr(exc, "code", getattr(exc, "reason", None)),
            )
            frames = [
                {
                    "file": Path(frame.filename).name,
                    "line": frame.lineno,
                    "function": frame.name,
                }
                for frame in traceback.extract_tb(exc.__traceback__)
            ]
            self.client.trace_event(
                context,
                "agent_failed",
                error_kind=type(exc).__name__,
                code=exc.code
                if isinstance(exc, AgentExecutionError)
                else (exc.reason if isinstance(exc, RunGuardError) else None),
                stack=frames,
            )
            raise
        finally:
            self.tools.unbind_memory(context)
            self._memory_sessions.pop(context["agent_call_id"], None)
        self.client.trace_event(
            context,
            "agent_completed",
            output=output.output if isinstance(output, RoleHandoff) else output,
            runtime=output.runtime if isinstance(output, RoleHandoff) else {},
        )
        return output

    def _emit(self, state, **event):
        self.client.trace.emit(state["context"], **event)

    def _transition(self, state, target):
        transition(state, target, lambda **event: self._emit(state, **event))

    def _compatibility(self):
        # Include current code and registry, rather than trusting stale caller provenance.
        root = Path(__file__).resolve().parents[4]
        source = hashlib.sha256()
        for path in sorted((root / "src").rglob("*.py")):
            source.update(str(path.relative_to(root)).encode())
            source.update(path.read_bytes())
        return digest(
            {
                "source": source.hexdigest(),
                "registry": self.registry.snapshot(),
                "provenance": self.provenance,
                "llm": self.client.config.model_dump(mode="json"),
                "context": self.context_policy.model_dump(),
                "memory": self.memory_policy.model_dump(),
                "configs": {
                    str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in sorted((root / "configs").rglob("*.yaml"))
                },
                "log": str(self.client.log_path.resolve()),
                "token_limit": self.token_limit,
                "auto_approve": self.auto_approve,
            }
        )

    def _restore_trace_sequence(self):
        maximum = self.client.trace.sequence
        if self.client.log_path.exists():
            with self.client.log_path.open(encoding="utf-8") as stream:
                for line in stream:
                    try:
                        event = json.loads(line)
                    except ValueError:
                        raise LifecycleError("resume_trace_incomplete") from None
                    sequence = event.get("sequence")
                    if type(sequence) is not int or sequence < 1:
                        raise LifecycleError("resume_trace_invalid")
                    maximum = max(maximum, sequence)
        self.client.trace.sequence = maximum

    async def resume(
        self, invocation_id: str, *, approval: ApprovalDecision | None = None
    ) -> dict | str | RoleHandoff:
        state = self.checkpoints.load(invocation_id)
        with self.checkpoints.lock(state["run_id"]):
            state = self.checkpoints.load(invocation_id)
            if (
                state.get("compatibility") != self._compatibility()
                or state["context"].get("agent_call_id") != invocation_id
                or state["request"].get("run_id") != state["run_id"]
                or state["context"].get("run_id") != state["run_id"]
            ):
                raise LifecycleError("resume_identity_or_config_mismatch")
            if state.get("inflight"):
                raise LifecycleError("resume_unsafe_inflight")
            if not state.get("resumable"):
                raise LifecycleError("resume_redacted_payload")
            if state["status"] == "FAILED":
                raise LifecycleError("resume_terminal_failure")
            self.checkpoints.require_latest(state)
            if approval is not None:
                validate_approval(state, approval)
            self.tools.restore_runtime(state["runtime"])
            self.client.usage.restore_run(state["run_id"], state["usage"])
            self._restore_trace_sequence()
            if state["status"] == "COMPLETED":
                stored = state["result"]
                return (
                    RoleHandoff(stored["output"], stored["runtime"])
                    if stored["handoff"]
                    else stored["output"]
                )
            self._guard(state)
            context = state["context"]
            saved_memory = state.get("memory")
            if saved_memory:
                memory = ShortTermMemory(
                    self.client.log_path.parent / "memory",
                    saved_memory["identity"],
                    self.memory_policy,
                    redact=self.client.trace.redact,
                )
                if memory.view() != saved_memory:
                    raise LifecycleError("resume_memory_mismatch")
                self._memory_sessions[invocation_id] = memory
                self.tools.bind_memory(
                    context, lambda notes, ctx: self._write_working_notes(ctx, notes)
                )
            try:
                if approval is not None:
                    if not approval.approve:
                        self._transition(state, "FAILED")
                        state["error"] = "approval_rejected"
                        self._save(state)
                        raise LifecycleError("approval_rejected")
                    state["approved_operation"] = state["pending_approval"]
                self._emit(state, event="lifecycle_resumed", revision=state["revision"])
                result = await self._drive(state)
                self.client.trace_event(
                    context,
                    "agent_completed",
                    output=result.output if isinstance(result, RoleHandoff) else result,
                    runtime=result.runtime if isinstance(result, RoleHandoff) else {},
                )
                return result
            finally:
                self.tools.unbind_memory(context)
                self._memory_sessions.pop(invocation_id, None)

    def _save(self, state):
        started = time.perf_counter()
        state["runtime"] = self.tools.snapshot_runtime(refresh_workspace=False)
        state["usage"] = self.client.usage.export_run(state["run_id"])
        memory = self._memory_sessions.get(state["invocation_id"])
        state["memory"] = memory.view() if memory else None
        snapshot_done = time.perf_counter()
        import base64

        def semantic(value):
            if isinstance(value, str) and value.lstrip().startswith(("{", "[")):
                try:
                    return semantic(json.loads(value))
                except ValueError:
                    pass
            if isinstance(value, dict):
                return {key: semantic(item) for key, item in value.items()}
            if isinstance(value, list):
                return [semantic(item) for item in value]
            return value

        baseline = state["runtime"]["workspace"]["baseline_files"]
        cached = getattr(self, "_baseline_redaction", None)
        if (cached is None or cached[0] != baseline or cached[1] != self.client.trace.secrets
                or cached[3] != state["run_id"]):
            text = {name: base64.b64decode(data).decode("utf-8", errors="replace")
                    for name, data in baseline.items()}
            clean = semantic(self.client.trace.redact(text)) == semantic(text)
            cached = (dict(baseline), self.client.trace.secrets, clean, state["run_id"])
            self._baseline_redaction = cached
        runtime = state["runtime"]
        probe = deepcopy({**state, "runtime": {**runtime, "workspace": {
            key: value for key, value in runtime["workspace"].items() if key != "baseline_files"
        }}})
        safe = self.client.trace.redact(probe)
        changed = not cached[2] or semantic(safe) != semantic(probe)
        if changed:
            state["resumable"] = False
            safe["runtime"]["workspace"]["baseline_files"] = {}
            safe["resumable"] = False
        else:
            safe = probe
            safe["runtime"]["workspace"]["baseline_files"] = dict(baseline)
        redaction_done = time.perf_counter()
        metrics = self.checkpoints.save(safe)
        state["revision"] = safe["revision"]
        self._emit(state, event="checkpoint_timing", phase=state["phase"], revision=state["revision"],
                   snapshot_ms=round((snapshot_done - started) * 1000, 3),
                   redact_ms=round((redaction_done - snapshot_done) * 1000, 3),
                   total_ms=round((time.perf_counter() - started) * 1000, 3), **(metrics or {}))

    async def _run(self, role, request, context):
        executor_deadline = getattr(self.tools.workspace.executor, "deadline", None)
        deadline = request.get("deadline_at")
        effective = self.provenance.get("effective_config", {})
        defaults = effective.get("chain_limits", effective.get("harness", {}))
        role_timeout = self.role_timeout(
            role.spec.name, defaults.get("agent_timeout_seconds")
        )
        if role_timeout is not None:
            role_deadline = time.time() + role_timeout
            deadline = (
                min(deadline, role_deadline) if deadline is not None else role_deadline
            )
        if executor_deadline is not None:
            executor_wall_deadline = time.time() + executor_deadline - time.monotonic()
            deadline = (
                min(deadline, executor_wall_deadline)
                if deadline is not None
                else executor_wall_deadline
            )
        state = {
            "invocation_id": context["agent_call_id"],
            "run_id": request["run_id"],
            "revision": 0,
            "context": context,
            "request": request,
            "status": "IDLE",
            "phase": "reason",
            "turn": 0,
            "messages": hydrate_messages(
                role, request, compact=False
            ),
            "code_edits_before": self.tools.workspace.code_edit_count,
            "environment_revision": self.tools.workspace.environment.revision,
            "compatibility": self._compatibility(),
            "deadline_at": deadline,
            "inflight": None,
            "receipts": {},
            "resumable": True,
            "pending_approval": None,
            "approved_operation": None,
        }
        self._transition(state, "PLANNING")
        self._save(state)
        return await self._drive(state)

    async def _drive(self, state):
        identifier = state["invocation_id"]
        self._active_states[identifier] = state
        try:
            timeout = (
                None
                if state["deadline_at"] is None
                else max(0, state["deadline_at"] - time.time())
            )
            async with asyncio.timeout(timeout):
                result = await build_event_loop(self).ainvoke(
                    state,
                    {
                        "recursion_limit": 16
                        * self.registry.get(state["context"]["role"]).spec.max_llm_calls
                        + 32
                    },
                )
            stored = result["result"]
            return (
                RoleHandoff(stored["output"], stored["runtime"])
                if stored["handoff"]
                else stored["output"]
            )
        except AgentPaused:
            raise
        except (Exception, asyncio.CancelledError) as exc:
            latest = self._active_states[identifier]
            if latest["status"] not in {"FAILED", "COMPLETED"}:
                self._transition(latest, "FAILED")
                latest["error"] = getattr(
                    exc, "code", getattr(exc, "reason", type(exc).__name__)
                )
                # Preserve the original failure if the workspace was destroyed by cancellation.
                try:
                    self._save(latest)
                except (OSError, ValueError, TimeoutError):
                    pass
            raise
        finally:
            self._active_states.pop(identifier, None)

    def _guard(self, state):
        self._check_budget(state["request"])
        if state["deadline_at"] is not None and time.time() >= state["deadline_at"]:
            raise TimeoutError("Lifecycle deadline exceeded")

    async def _step(self, state, phase):
        self._active_states[state["invocation_id"]] = state
        self._guard(state)
        await getattr(self, "_" + phase)(state)
        if phase != "reason":
            self._save(state)
        return state

    async def _reason(self, state):
        role, request, context = (
            self.registry.get(state["context"]["role"]),
            state["request"],
            state["context"],
        )
        turn = state["turn"] + 1
        if turn > role.spec.max_llm_calls:
            raise AgentExecutionError("agent_call_limit")
        state["turn"] = turn
        call_context = state["call_context"] = {**context, "turn": turn}
        messages = state["messages"]
        memory = self._memory_sessions.get(state["invocation_id"])
        if memory:
            notes = next(
                (
                    item
                    for item in memory.entries
                    if item.get("kind") == "working_notes"
                ),
                None,
            )
            if notes:
                task = deepcopy(request)
                previous = task.get("role_memory", {})
                task["role_memory"] = {
                    "version": memory.VERSION,
                    "identity": deepcopy(memory.identity),
                    "revision": memory.revision,
                    "entries": [
                        deepcopy(notes),
                        *[
                            entry
                            for entry in previous.get("entries", [])
                            if entry.get("kind") != "working_notes"
                        ],
                    ],
                }
                messages[1] = hydrate_messages(
                    role, task, compact=False
                )[1]
        preparation_started = time.perf_counter()
        raw_declarations = self.tools.declarations(role)
        prepared, metrics = prepare_messages(
            messages, self.context_policy, file_validator=self._file_matches
        )
        if metrics["after_chars"] < metrics["before_chars"]:
            self.client.trace_event(call_context, "context_compacted", **metrics)
        remaining = role.spec.max_llm_calls - turn
        handoff_turn = role.spec.max_llm_calls - (role.spec.name in {"Analyst", "Tester"})
        tool_turns_left = max(0, handoff_turn - turn)
        if 0 < tool_turns_left <= 2:
            prepared[0]["content"] += (
                f"\nPrepare to hand off: {tool_turns_left} tool-enabled model call(s) remain including this call. "
                f"Tools will be unavailable starting at call {handoff_turn}. "
                "Stop broad exploration. Use remaining tools only for essential missing evidence, "
                "then return the required handoff from observed results. Report uncertainties and "
                "unfinished checks as blockers; do not invent results. Do not emit DSML to bypass unavailable tools."
            )
        if role.spec.name == "Tester" and tool_turns_left > 0:
            if self.tools.workspace.test_spec is None:
                prepared[0]["content"] += (
                    "\nNo test suite has been selected. Prioritize identifying the repository's native test command "
                    "and calling run_tests(command=[...], cwd='.', env={}) while tools are available. "
                    "run_command/run_commands probes and Coder evidence do not select your test suite. "
                    "Choose a focused suite covering the acceptance criteria before optional exploration; "
                    "do not guess a framework or weaken assertions to obtain a pass."
                )
            elif tool_turns_left <= 2:
                prepared[0]["content"] += (
                    "\nA test suite is selected. Prioritize running it on any updated tests via run_tests, "
                    "then hand off observed failures, coverage gaps and blockers. Avoid unrelated probes."
                )
        if role.spec.name == "Coder":
            seconds = None if state["deadline_at"] is None else max(0, int(state["deadline_at"] - time.time()))
            progress = {
                "calls_left_including_this": remaining + 1,
                "seconds_left": seconds,
                "evidence": self.tools.coder_progress(context),
                "code_edit_count": self.tools.workspace.code_edit_count,
            }
            prepared[0]["content"] += "\nRuntime progress: " + json.dumps(progress)
            prepared[0]["content"] += (
                " Hand off after adequate successful smoke checks. Expand checks or reverse an edit only for new failure, feedback or concrete risk."
            )
            if seconds is not None and seconds <= 60:
                prepared[0]["content"] += " Deadline is near: prioritize a truthful handoff and state unfinished checks."
        if remaining <= 1:
            prepared[0]["content"] += (
                f"\nYou have {remaining + 1} model call(s) left including this call. "
                "Prioritize a truthful structured handoff. Report unfinished work and blockers."
            )
        final_call = remaining == 0 or (role.spec.name in {"Analyst", "Tester"} and remaining == 1)
        if final_call:
            prepared[0]["content"] += (
                f" This {'final' if remaining == 0 else 'handoff'} call has no tools. Return the required output now: "
                + (
                    "unified diff only (empty text for no changes)."
                    if role.spec.name == "Coder"
                    else "one concise complete JSON object using observed results; avoid repeated explanations and do not claim unexecuted tests passed."
                )
            )
            self.client.trace_event(
                call_context, "agent_final_call", max_llm_calls=role.spec.max_llm_calls,
                repair_call_reserved=remaining > 0,
            )
        declarations = raw_declarations
        if self.context_policy.enabled:
            declarations = compact_tools(declarations)
        call_context["context_metrics"] = {
            "before": request_metrics(messages, [] if final_call else raw_declarations),
            "after": request_metrics(prepared, [] if final_call else declarations),
            "preparation_ms": round((time.perf_counter() - preparation_started) * 1000, 3),
            "policy": self.context_policy.model_dump(),
            "concise_version": CONCISE_VERSION if self.context_policy.concise_output else None,
        }
        state["inflight"] = {"kind": "llm", "turn": turn}
        self._save(state)
        try:
            completion = await self.client.complete(
                prepared,
                context=call_context,
                tools=[] if final_call else declarations,
                allow_empty=role.spec.name == "Coder",
                model_ref=role.spec.model.ref,
            )
        except CompletionFormatError as exc:
            call_context["llm_call_id"] = exc.call_id
            state.update(message={"role": "assistant", "content": exc.content}, completion_error=exc.code)
        else:
            call_context["llm_call_id"] = completion.call_id
            state.update(message=completion.message, completion_error=None)
        state.update(inflight=None, phase="decide")
        self._save(state)
        self._guard(state)

    async def _decide(self, state):
        message, context = state["message"], state["call_context"]
        completion_error = state.pop("completion_error", None)
        if completion_error:
            self._recover_output(state, completion_error,
                                 ("Tools are unavailable. Return the required handoff JSON, no DSML. "
                                  if state["turn"] >= self.registry.get(context["role"]).spec.max_llm_calls - (context["role"] in {"Analyst", "Tester"})
                                  else "Use native tool_calls while tools are available; otherwise return the required handoff. ") +
                                 "The prior completion had an invalid format or was empty/cut off. Return a complete replacement. "
                                 "Preserve all required fields and acceptance criteria; avoid repeated explanations.")
            return
        if message.get("content"):
            self._remember(context, "assistant", content=message["content"])
        calls = message.get("tool_calls") or []
        if calls:
            maximum = self.registry.get(context["role"]).spec.max_llm_calls
            if state["turn"] == maximum or (context["role"] in {"Analyst", "Tester"} and state["turn"] == maximum - 1):
                raise AgentExecutionError("agent_call_limit")
            if not isinstance(calls, list) or len(calls) > 4:
                raise AgentExecutionError("too_many_tool_calls")
            state["messages"].append(
                {
                    "role": "assistant",
                    "content": message.get("content"),
                    "tool_calls": calls,
                }
            )
            state.update(calls=calls, tool_index=0, phase="act")
        else:
            content = message.get("content") or ""
            role = context["role"]
            try:
                parse_role_output(role, content)
            except OutputFormatError as exc:
                if has_dsml_tool_call(content):
                    maximum = self.registry.get(role).spec.max_llm_calls
                    handoff = state["turn"] >= maximum - (role in {"Analyst", "Tester"})
                    detail = ("Tools are unavailable. Return the required handoff JSON using observed results; no tool calls or DSML."
                              if handoff else "Use native tool_calls while tools are available, never DSML text. Otherwise return the required handoff.")
                    self._recover_output(state, "invalid_tool_call_format", detail)
                elif role == "Coder" and self.tools.workspace.code_edit_count != state["code_edits_before"]:
                    state["phase"] = "finalize"
                else:
                    self._recover_output(state, "invalid_output", str(exc))
                return
            state["phase"] = "finalize"

    def _recover_output(self, state, code, detail):
        self._guard(state)
        context = state["call_context"]
        detail = self.client.trace.redact(detail)[:1200]
        self.client.trace_event(context, "output_invalid", code=code, detail=detail)
        if state.get("output_recoveries", 0) or state["turn"] >= self.registry.get(context["role"]).spec.max_llm_calls:
            self.client.trace_event(context, "output_recovery_failed", code=code)
            raise AgentExecutionError(code)
        state["output_recoveries"] = 1
        if state["message"].get("content"):
            state["messages"].append({"role": "assistant", "content": state["message"]["content"]})
        state["messages"].append(
            {"role": "user", "content": "Correct the response format once using the required output_schema. "
             "Use only observed evidence; do not repeat completed operations. " + detail})
        state["phase"] = "reason"
        self.client.trace_event(context, "output_recovery_started", code=code)

    async def _execute_tool(self, role, name, arguments, *, context, **kwargs):
        state = self._active_states[context["agent_call_id"]]
        identifier = kwargs.get("tool_call_id")
        operation_id = (
            f"turn:{context['turn']}:{state['tool_index']}:{identifier}"
            if identifier
            else f"finalize:{state['turn']}:" + kwargs.get("origin", "adapter") + ":" + name
        )
        operation = {
            "id": operation_id,
            "name": name,
            "arguments_hash": digest(arguments),
        }
        receipt = state["receipts"].get(operation_id)
        if receipt is not None:
            if receipt["operation"] != operation:
                raise LifecycleError("operation_identity_mismatch")
            return deepcopy(receipt["observation"])
        self._guard(state)
        if not self.auto_approve and state.get("approved_operation") != operation:
            if state["status"] != "WAITING_HUMAN_APPROVAL":
                self._transition(state, "WAITING_HUMAN_APPROVAL")
                state["pending_approval"] = operation
                self._save(state)
                self._emit(
                    state,
                    event="lifecycle_paused",
                    revision=state["revision"],
                    operation=operation,
                )
            raise AgentPaused(
                state["invocation_id"], state["revision"], "approval_required"
            )
        state["approved_operation"] = state["pending_approval"] = None
        self._transition(state, "EXECUTING_TOOL")
        state["inflight"] = operation
        self._save(state)
        observation = await self._perform_tool(
            role, name, arguments, context=context, **kwargs
        )
        state["receipts"][operation_id] = {
            "operation": operation,
            "observation": observation,
        }
        state["inflight"] = None
        self._transition(state, "PLANNING")
        self._save(state)
        return observation

    async def _act(self, state):
        call = state["calls"][state["tool_index"]]
        if (
            not isinstance(call, dict)
            or not isinstance(call.get("id"), str)
            or not call["id"]
            or call.get("type") != "function"
            or not isinstance(call.get("function"), dict)
        ):
            raise AgentExecutionError("invalid_tool_call")
        function, context = call["function"], state["call_context"]
        try:
            arguments = json.loads(function["arguments"])
        except (ValueError, KeyError, TypeError) as exc:
            detail = f"Invalid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}" if isinstance(exc, json.JSONDecodeError) else "Tool arguments must be a JSON object"
            observation = {"ok": False, "error": detail}
            self._remember(
                context,
                "tool",
                tool=function.get("name"),
                arguments=function.get("arguments"),
                observation=observation,
            )
            self.client.trace_event(
                context,
                "tool_arguments_invalid",
                tool_call_id=call["id"],
                tool_name=function.get("name"),
                arguments=function.get("arguments"),
                observation=observation,
            )
        else:
            observation = await self._execute_tool(
                self.registry.get(context["role"]),
                function.get("name"),
                arguments,
                context=context,
                tool_call_id=call["id"],
            )
        state.update(observation=observation, phase="observe")

    async def _observe(self, state):
        call = state["calls"][state["tool_index"]]
        state["messages"].append(
            {
                "role": "tool",
                "tool_call_id": call["id"],
                "content": json.dumps(state.pop("observation"), ensure_ascii=False),
            }
        )
        state["tool_index"] += 1
        state["phase"] = (
            "act" if state["tool_index"] < len(state["calls"]) else "reason"
        )

    async def _finalize(self, state):
        output = await self._finalize_output(state)
        if output is None:
            return
        handoff = isinstance(output, RoleHandoff)
        state["result"] = {
            "handoff": handoff,
            "output": output.output if handoff else output,
            "runtime": output.runtime if handoff else {},
        }
        self._transition(state, "COMPLETED")
        state["phase"] = "done"

    def _continue_testing(self, state, code, detail):
        self._guard(state)
        if state["turn"] >= self.registry.get("Tester").spec.max_llm_calls:
            raise AgentExecutionError(code)
        state["messages"].extend([
            {"role": "assistant", "content": state["message"].get("content") or ""},
            {"role": "user", "content": detail},
        ])
        state["phase"] = "reason"
        self.client.trace_event(state["call_context"], "tester_handoff_incomplete", code=code)

    async def _finalize_output(self, state):
        context = state["context"]
        role = self.registry.get(context["role"])
        name, request = role.spec.name, state["request"]
        message, call_context = state["message"], state["call_context"]
        code_edits_before = state["code_edits_before"]
        environment_revision = state["environment_revision"]
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
                        patch, git_cwds=self.tools.git_diff_directories(call_context)
                    )
                except ValueError:
                    raise AgentExecutionError("invalid_coder_patch") from None
                if selected != patch:
                    self.client.trace_event(
                        call_context,
                        "coder_patch_rebased",
                        source="observed_git_worktree",
                    )
                patch = selected
                if already_applied:
                    # A retry may repeat a Git HEAD diff for code already on
                    # disk. Verify its new side; never reapply that old base.
                    rebuild_reason = "already_applied_diff"
                else:
                    applied = await self._execute_tool(
                        role,
                        "apply_patch",
                        {"patch": patch, "base": "baseline"},
                        context=call_context,
                        origin="adapter_coder_patch",
                    )
                    if not applied["ok"]:
                        raise AgentExecutionError("invalid_coder_patch")
            try:
                patch = parse_role_output(
                    name, self.tools.workspace.current_patch()
                ).root
            except (ValueError, OSError):
                raise AgentExecutionError("patch_generation_failed") from None
            final_hash = hashlib.sha256(
                (self.tools.workspace.baseline_ref + "\0" + patch).encode()
            ).hexdigest()
            if rebuild_reason:
                self.client.trace_event(
                    call_context,
                    "coder_patch_rebuilt",
                    source="workspace",
                    reason=rebuild_reason,
                    patch_hash=final_hash,
                    model_patch_chars=len(content),
                    patch_chars=len(patch),
                )
            return RoleHandoff(
                patch,
                {
                    "summary": "Workspace changes saved"
                    if patch
                    else "No workspace changes",
                    "execution_evidence": self.tools.execution_evidence(
                        context, final_hash
                    ),
                    "repository_changes": [
                        {
                            "path": path,
                            "deleted": not (self.tools.workspace.root / path).is_file(),
                        }
                        for path in sorted(self.tools.workspace.coder_paths)
                    ],
                },
            )
        output = parse_role_output(name, content).model_dump()
        if name == "Environment":
            environment = self.tools.workspace.environment
            verified = environment.ready and environment.revision > environment_revision
            if output["status"] == "ready" and not verified:
                output = {
                    "status": "blocked",
                    "summary": "No successful environment verification in this role invocation",
                    "blocking_issues": [
                        "Call configure_environment successfully before reporting ready."
                    ],
                }
            return RoleHandoff(
                output,
                {
                    "verified": verified,
                    "configuration": deepcopy(environment.configuration),
                    "verification": deepcopy(environment.verification),
                    "smoke_report": deepcopy(environment.smoke_report),
                    "observation_ref": getattr(environment, "observation_ref", None),
                },
            )
        if name == "Tester":
            if self.tools.workspace.test_spec is None:
                self._continue_testing(state, "missing_test_command",
                    "Inspect the project's test framework and call run_tests(command=[...], cwd='.', env=...). "
                    "No suite has been selected. Do not guess a framework or report unobserved results.")
                return None
            checked = await self._execute_tool(
                role, "run_tests", {}, context=call_context, origin="adapter_validation"
            )
            observed = (
                checked["result"]
                if checked["ok"]
                else {
                    "status": "error",
                    "tests_run": 0,
                    "failed": 0,
                    "test_cases": [],
                    "summary": checked["error"],
                }
            )
            catalog = request.get("artifacts", {}).get("ac_catalog", {})
            if observed["status"] == "passed" and catalog and set(output["covered_ac_ids"]) != set(catalog):
                self._continue_testing(state, "incomplete_test_coverage",
                    "The selected suite passed, but AC coverage is incomplete. Missing IDs: "
                    + ", ".join(sorted(set(catalog) - set(output["covered_ac_ids"])))
                    + ". Add independent development tests and run_tests for these criteria before handing off. "
                    "Do not invent coverage or weaken criteria. If implementation fails, report the observed failure. "
                    "blocking_issues contains actionable unresolved problems only, never passing test counts.")
                return None
            # Per-test results come from the runner, never the model. Runners
            # with aggregate reports get one explicitly labelled suite result.
            results = observed.get("results") or [
                {
                    "test": "<observed test execution>",
                    "status": observed["status"],
                    "details": observed["summary"],
                }
            ]
            output["results"] = results
            output["tests_to_run"] = observed["test_cases"] or [
                "<observed test execution>"
            ]
            output["verdict"] = (
                "fail"
                if any(item["status"] in {"failed", "error"} for item in results)
                else "pass"
            )
            if observed["status"] != "passed":
                output["blocking_issues"].append("Observed local tests did not pass")
            output = parse_role_output(name, output).model_dump()
            runtime = {
                key: observed[key]
                for key in ("status", "tests_run", "failed", "test_cases", "summary")
            }
            runtime.update(
                {
                    key: observed[key]
                    for key in ("skipped", "expected_failures")
                    if key in observed
                }
            )
            runtime["observation_ref"] = observed.get("observation_ref")
            runtime["summary"] = runtime["summary"] or "Observed local test execution"
            runtime["patch_hash"] = request["artifacts"]["patch_hash"]
            return RoleHandoff(output, runtime)
        if name == "Reviewer":
            return RoleHandoff(
                output, {"patch_hash": request["artifacts"]["patch_hash"]}
            )
        return output
