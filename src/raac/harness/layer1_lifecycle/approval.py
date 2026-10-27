"""Approval authorizes exactly one operation at one persisted revision."""

from dataclasses import dataclass

from .checkpoint import LifecycleError


@dataclass(frozen=True)
class ApprovalDecision:
    invocation_id: str
    revision: int
    operation_id: str
    arguments_hash: str
    approve: bool


class AgentPaused(Exception):
    def __init__(self, invocation_id: str, revision: int, reason: str):
        self.invocation_id, self.revision, self.reason = invocation_id, revision, reason
        super().__init__(reason)


def validate_approval(state, decision: ApprovalDecision) -> None:
    operation = state.get("pending_approval") or {}
    if (
        decision.invocation_id != state["invocation_id"]
        or decision.revision != state["revision"]
        or decision.operation_id != operation.get("id")
        or decision.arguments_hash != operation.get("arguments_hash")
        or type(decision.approve) is not bool
    ):
        raise LifecycleError("approval_mismatch")
