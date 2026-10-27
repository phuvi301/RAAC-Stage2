"""Star topology: the Hub routes every role. Rules bound the choice; an LLM Hub only picks among allowed roles."""

import unittest
from copy import deepcopy

from test_chain_nodes import FakeAgents

from raac.collaboration.levels.l3_collaboration import (
    ChainLimits, ChainNodes, WorkItem, build_chain_graph, initial_chain_state,
)
from raac.collaboration.levels.l3_collaboration.hub_policy import hub_options
from raac.collaboration.topologies import get_topology

CATALOG = {"analyst", "planner", "coder", "tester", "reviewer"}


class HubAgents(FakeAgents):
    """FakeAgents plus a scripted Hub; `choices` is consumed one entry per Hub call."""

    def __init__(self, choices=(), **kwargs):
        super().__init__(**kwargs)
        self.choices = list(choices)

    async def __call__(self, role, request):
        if role != "Hub":
            return await super().__call__(role, request)
        self.calls.append((role, deepcopy(request)))
        choice = self.choices.pop(0) if self.choices else {"next": request["artifacts"]["default"], "reason": "default"}
        return choice(request) if callable(choice) else choice


def reject_first(output, request):
    rejected = request["round"] < 2 and not request["artifacts"].get("rejected_once")
    return {**output, "verdict": "REJECT" if rejected else "PASS",
            "blocking_issues": ["Handle None too"] if rejected else []}


def reject_always(output, request):
    return {**output, "verdict": "REJECT", "blocking_issues": ["Wrong behaviour"]}


def uncovered_first(output, request):
    if request["round"] < 2:
        return {**output, "verdict": "REJECT", "covered_ac_ids": [], "blocking_issues": ["AC1 is not planned for"]}
    return output


