"""Validated single-agent lifecycle, separate from collaboration state."""

from enum import StrEnum


class Status(StrEnum):
    IDLE = "IDLE"
    PLANNING = "PLANNING"
    EXECUTING_TOOL = "EXECUTING_TOOL"
    WAITING_HUMAN_APPROVAL = "WAITING_HUMAN_APPROVAL"
    FAILED = "FAILED"
    COMPLETED = "COMPLETED"


AgentState = Status  # Readable alias for callers outside the lifecycle module.

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


class InvalidTransition(ValueError):
    """Rejected lifecycle transition; ValueError keeps existing callers working."""


def can_transition(source: str, target: str) -> bool:
    try:
        return Status(target) in EDGES[Status(source)]
    except ValueError:
        return False


def transition(state: dict, target: str, emit) -> None:
    source = state["status"]
    if not can_transition(source, target):
        emit(event="lifecycle_transition_rejected", from_state=source, to_state=target)
        raise InvalidTransition("Invalid lifecycle transition")
    state["status"] = str(target)
    emit(event="lifecycle_transition", from_state=source, to_state=target)


class StateMachine:
    """Object view over the same guarded transitions, keeping an in-memory history.

    The runner persists the plain ``status`` string in its checkpoint; this class is
    for callers that want ``can_transition``/``history`` without handling raw dicts.
    """

    def __init__(self, initial: str = Status.IDLE, *, emit=None):
        self.state = {"status": str(Status(initial))}
        self.history: list[dict] = []
        self._emit = emit

    @property
    def status(self) -> Status:
        return Status(self.state["status"])

    def can_transition(self, target: str) -> bool:
        return can_transition(self.state["status"], target)

    def transition(self, target: str) -> Status:
        def record(**event):
            self.history.append(event)
            if self._emit:
                self._emit(**event)

        transition(self.state, target, record)
        return self.status
