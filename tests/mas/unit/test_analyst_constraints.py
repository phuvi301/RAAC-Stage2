"""The Analyst node must accept the current spec contract (no `constraints` field)."""

import asyncio
from types import SimpleNamespace

from raac.collaboration.levels.l3_collaboration.chain_nodes import ChainNodes
from raac.collaboration.levels.l3_collaboration.chain_state import ChainLimits, WorkItem, initial_chain_state

SPEC = {
    "product_goal": "goal", "target_users": ["dev"], "user_flows": [], "functional_requirements": [],
    "non_functional_requirements": ["keep the public API stable"], "interface_contracts": {},
    "acceptance_criteria": ["Symbol('m', even=True).is_finite is True"], "out_of_scope": [],
    "assumptions": [], "open_questions": [],
}


def run_analyst(*, inherited_constraints=(), spec=SPEC):
    nodes = ChainNodes(runner=None)

    async def fake_call(state, node, role, incoming, artifacts):
        return SimpleNamespace(output=dict(spec)), None, []

    nodes._call = fake_call
    work = WorkItem(run_id="run-1", task_id="t", work_id="t", scope="task", requirement="x", baseline_ref="base",
                    constraints=list(inherited_constraints))
    state = initial_chain_state(
        work, ChainLimits(max_rounds=1, hard_max_rounds=1, timeout_seconds=10.0, agent_timeout_seconds=2.0), "chain")
    return asyncio.run(nodes.analyst(state))


def test_a_spec_without_constraints_goes_on_to_the_planner():
    update = run_analyst()  # used to raise KeyError: 'constraints'
    assert update["next_node"] == "planner" and update["ac_catalog"] == {"AC1": SPEC["acceptance_criteria"][0]}


def test_inherited_constraints_are_checked_against_the_non_functional_requirements():
    kept = run_analyst(inherited_constraints=["keep the public API stable"])
    assert kept["next_node"] == "planner"
    dropped = run_analyst(inherited_constraints=["never touch the database"])
    assert dropped["next_node"] == "result" and dropped["stop_reason"] == "changed_constraints"


def test_a_spec_that_still_has_constraints_keeps_working():
    legacy = {**SPEC, "constraints": ["never touch the database"]}
    assert run_analyst(inherited_constraints=["never touch the database"], spec=legacy)["next_node"] == "planner"
