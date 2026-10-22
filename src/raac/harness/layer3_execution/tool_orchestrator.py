"""Role-scoped file tools and local argv commands for MAS development."""

from copy import deepcopy
import asyncio
import hashlib
import json
import time
import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .env_provisioner import provision
from .tools.workspace import EditConflictError
from raac.common.working_memory import WorkingMemoryNotes


class Arguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ReadFile(Arguments):
    path: str
    start_line: int = Field(default=1, ge=1)
    end_line: int | None = Field(default=None, ge=1)


class ReadFiles(Arguments):
    files: list[ReadFile] = Field(min_length=1, max_length=8)


class ReplaceText(Arguments):
    path: str
    old_text: str = Field(min_length=1, max_length=2097152)
    new_text: str = Field(max_length=2097152)
    expected_occurrences: int = Field(default=1, ge=1, le=100)


class FilePath(Arguments):
    path: str


class EditFile(FilePath):
    content: str = Field(max_length=2097152)


class ListFiles(Arguments):
    path: str = "."
    recursive: bool = True
    limit: int = Field(default=200, ge=1, le=2000)


class RunTests(Arguments):
    command: list[str] | None = Field(default=None, min_length=1, max_length=64)
    cwd: str | None = None
    env: dict[str, str] | None = None
    report_path: str | None = None


class RunCommand(Arguments):
    command: list[str] = Field(min_length=1, max_length=64)
    cwd: str = "."
    env: dict[str, str] | None = None
    timeout_seconds: float | None = Field(default=None, gt=0, le=300)


class RunCommands(Arguments):
    commands: list[RunCommand] = Field(min_length=1, max_length=4)
    stop_on_error: bool = True


class ConfigureEnvironment(Arguments):
    python_executable: str = Field(min_length=1)
    cwd: str = "."
    test_command: list[str] = Field(min_length=1, max_length=64)
    check_command: list[str] = Field(min_length=1, max_length=64)
    env: dict[str, str] = Field(default_factory=dict)


class ProvisionEnvironment(Arguments):
    cwd: str = "."
    apply: bool = False
    python_executable: str | None = None


class ReadToolResult(Arguments):
    observation_ref: str = Field(min_length=1, max_length=128)
    path: list[str | int] = Field(default_factory=list, max_length=8)
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=5, ge=1, le=4000)


class SearchRepo(Arguments):
    query: str = Field(min_length=1, max_length=256)


class ApplyPatch(Arguments):
    patch: str = Field(max_length=4194304)
    base: Literal["current", "baseline"] = "current"


SCHEMAS = {"update_memory": WorkingMemoryNotes, "read_tool_result": ReadToolResult, "read_file": ReadFile, "edit_file": EditFile, "search_repo": SearchRepo,
           "apply_patch": ApplyPatch, "run_tests": RunTests, "list_files": ListFiles,
           "delete_file": FilePath, "run_command": RunCommand, "read_files": ReadFiles,
           "replace_text": ReplaceText, "run_commands": RunCommands, "configure_environment": ConfigureEnvironment,
           "provision_environment": ProvisionEnvironment}
