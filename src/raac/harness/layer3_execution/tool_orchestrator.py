"""Role-scoped file tools and local argv commands for MAS development."""

import asyncio
import hashlib
import json
import time
import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .tools.workspace import EditConflictError


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


class SearchRepo(Arguments):
    query: str = Field(min_length=1, max_length=256)


class ApplyPatch(Arguments):
    patch: str = Field(max_length=4194304)
    base: Literal["current", "baseline"] = "current"


SCHEMAS = {"read_file": ReadFile, "edit_file": EditFile, "search_repo": SearchRepo,
           "apply_patch": ApplyPatch, "run_tests": RunTests, "list_files": ListFiles,
           "delete_file": FilePath, "run_command": RunCommand, "read_files": ReadFiles,
           "replace_text": ReplaceText, "run_commands": RunCommands}
DESCRIPTIONS = {
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

    def declarations(self, role) -> list[dict]:
        unknown = set(role.spec.tools) - SCHEMAS.keys()
        if unknown:
            raise ValueError("Role contains an unknown tool")
        declarations = [{"type": "function", "function": {
            "name": name, "description": DESCRIPTIONS[name],
            "parameters": SCHEMAS[name].model_json_schema(),
        }} for name in role.spec.tools]
        maximum = self._command_timeout()
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
        budget = self._command_timeout() or self.timeout_seconds
        deadline = time.perf_counter() + budget
        results, reason = [], "completed"
        for index, item in enumerate(items, 1):
            remaining = deadline - time.perf_counter()
            if commands and remaining <= 0:
                reason = "batch_timeout"
                break
            request = dict(item)
            if commands:
                request["timeout_seconds"] = min(item["timeout_seconds"] or budget, remaining)
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
                "stop_reason": reason, **({"timeout_seconds": budget} if commands else {})}

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

    def execution_evidence(self, context, final_patch_hash):
        entries = self._evidence.get(context.get("agent_call_id"), [])[-8:]
        return [{**entry, "matches_final_patch": entry["patch_hash"] == final_patch_hash}
                for entry in entries]

    async def _execute(self, role, name: str, arguments: dict, *, context=None) -> dict:
        if name not in role.spec.tools or name not in SCHEMAS:
            return {"ok": False, "error": "Tool is not allowed for this role", "error_kind": "ToolNotAllowed"}
        try:
            args = SCHEMAS[name].model_validate(arguments).model_dump()
            if name in {"run_command", "run_commands"}:
                maximum = self._command_timeout()
                entries = args["commands"] if name == "run_commands" else [args]
                for entry in entries:
                    if maximum is not None and entry["timeout_seconds"] is not None and entry["timeout_seconds"] > maximum:
                        return {"ok": False, "error_kind": "TimeoutPolicyExceeded",
                                "error": f"timeout_seconds must be <= {maximum}; omit it to use the configured timeout.",
                                "max_timeout_seconds": maximum}
            if name in {"read_files", "run_commands"}:
                data = await asyncio.wait_for(self._batch(role, name, args, context), self.timeout_seconds)
            elif name in {"run_tests", "run_command"}:
                data = await asyncio.wait_for(getattr(self.workspace, name)(**args), self.timeout_seconds)
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
