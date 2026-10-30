"""Failures that adapters may report to collaboration nodes without secret text."""


class RunGuardError(RuntimeError):
    def __init__(self, reason: str):
        if reason not in ("budget_exceeded", "usage_unavailable"):
            raise ValueError("Unknown run guard reason")
        self.reason = reason
        super().__init__(reason)


class WorkspaceSnapshotError(ValueError):
    """Source snapshot limit failure, distinct from invalid model output."""

    def __init__(self, code: str):
        messages = {"workspace_file_too_large": "Repository file exceeds size limit",
                    "workspace_snapshot_limit": "Repository snapshot exceeds configured limits"}
        if code not in messages:
            raise ValueError("Unknown workspace snapshot error code")
        self.code = code
        super().__init__(messages[code])


class AgentExecutionError(RuntimeError):
    """Allowlisted adapter failures, safe to expose in result/event metadata."""

    def __init__(self, code: str):
        if code not in {"too_many_tool_calls", "invalid_tool_call", "invalid_coder_patch",
                        "patch_generation_failed", "agent_call_limit"}:
            raise ValueError("Unknown agent error code")
        self.code = code
        super().__init__(code)


class SourceModifiedError(PermissionError):
    """A command changed repository files its role may not change; `paths` is a bounded list."""

    def __init__(self, message: str, paths=()):
        names = sorted(paths)
        self.paths, self.more = names[:10], max(0, len(names) - 10)
        super().__init__(message)


class RejectedEditError(ValueError):
    """A write that would leave a file worse than before; nothing was written."""

    REASONS = ("invalid_python", "tool_markup")

    def __init__(self, reason: str, path: str, line: int | None = None):
        if reason not in self.REASONS:
            raise ValueError("Unknown rejected edit reason")
        self.reason, self.path, self.line = reason, path, line
        super().__init__(f"{reason}: {path}")
