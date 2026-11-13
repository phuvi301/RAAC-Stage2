"""Every L3 topology starts with Environment, which runs once before the Hub hands the run to the Analyst."""

import asyncio
from types import SimpleNamespace

import pytest

from raac.collaboration.levels.l3_collaboration.chain_graph import build_chain_graph
from raac.collaboration.levels.l3_collaboration.chain_nodes import ChainNodes
from raac.collaboration.levels.l3_collaboration.chain_state import ChainLimits, WorkItem, initial_chain_state
from raac.collaboration.topologies import get_topology


def run_graph(topology, *, environment_ready=True):
    nodes = ChainNodes(runner=None)
    called = []

    async def fake_call(state, node, role, incoming, artifacts):
        called.append((node, incoming.get("from"), incoming.get("requirement")))
        if node == "environment":
            status = "ready" if environment_ready else "blocked"
            return SimpleNamespace(output={"status": status, "summary": "s", "blocking_issues": []},
                                   runtime={"verified": environment_ready, "configuration": {"cwd": "."}}), None, []
        # Stop after the Analyst: this test only checks how the run is entered.
        return None, {"next_node": "result", "stop_reason": "stopped_here", "verdict": None, "events": []}, []

    nodes._call = fake_call
    work = WorkItem(run_id="run-1", task_id="t", work_id="t", scope="task", requirement="Fix the bug",
                    baseline_ref="base")
    limits = ChainLimits(max_rounds=1, hard_max_rounds=1, timeout_seconds=10.0, agent_timeout_seconds=2.0)
    state = initial_chain_state(work, limits, topology, "rules")
    graph = build_chain_graph(nodes, topology=get_topology(topology))
    return called, asyncio.run(graph.ainvoke(state, {"recursion_limit": 30}))


@pytest.mark.parametrize("topology", ["chain", "chain_review_loop", "star"])
def test_environment_runs_first_then_the_analyst(topology):
    called, output = run_graph(topology)
    assert [node for node, _, _ in called] == ["environment", "analyst"]
    assert called[0][2] == "Fix the bug"  # Environment gets the raw requirement, as before
    assert output["environment_report"]["status"] == "ready"
    assert any(event["event"] == "hub_decision" and event["next"] == "analyst" for event in output["events"])


@pytest.mark.parametrize("topology", ["chain", "chain_review_loop", "star"])
def test_a_runtime_that_is_not_ready_stops_before_any_analyst_tokens(topology):
    called, output = run_graph(topology, environment_ready=False)
    assert [node for node, _, _ in called] == ["environment"]
    assert output["stop_reason"] == "environment_setup_failed"
    assert output["environment_report"]["status"] == "blocked"


@pytest.mark.parametrize("topology", ["chain", "chain_review_loop", "star"])
def test_the_declared_layout_enters_through_environment_then_hub(topology):
    spec = get_topology(topology)
    assert spec.entry_node == "environment"
    assert spec.incoming_nodes("environment") == ()
    assert "environment" in spec.incoming_nodes("hub")
    assert spec.incoming_nodes("analyst") == ("hub",)
