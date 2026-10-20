"""Failures that adapters may report to collaboration nodes without secret text."""


class RunGuardError(RuntimeError):
    def __init__(self, reason: str):
        if reason not in ("budget_exceeded", "usage_unavailable"):
            raise ValueError("Unknown run guard reason")
        self.reason = reason
        super().__init__(reason)


class AgentExecutionError(RuntimeError):
    """Allowlisted adapter failures, safe to expose in result/event metadata."""

    def __init__(self, code: str):
        if code not in {"too_many_tool_calls", "invalid_tool_call", "invalid_coder_patch",
                        "patch_generation_failed", "agent_call_limit"}:
            raise ValueError("Unknown agent error code")
        self.code = code
        super().__init__(code)
