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


class WorkspaceIntegrityError(PermissionError):
    """A verified repository mutation, with a safe relative path for diagnostics."""

    def __init__(self, message, path):
        super().__init__(message)
        self.path = path


class AgentExecutionError(RuntimeError):
    """Allowlisted adapter failures, safe to expose in result/event metadata."""

    def __init__(self, code: str):
        if code not in {"too_many_tool_calls", "invalid_tool_call", "invalid_coder_patch",
                        "patch_generation_failed", "agent_call_limit", "invalid_output",
                        "empty_completion", "truncated_completion",
                        "missing_test_command", "incomplete_test_coverage"}:
            raise ValueError("Unknown agent error code")
        self.code = code
        super().__init__(code)