class StarHubTests(unittest.IsolatedAsyncioTestCase):
    async def run_star(self, agents, *, mode="llm", rounds=3, review_rounds=2, dispatches=12):
        state = initial_chain_state(
            WorkItem(run_id="run-1", task_id="task-1", work_id="task-1", scope="task",
                     requirement="Return zero for empty input", baseline_ref="base-commit"),
            ChainLimits(max_rounds=rounds, hard_max_rounds=4, max_review_rounds=review_rounds,
                        max_dispatches=dispatches, timeout_seconds=10.0, agent_timeout_seconds=2.0),
            "star", mode,
        )
        graph = build_chain_graph(ChainNodes(agents), topology=get_topology("star"))
        return await graph.ainvoke(state, {"recursion_limit": 80})

    @staticmethod
    def decisions(output):
        return [event for event in output["events"] if event["event"] == "hub_decision"]

    @staticmethod
    def roles(agents):
        return [role for role, _ in agents.calls]

    async def test_straight_path_never_needs_the_llm(self):
        for mode in ("rules", "llm"):
            with self.subTest(mode=mode):
                agents = HubAgents()
                output = await self.run_star(agents, mode=mode)
                self.assertEqual(self.roles(agents), ["Environment", "Analyst", "Planner", "Coder", "Tester", "Reviewer"])
                self.assertEqual(output["verdict"], "PASS")
                self.assertEqual(output["result"]["topology"], "star")
                decisions = self.decisions(output)
                self.assertEqual([d["next"] for d in decisions],
                                 ["analyst", "planner", "coder", "tester", "reviewer", "finish"])
                self.assertEqual({d["source"] for d in decisions}, {"rules"})
                self.assertEqual(output["hub_dispatches"], 5)

    async def test_failing_tests_retry_the_coder_by_rules(self):
        agents = HubAgents(pass_on_round=2)
        output = await self.run_star(agents, mode="rules")
        self.assertEqual(self.roles(agents), ["Environment", "Analyst", "Planner", "Coder", "Tester",
                                              "Coder", "Tester", "Reviewer"])
        self.assertEqual(output["verdict"], "PASS")
        second = [request for role, request in agents.calls if role == "Coder"][1]
        self.assertEqual(second["incoming_message"]["from"], "Tester")

    async def test_llm_hub_chooses_among_allowed_roles(self):
        agents = HubAgents(pass_on_round=2, choices=[{"next": "planner", "reason": "plan misses the None case", "note": "cover None"}])
        output = await self.run_star(agents)
        self.assertEqual(self.roles(agents), ["Environment", "Analyst", "Planner", "Coder", "Tester", "Hub",
                                              "Planner", "Coder", "Tester", "Reviewer"])
        hub_request = [request for role, request in agents.calls if role == "Hub"][0]
        self.assertEqual(hub_request["artifacts"]["allowed"], ["coder", "planner"])
        self.assertEqual(hub_request["artifacts"]["default"], "coder")
        self.assertEqual(hub_request["artifacts"]["summary"]["tests"]["failed"], 1)
        self.assertNotIn("requirement", hub_request)  # the Hub never receives the raw chat
        replanned = [request for role, request in agents.calls if role == "Planner"][1]
        self.assertEqual(replanned["incoming_message"]["from"], "Hub")
        self.assertEqual(replanned["artifacts"]["hub_note"], "cover None")
        sources = [(d["next"], d["source"]) for d in self.decisions(output) if d["next"] == "planner"]
        self.assertEqual(sources[-1], ("planner", "llm"))
        self.assertEqual(output["verdict"], "PASS")

    async def test_choice_outside_the_allowed_set_falls_back_to_the_rule(self):
        agents = HubAgents(pass_on_round=2, choices=[{"next": "reviewer", "reason": "skip the retry"}])
        output = await self.run_star(agents)
        fallback = [d for d in self.decisions(output) if d["source"] == "fallback"]
        self.assertEqual([(d["next"], d["default"]) for d in fallback], [("coder", "coder")])
        self.assertIn("not in the allowed set", fallback[0]["reason"])
        self.assertEqual(self.roles(agents).count("Reviewer"), 1)
        self.assertEqual(output["verdict"], "PASS")

    async def test_unparseable_hub_output_falls_back_without_aborting(self):
        agents = HubAgents(pass_on_round=2, choices=[{"unexpected": "shape"}])
        output = await self.run_star(agents)
        fallback = [d for d in self.decisions(output) if d["source"] == "fallback"]
        self.assertEqual(len(fallback), 1)
        self.assertIn("invalid_output", fallback[0]["reason"])
        self.assertEqual(output["verdict"], "PASS")
        self.assertIsNone(output["error"])

    async def test_review_rejection_can_go_back_to_coder_with_feedback(self):
        agents = HubAgents(choices=[{"next": "coder", "reason": "fix the None case"}],
                           overrides={"Reviewer": lambda output, request: reject_first(output, {**request, "artifacts": {}})
                                      if request["round"] < 2 else output})
        output = await self.run_star(agents)
        self.assertEqual(self.roles(agents), ["Environment", "Analyst", "Planner", "Coder", "Tester", "Reviewer",
                                              "Hub", "Coder", "Tester", "Reviewer"])
        second = [request for role, request in agents.calls if role == "Coder"][1]
        self.assertEqual(second["incoming_message"]["from"], "Reviewer")
        self.assertEqual(second["artifacts"]["review_report"]["verdict"], "REJECT")
        self.assertEqual(output["review_rounds"], 1)
        self.assertEqual(output["verdict"], "PASS")

    async def test_llm_hub_may_finish_after_a_rejection(self):
        agents = HubAgents(choices=[{"next": "finish", "reason": "reviewer concerns are out of scope"}],
                           overrides={"Reviewer": reject_always})
        output = await self.run_star(agents)
        self.assertEqual(output["verdict"], "REJECT")
        self.assertEqual(output["stop_reason"], "reviewer_rejected")
        self.assertEqual(self.roles(agents).count("Coder"), 1)

    async def test_uncovered_acceptance_criteria_default_to_the_planner(self):
        agents = HubAgents(overrides={"Reviewer": uncovered_first})
        output = await self.run_star(agents, mode="rules")
        self.assertEqual(self.roles(agents), ["Environment", "Analyst", "Planner", "Coder", "Tester", "Reviewer",
                                              "Planner", "Coder", "Tester", "Reviewer"])
        self.assertEqual(output["verdict"], "PASS")
        self.assertFalse(output["plan_changed"])

    async def test_review_feedback_rounds_are_bounded_in_star_too(self):
        agents = HubAgents(overrides={"Reviewer": reject_always})
        output = await self.run_star(agents, mode="rules", review_rounds=1)
        roles = self.roles(agents)
        self.assertEqual((roles.count("Coder"), roles.count("Reviewer")), (2, 2))
        self.assertEqual(output["review_rounds"], 1)
        self.assertEqual(output["verdict"], "REJECT")

    async def test_exhausted_rounds_get_a_final_review_then_stop(self):
        agents = HubAgents(pass_on_round=100)
        output = await self.run_star(agents, mode="rules", rounds=2)
        roles = self.roles(agents)
        self.assertEqual((roles.count("Coder"), roles.count("Tester"), roles.count("Reviewer")), (2, 2, 1))
        self.assertEqual(output["verdict"], "REJECT")
        self.assertEqual(output["stop_reason"], "max_rounds_reached")

    async def test_dispatch_cap_forces_the_run_to_finish(self):
        agents = HubAgents()
        output = await self.run_star(agents, mode="rules", dispatches=3)
        self.assertEqual(self.roles(agents), ["Environment", "Analyst", "Planner", "Coder"])
        self.assertEqual(output["stop_reason"], "max_dispatches_reached")
        self.assertEqual(output["result"]["status"], "rejected")
        last = self.decisions(output)[-1]
        self.assertEqual((last["source"], last["next"]), ("forced", "finish"))

    async def test_hub_only_dispatches_roles_in_the_catalog(self):
        agents = HubAgents(pass_on_round=2, overrides={"Reviewer": reject_always},
                           choices=[{"next": "planner", "reason": "x"}, {"next": "coder", "reason": "y"}])
        output = await self.run_star(agents)
        self.assertLessEqual({d["next"] for d in self.decisions(output)} - {"finish"}, CATALOG)
        self.assertLessEqual(set(self.roles(agents)) - {"Environment", "Hub"}, {role.capitalize() for role in CATALOG})


