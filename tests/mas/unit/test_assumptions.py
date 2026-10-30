"""Undecided behaviour is settled by a recorded Analyst assumption; it never blocks and is never asked."""

import unittest

from pydantic import ValidationError
from test_chain_nodes import FakeAgents, make_state

from raac.benchmark.run_record import RunRecord
from raac.collaboration.levels.l3_collaboration import ChainNodes, build_chain_graph
from raac.common.agent_contracts import AnalystOutput

ASSUMPTION = {"question": "How is 'm/s.kg' grouped without parentheses?",
              "assumption": "Keep the current reading m/(s*kg).", "affects_behavior": True}


def with_assumption(output, request):
    return {**output, "assumptions": [ASSUMPTION]}


class AssumptionTests(unittest.IsolatedAsyncioTestCase):
    def test_analyst_output_accepts_and_validates_assumptions(self):
        base = {"summary": "s", "requirements": [{"id": "R1", "description": "d"}], "acceptance_criteria": ["a"]}
        self.assertEqual(AnalystOutput.model_validate(base).assumptions, [])
        parsed = AnalystOutput.model_validate({**base, "assumptions": [ASSUMPTION]})
        self.assertEqual(parsed.assumptions[0].assumption, "Keep the current reading m/(s*kg).")
        self.assertTrue(AnalystOutput.model_validate(
            {**base, "assumptions": [{"question": "q", "assumption": "a"}]}).assumptions[0].affects_behavior)
        for broken in ({"question": "", "assumption": "a"}, {"question": "q"}, {"question": "q", "assumption": "a", "x": 1}):
            with self.subTest(broken=broken), self.assertRaises(ValidationError):
                AnalystOutput.model_validate({**base, "assumptions": [broken]})

    async def test_assumptions_reach_the_result_and_do_not_block_a_pass(self):
        agents = FakeAgents(overrides={"Analyst": with_assumption})
        output = await build_chain_graph(ChainNodes(agents)).ainvoke(make_state(), {"recursion_limit": 40})
        self.assertEqual(output["result"]["assumptions"], [ASSUMPTION])
        self.assertEqual(output["result"]["verdict"], "PASS")
        # Downstream roles see it through the spec they already receive.
        reviewer = [request for role, request in agents.calls if role == "Reviewer"][0]
        self.assertEqual(reviewer["artifacts"]["spec"]["assumptions"], [ASSUMPTION])

    async def test_no_assumptions_is_an_empty_list(self):
        output = await build_chain_graph(ChainNodes(FakeAgents())).ainvoke(make_state(), {"recursion_limit": 40})
        self.assertEqual(output["result"]["assumptions"], [])

    def test_benchmark_record_keeps_the_assumptions(self):
        record = RunRecord(run_id="r1", task_id="t", level="L3", base_commit="a" * 40, assumptions=[ASSUMPTION])
        self.assertEqual(RunRecord.model_validate_json(record.model_dump_json()).assumptions, [ASSUMPTION])
        self.assertEqual(RunRecord(run_id="r2", task_id="t", level="L3", base_commit="a" * 40).assumptions, [])