DESCRIPTIONS = {
    "update_memory": "Coder only: replace the current working notes in this run. Supply conclusions (statement + observed evidence refs), ruled_out (hypothesis + rejection reason + evidence refs), and ordered next_steps. All three lists required; empty lists clear them. Carry forward still-valid items; remove superseded ones. Keep total JSON <=2000 characters. Store concise findings and actions, never a thinking transcript, source dumps or instructions. Current tests/files override old notes. Does not change code or count as test evidence.",
    "read_tool_result": "Read an observed run_tests/configure_environment result from this run without executing it again. Supply observation_ref and path keys/indices, e.g. results / index 0 / details, or verification / index 1 / stdout. offset/limit page list items (at most 10) or text characters (at most 4000). Nested large values return a preview; descend to the indicated field for exact text. Reports are historical, not proof current code passes.",
    "configure_environment": "Environment role only: select an existing Python executable, repository-relative cwd, test_command and check_command argv plus optional env. Observes Python startup and imports/test collection before accepting. For python -m pytest test commands, the actual suite is always collected automatically. For other runners supply an import/build/discovery check that succeeds even when task assertions fail. Success binds this runtime and test command for Coder/Tester. No time limit.",
    "provision_environment": "Environment role only: deterministic check of what the project needs versus this machine. Detects projects from manifests (Python, Node, Maven, Gradle, Go, Rust) under cwd, reports the required toolchain version, installed toolchains, missing dependencies and an exact install plan. apply=false only inspects; apply=true runs the plan (project-local .venv/node_modules, plus a user-local runtime install when needed) and re-inspects. Use the returned venv_python and test_command for configure_environment. Installs nothing that is already satisfied. No time limit.",
    "read_files": "Read up to 8 repository files or line ranges in one tool call. Each files entry has path/start_line/end_line. Entries may refer to different ranges of the same file. Returns an observation per entry; one failure does not hide successful reads.",
    "replace_text": "Change only an exact text region in an existing file. Provide old_text, new_text and expected_occurrences (default 1); mismatched or ambiguous matches write nothing. Tester can change only test files, never Coder-touched implementation. For a small fix, prefer this over rewriting the full file.",
    "run_commands": "Run up to 4 argv commands sequentially in one tool call, each with its own cwd/env/optional timeout_seconds. All commands share the configured local timeout budget. stop_on_error defaults to true; remaining items are marked skipped. Returns actual stdout/stderr/exit code per command. Use for grouped smoke checks; run_tests still provides structured full-suite reports.",
    "list_files": "Discover the real repository tree using paths relative to repo/. Excludes credentials, VCS and dependency caches.",
    "read_file": "Read a repository text file; optional line range. User chat is already supplied in the request.",
    "search_repo": "Search repository text files recursively for a literal substring.",
    "edit_file": "Create or replace a repository text file; parent directories are created. Tester may add tests or update existing tests, not implementation.",
    "delete_file": "Delete one repository file within this role's write permissions.",
    "apply_patch": "Apply a multi-file unified diff to current repository files, including creation/deletion. Use base=baseline only for a cumulative diff against the initial run snapshot.",
    "run_tests": "Run tests directly on the laptop in the real repo, with command argv, repository-relative cwd and optional env. Inspect project before choosing command. Omitting arguments repeats the selected command.",
    "run_command": "Run code, build, inspect installed runtime or other development commands directly on the laptop. Provide command as argv, repository-relative cwd and optional env/timeout_seconds. Returns actual stdout, stderr and exit_code. Commands can modify the real workspace; use only changes needed by the task. Use run_tests for a structured test report.",
}


