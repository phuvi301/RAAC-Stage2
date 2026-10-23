"""Validated single-agent lifecycle, separate from collaboration state."""

from enum import StrEnum


class Status(StrEnum):
    IDLE = "IDLE"
    PLANNING = "PLANNING"
    EXECUTING_TOOL = "EXECUTING_TOOL"
    WAITING_HUMAN_APPROVAL = "WAITING_HUMAN_APPROVAL"
    FAILED = "FAILED"
    COMPLETED = "COMPLETED"


EDGES = {
    Status.IDLE: {Status.PLANNING},
    Status.PLANNING: {
        Status.EXECUTING_TOOL,
        Status.WAITING_HUMAN_APPROVAL,
        Status.COMPLETED,
        Status.FAILED,
    },
    Status.EXECUTING_TOOL: {Status.PLANNING, Status.FAILED},
    Status.WAITING_HUMAN_APPROVAL: {Status.EXECUTING_TOOL, Status.FAILED},
    Status.FAILED: set(),
    Status.COMPLETED: set(),
}


def transition(state: dict, target: str, emit) -> None:
    source = state["status"]
    if target not in EDGES.get(source, set()):
        emit(event="lifecycle_transition_rejected", from_state=source, to_state=target)
        raise ValueError("Invalid lifecycle transition")
    state["status"] = str(target)
    emit(event="lifecycle_transition", from_state=source, to_state=target)
