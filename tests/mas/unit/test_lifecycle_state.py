import pytest
from raac.harness.layer1_lifecycle import state_machine


def test_lifecycle_refuses_invalid_and_terminal_transitions():
    assert hasattr(state_machine, "transition"), (
        "Lifecycle transitions are not implemented"
    )
    rows = []
    state = {"status": "IDLE"}
    state_machine.transition(state, "PLANNING", lambda **e: rows.append(e))
    state_machine.transition(state, "COMPLETED", lambda **e: rows.append(e))
    with pytest.raises(ValueError):
        state_machine.transition(state, "PLANNING", lambda **e: rows.append(e))
    assert state["status"] == "COMPLETED"
    assert rows[-1]["event"] == "lifecycle_transition_rejected"


@pytest.mark.parametrize(
    "start,end",
    [
        ("IDLE", "PLANNING"),
        ("PLANNING", "EXECUTING_TOOL"),
        ("PLANNING", "WAITING_HUMAN_APPROVAL"),
        ("PLANNING", "COMPLETED"),
        ("PLANNING", "FAILED"),
        ("WAITING_HUMAN_APPROVAL", "EXECUTING_TOOL"),
        ("WAITING_HUMAN_APPROVAL", "FAILED"),
        ("EXECUTING_TOOL", "PLANNING"),
        ("EXECUTING_TOOL", "FAILED"),
    ],
)
def test_lifecycle_allowed_transitions(start, end):
    assert hasattr(state_machine, "transition")
    state = {"status": start}
    state_machine.transition(state, end, lambda **e: None)
    assert state["status"] == end
