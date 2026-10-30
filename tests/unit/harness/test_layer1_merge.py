"""Merged Layer 1 surface: object state machine over upstream transitions, Agent over AgentRunner."""

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from raac.harness.agent import Agent
from raac.harness.layer1_lifecycle import AgentState, InvalidTransition, StateMachine
from raac.harness.layer1_lifecycle.state_machine import EDGES, can_transition
from raac.registry.loader import load_registry

ROOT = Path(__file__).resolve().parents[3]


def test_every_declared_edge_is_executable_and_logged():
    for source, targets in EDGES.items():
        for target in targets:
            rows = []
            machine = StateMachine(source, emit=lambda **event: rows.append(event))
            assert machine.transition(target) == target
            assert rows[-1]["event"] == "lifecycle_transition"
            assert machine.history == rows


def test_every_undeclared_edge_is_rejected_and_logged():
    for source in AgentState:
        for target in AgentState:
            if target in EDGES[source]:
                continue
            rows = []
            machine = StateMachine(source, emit=lambda **event: rows.append(event))
            assert not machine.can_transition(target)
            with pytest.raises(InvalidTransition):
                machine.transition(target)
            assert machine.status == source
            assert rows[-1]["event"] == "lifecycle_transition_rejected"


def test_invalid_transition_stays_a_value_error_for_existing_callers():
    assert issubclass(InvalidTransition, ValueError)
    assert not can_transition("NOT_A_STATE", "PLANNING")


def test_terminal_states_cannot_restart():
    for terminal in (AgentState.COMPLETED, AgentState.FAILED):
        assert not StateMachine(terminal).can_transition(AgentState.PLANNING)


def test_agent_binds_registry_role_and_exposes_the_four_layers():
    registry = load_registry(ROOT / "registry")
    calls = []

    async def record(name, request):
        calls.append((name, request))
        return {"ok": True}

    class FakeRunner:
        def __init__(self):
            self.registry, self.context_policy = registry, "ctx"
            self.memory_policy, self.tools = "mem", "tools"

        async def __call__(self, name, request):
            return await record(name, request)

    runner = FakeRunner()
    connector = object()
    agent = Agent.from_registry("coder", runner=runner, connectors=[connector])
    assert agent.role.spec.id == "coder"
    assert agent.layer1 is runner
    assert agent.layer2 == {"context": "ctx", "memory": "mem"}
    assert agent.layer3 == "tools"
    assert agent.layer4 == (connector,)
    assert asyncio.run(agent.run({"run_id": "r"})) == {"ok": True}
    assert calls == [(agent.role.spec.role, {"run_id": "r"})]


def test_agent_rejects_unknown_role():
    runner = SimpleNamespace(registry=load_registry(ROOT / "registry"))
    with pytest.raises(ValueError):
        Agent.from_registry("nope", runner=runner)