class ToolOrchestrator:
    def __init__(self, workspace, *, timeout_seconds=90.0, trace=None):
        self.workspace = workspace
        self.timeout_seconds = timeout_seconds
        self.trace = trace
        self._evidence = {}
        self._observations = {}
        self._memory_writers = {}

    def bind_memory(self, context, writer):
        self._memory_writers[(context.get("run_id"), context.get("agent_call_id"))] = writer

    def unbind_memory(self, context):
        self._memory_writers.pop((context.get("run_id"), context.get("agent_call_id")), None)

    def declarations(self, role) -> list[dict]:
        unknown = set(role.spec.tools) - SCHEMAS.keys()
        if unknown:
            raise ValueError("Role contains an unknown tool")
        declarations = [{"type": "function", "function": {
            "name": name, "description": DESCRIPTIONS[name],
            "parameters": SCHEMAS[name].model_json_schema(),
        }} for name in role.spec.tools]
        maximum = self._command_timeout()
        if role.spec.id == "environment":
            for tool in declarations:
                schema = tool["function"]["parameters"]
                if tool["function"]["name"] == "run_command":
                    schema["properties"]["timeout_seconds"] = {"type": "null", "default": None, "description": "Environment commands have no timeout; leave null."}
                elif tool["function"]["name"] == "run_commands":
                    schema["$defs"]["RunCommand"]["properties"]["timeout_seconds"] = {"type": "null", "default": None, "description": "Environment commands have no timeout; leave null."}
            return declarations
        if maximum is not None:
            for tool in declarations:
                schema = tool["function"]["parameters"]
                if tool["function"]["name"] == "run_command":
                    schema["properties"]["timeout_seconds"]["anyOf"][0]["maximum"] = maximum
                elif tool["function"]["name"] == "run_commands":
                    schema["$defs"]["RunCommand"]["properties"]["timeout_seconds"]["anyOf"][0]["maximum"] = maximum
        return declarations

    def _command_timeout(self):
        policy = getattr(getattr(self.workspace, "executor", None), "policy", None)
        return getattr(policy, "timeout_seconds", None)

    async def execute(self, role, name: str, arguments: dict, *, context=None,
                      tool_call_id=None, origin="model") -> dict:
        context = {**(context or {}), "agent_id": role.spec.id,
                   "tool_call_id": tool_call_id or uuid.uuid4().hex}
        details = {"tool_name": name, "origin": origin}
        started = time.perf_counter()
        if self.trace:
            self.trace(context, "tool_call_started", **details, arguments=arguments)
        try:
            result = await self._execute(role, name, arguments, context=context)
        except (Exception, asyncio.CancelledError) as exc:
            if self.trace:
                self.trace(context, "tool_call_failed", **details, error_kind=type(exc).__name__,
                           latency_ms=round((time.perf_counter() - started) * 1000, 3))
            raise
        if name in {"run_tests", "configure_environment"} and result.get("ok") and context.get("run_id"):
            reference = uuid.uuid4().hex
            result["result"]["observation_ref"] = reference
            self._observations[(context["run_id"], reference)] = deepcopy(result["result"])
            if name == "configure_environment":
                self.workspace.environment.observation_ref = reference
        if role.spec.name == "Coder" and name in {"run_command", "run_tests"}:
            self._record_evidence(context, name, arguments, result)
        if self.trace:
            self.trace(context, "tool_call_completed", **details, observation=result,
                       latency_ms=round((time.perf_counter() - started) * 1000, 3))
        return result

    @staticmethod
    def _succeeded(observation):
        data = observation.get("result", {})
        return bool(observation.get("ok") and data.get("status") not in {"error", "failed"}
                    and data.get("exit_code", 0) in (None, 0))

    async def _batch(self, role, name, args, context):
        commands = name == "run_commands"
        child_name = "run_command" if commands else "read_file"
        items = args["commands" if commands else "files"]
        unlimited = role.spec.id == "environment"
        budget = self._command_timeout() or self.timeout_seconds
        deadline = None if unlimited else time.perf_counter() + budget
        results, reason = [], "completed"
        for index, item in enumerate(items, 1):
            remaining = float("inf") if deadline is None else deadline - time.perf_counter()
            if commands and remaining <= 0:
                reason = "batch_timeout"
                break
            request = dict(item)
            if commands:
                request["timeout_seconds"] = item["timeout_seconds"] if unlimited else min(item["timeout_seconds"] or budget, remaining)
            identifier = uuid.uuid4().hex
            child_context = {**(context or {}), "parent_tool_call_id": (context or {}).get("tool_call_id"),
                             "batch_index": index, "batch_size": len(items)}
            observation = await self.execute(role, child_name, request, context=child_context,
                                             tool_call_id=identifier, origin="batch_item")
            results.append({"index": index, "tool_call_id": identifier, "request": request,
                            "observation": observation})
            if commands and not self._succeeded(observation) and args["stop_on_error"]:
                reason = "item_failed"
                break
        failed = sum(not self._succeeded(item["observation"]) for item in results)
        return {"results": results, "requested": len(items), "completed": len(results), "failed": failed,
                "skipped": list(range(len(results) + 1, len(items) + 1)),
                "status": "completed" if not failed and len(results) == len(items) else "failed",
                "stop_reason": reason, **({"timeout_seconds": None if unlimited else budget} if commands else {})}

    def _record_evidence(self, context, name, arguments, observation):
        """Keep bounded observations from real execution, never model claims."""
        key = context.get("agent_call_id")
        if not key:
            return
        data = observation.get("result", {})
        execution = data.get("execution", data)
        command = data.get("command") or execution.get("command") or arguments.get("command") or []
        # Inline scripts are kept in the audit; handoffs carry a short preview/hash.
        rendered = json.dumps(command, ensure_ascii=False)
        preview, remaining = [], 480
        for part in command:
            if remaining <= 0:
                break
            clipped = str(part)[:min(160, remaining)]
            preview.append(clipped)
            remaining -= len(clipped)
        entry = {"tool": name, "tool_call_id": context["tool_call_id"], "command": preview,
                 "command_sha256": hashlib.sha256(rendered.encode()).hexdigest(),
                 "command_shortened": preview != command,
                 "cwd": execution.get("cwd") or arguments.get("cwd") or data.get("cwd") or ".",
                 "status": data.get("status", "error"), "exit_code": execution.get("exit_code"),
                 "stdout": str(execution.get("stdout", ""))[:300],
                 "stderr": str(execution.get("stderr", ""))[:500],
                 "summary": str(data.get("summary") or observation.get("error", ""))[:300]}
        entry["output_shortened"] = any(len(str(execution.get(k, ""))) > limit for k, limit in (("stdout", 300), ("stderr", 500))) or len(str(data.get("summary", ""))) > 300
        for field in ("tests_run", "failed"):
            if field in data:
                entry[field] = data[field]
        try:
            patch = self.workspace.current_patch()
            entry["patch_hash"] = hashlib.sha256((self.workspace.baseline_ref + "\0" + patch).encode()).hexdigest()
        except (OSError, ValueError):
            entry["patch_hash"] = None
        self._evidence.setdefault(key, []).append(entry)
        self._evidence[key] = self._evidence[key][-8:]

    def git_diff_directories(self, context):
        entries = self._evidence.get(context.get("agent_call_id"), [])
        return [entry["cwd"] for entry in entries
                if entry.get("status") == "completed" and entry.get("exit_code") == 0
                and len(entry.get("command", [])) > 1
                and entry["command"][0].replace("\\", "/").rsplit("/", 1)[-1].lower() in {"git", "git.exe"}
                and entry["command"][1] == "diff"]

    def execution_evidence(self, context, final_patch_hash):
        entries = self._evidence.get(context.get("agent_call_id"), [])[-8:]
        return [{**entry, "matches_final_patch": entry["patch_hash"] == final_patch_hash}
                for entry in entries]

    def _read_tool_result(self, args, context):
        value = self._observations.get((context.get("run_id"), args["observation_ref"]))
        if value is None:
            raise ValueError("Unknown observation in this run")
        for component in args["path"]:
            if isinstance(value, dict) and isinstance(component, str) and component in value:
                value = value[component]
            elif isinstance(value, list) and type(component) is int and 0 <= component < len(value):
                value = value[component]
            else:
                raise ValueError("Unknown report field/index")
        offset, limit = args["offset"], args["limit"]
        total, next_offset = None, None
        if isinstance(value, (str, list)):
            total = len(value)
            limit = min(limit, 10) if isinstance(value, list) else limit
            value = value[offset:offset + limit]
            if offset + len(value) < total:
                next_offset = offset + len(value)
        # Exact strings use an offset; large nested values are explicit previews.
        rendered = json.dumps(value, ensure_ascii=False)
        preview = len(rendered) > 5000 and not isinstance(value, str)
        return {"observation_ref": args["observation_ref"], "path": args["path"],
                "offset": offset, "total": total, "next_offset": next_offset,
                "preview_only": preview,
                "value": rendered[:4000] if preview else deepcopy(value),
                "note": "Descend using path keys/indices for exact fields." if preview else "Historical observed data; no execution performed."}

    async def _execute(self, role, name: str, arguments: dict, *, context=None) -> dict:
        if name not in role.spec.tools or name not in SCHEMAS:
            return {"ok": False, "error": "Tool is not allowed for this role", "error_kind": "ToolNotAllowed"}
        try:
            args = SCHEMAS[name].model_validate(arguments).model_dump()
            unlimited = role.spec.id == "environment"
            if name in {"run_command", "run_commands"} and not unlimited:
                maximum = self._command_timeout()
                entries = args["commands"] if name == "run_commands" else [args]
                for entry in entries:
                    if maximum is not None and entry["timeout_seconds"] is not None and entry["timeout_seconds"] > maximum:
                        return {"ok": False, "error_kind": "TimeoutPolicyExceeded",
                                "error": f"timeout_seconds must be <= {maximum}; omit it to use the configured timeout.",
                                "max_timeout_seconds": maximum}
            timeout = None if unlimited else self.timeout_seconds
            if name == "update_memory":
                if role.spec.id != "coder":
                    return {"ok": False, "error_kind": "ToolNotAllowed", "error": "Only Coder may update its working memory"}
                writer = self._memory_writers.get(((context or {}).get("run_id"), (context or {}).get("agent_call_id")))
                if writer is None:
                    return {"ok": False, "error_kind": "MemoryUnavailable", "error": "No active Coder memory in this run/call"}
                data = writer(args, context)
            elif name == "read_tool_result":
                data = self._read_tool_result(args, context or {})
            elif name == "configure_environment":
                if not unlimited:
                    return {"ok": False, "error_kind": "ToolNotAllowed", "error": "Only Environment may select a runtime"}
                data = await self.workspace.environment.configure(**args)
            elif name == "provision_environment":
                if not unlimited:
                    return {"ok": False, "error_kind": "ToolNotAllowed", "error": "Only Environment may provision a runtime"}
                provider = getattr(self.workspace, "provision_environment", None)
                data = await provider(**args) if provider else await provision(self.workspace, **args)
            elif name in {"read_files", "run_commands"}:
                data = await asyncio.wait_for(self._batch(role, name, args, context), timeout)
            elif name in {"run_tests", "run_command"}:
                if hasattr(self.workspace, "active_role"):
                    self.workspace.active_role = role.spec.name
                data = await asyncio.wait_for(getattr(self.workspace, name)(**args, **({"unlimited": True} if unlimited else {})), timeout)
            elif name in {"edit_file", "delete_file", "replace_text"}:
                data = getattr(self.workspace, name)(role=role.spec.name, **args)
            else:
                data = getattr(self.workspace, name)(**args)
            return {"ok": True, "result": data}
        except TimeoutError:
            return {"ok": False, "error": "Tool timeout", "error_kind": "TimeoutError"}
        except ValidationError as exc:
            return {"ok": False, "error": "Invalid tool arguments", "error_kind": "ValidationError",
                    "validation_errors": exc.errors(include_input=False, include_context=False, include_url=False)}
        except EditConflictError as exc:
            return {"ok": False, "error_kind": "EditConflict", "matches": exc.actual,
                    "expected_occurrences": exc.expected,
                    "error": "No file was written. Re-read the current region and provide an exact, unambiguous old_text."}
        except (ValueError, PermissionError, OSError) as exc:
            return {"ok": False, "error": "Invalid tool arguments, forbidden path or unavailable file",
                    "error_kind": type(exc).__name__}