class HubPolicyTests(unittest.TestCase):
    @staticmethod
    def state(**changes):
        base = {"limits": {"max_rounds": 3, "max_review_rounds": 2, "max_dispatches": 12},
                "rounds_completed": 0, "review_rounds": 0, "spec": None, "plan": None, "patch": None,
                "plan_changed": False, "test_report": None, "review_report": None, "verdict": None,
                "ac_catalog": {"AC1": "a", "AC2": "b"}}
        return {**base, **changes}

    def test_prerequisites_force_a_single_option(self):
        self.assertEqual(hub_options(self.state(), False), (["analyst"], "analyst"))
        self.assertEqual(hub_options(self.state(spec={}), False), (["planner"], "planner"))
        self.assertEqual(hub_options(self.state(spec={}, plan={}), False), (["coder"], "coder"))
        self.assertEqual(hub_options(self.state(spec={}, plan={}, patch="p"), False), (["tester"], "tester"))
        self.assertEqual(hub_options(self.state(spec={}, plan={}, patch="p", plan_changed=True), False), (["coder"], "coder"))

    def test_choices_open_only_after_a_result_exists(self):
        failed = self.state(spec={}, plan={}, patch="p", test_report={"x": 1}, rounds_completed=1)
        self.assertEqual(hub_options(failed, False), (["coder", "planner"], "coder"))
        exhausted = {**failed, "rounds_completed": 3}
        self.assertEqual(hub_options(exhausted, False), (["reviewer"], "reviewer"))
        rejected = {**failed, "review_report": {"covered_ac_ids": ["AC1", "AC2"]}, "verdict": "REJECT"}
        self.assertEqual(hub_options(rejected, True), (["coder", "planner", "finish"], "coder"))
        partial = {**rejected, "review_report": {"covered_ac_ids": ["AC1"]}}
        self.assertEqual(hub_options(partial, True)[1], "planner")
        self.assertEqual(hub_options({**rejected, "review_rounds": 2}, True), (["finish"], "finish"))
        self.assertEqual(hub_options({**rejected, "verdict": "PASS"}, True), (["finish"], "finish"))
