"""Star topology: the Hub may pick any role whose inputs exist; rules bound the choice and the budget."""

import unittest
from copy import deepcopy

from test_chain_nodes import FakeAgents

from raac.collaboration.levels.l3_collaboration import (
    ChainLimits, ChainNodes, WorkItem, build_chain_graph, initial_chain_state,
)
from raac.collaboration.levels.l3_collaboration.hub_policy import hub_options
from raac.collaboration.topologies import get_topology

CATALOG = {"analyst", "planner", "coder", "tester", "reviewer"}
SKIP = None  # a scripted Hub call that simply takes the rule default


class HubAgents(FakeAgents):
    """FakeAgents plus a scripted Hub. `choices` is consumed one entry per Hub call; None takes the default."""

    def __init__(self, choices=(), **kwargs):
        super().__init__(**kwargs)
        self.choices = list(choices)

    async def __call__(self, role, request):
        if role != "Hub":
            return await super().__call__(role, request)
        self.calls.append((role, deepcopy(request)))
        choice = self.choices.pop(0) if self.choices else SKIP
        if choice is SKIP:
            return {"next": request["artifacts"]["default"], "reason": "default"}
        return choice(request) if callable(choice) else choice


def reject_always(output, request):
    return {**output, "verdict": "REJECT", "blocking_issues": ["Wrong behaviour"]}


def reject_first_review(output, request):
    if request["round"] < 2:
        return {**output, "verdict": "REJECT", "blocking_issues": ["Handle None too"]}
    return output


def uncovered_first(output, request):
    if request["round"] < 2:
        return {**output, "verdict": "REJECT", "covered_ac_ids": [], "blocking_issues": ["AC1 is not planned for"]}
    return output


# Hub calls in a run that goes Analyst -> Planner -> Coder -> Tester: after the Analyst, Planner, Coder, Tester.
BEFORE_TEST_RESULT = [SKIP, SKIP, SKIP]


class StarHubTests(unittest.IsolatedAsyncioTestCase):
    async def run_star(self, agents, *, mode="llm", rounds=3, review_rounds=2, dispatches=12, show_default=True):
        state = initial_chain_state(
            WorkItem(run_id="run-1", task_id="task-1", work_id="task-1", scope="task",
                     requirement="Return zero for empty input", baseline_ref="base-commit"),
            ChainLimits(max_rounds=rounds, hard_max_rounds=4, max_review_rounds=review_rounds,
                        max_dispatches=dispatches, timeout_seconds=10.0, agent_timeout_seconds=2.0),
            "star", mode,
        )
        graph = build_chain_graph(ChainNodes(agents, hub_shows_default=show_default), topology=get_topology("star"))
        return await graph.ainvoke(state, {"recursion_limit": 120})

    @staticmethod
    def decisions(output):
        return [event for event in output["events"] if event["event"] == "hub_decision"]

    @staticmethod
    def roles(agents, *, hub=False):
        return [role for role, _ in agents.calls if hub or role != "Hub"]

    @staticmethod
    def hub_requests(agents):
        return [request for role, request in agents.calls if role == "Hub"]

    async def test_the_llm_hub_does_not_see_the_rule_default(self):
        agents = HubAgents(choices=[{"next": "planner", "reason": "x"}, {"next": "coder", "reason": "x"},
                                    {"next": "tester", "reason": "x"}, {"next": "reviewer", "reason": "x"}])
        output = await self.run_star(agents, show_default=False)
        requests = self.hub_requests(agents)
        self.assertTrue(requests)
        self.assertTrue(all("default" not in request["artifacts"] for request in requests))
        self.assertTrue(all({"allowed", "summary"} <= set(request["artifacts"]) for request in requests))
        # The framework still knows the rule's pick and records it next to the Hub's choice.
        self.assertEqual([d["default"] for d in self.decisions(output) if d["source"] == "llm"],
                         ["planner", "coder", "tester", "reviewer"])
        self.assertEqual(output["verdict"], "PASS")

    async def test_hidden_default_is_still_the_fallback(self):
        agents = HubAgents(pass_on_round=2, choices=[{"next": "planner", "reason": "x"}, {"next": "coder", "reason": "x"},
                                                     {"next": "tester", "reason": "x"}, {"next": "finish", "reason": "x"},
                                                     {"next": "tester", "reason": "x"}, {"next": "reviewer", "reason": "x"}])
        output = await self.run_star(agents, show_default=False)
        fallback = [d for d in self.decisions(output) if d["source"] == "fallback"]
        self.assertEqual([(d["next"], d["default"]) for d in fallback], [("coder", "coder")])
        self.assertEqual(output["verdict"], "PASS")

    async def test_rules_mode_follows_the_usual_sequence_without_the_llm(self):
        agents = HubAgents()
        output = await self.run_star(agents, mode="rules")
        self.assertEqual(self.roles(agents, hub=True), ["Analyst", "Planner", "Coder", "Tester", "Reviewer"])
        self.assertEqual(output["verdict"], "PASS")
        self.assertEqual(output["result"]["topology"], "star")
        decisions = self.decisions(output)
        self.assertEqual([d["next"] for d in decisions], ["analyst", "planner", "coder", "tester", "reviewer", "finish"])
        self.assertEqual({d["source"] for d in decisions}, {"rules"})
        self.assertEqual(output["hub_dispatches"], 5)

    async def test_llm_hub_is_asked_wherever_more_than_one_role_is_available(self):
        agents = HubAgents()
        output = await self.run_star(agents)
        self.assertEqual(self.roles(agents, hub=True), [
            "Analyst", "Hub", "Planner", "Hub", "Coder", "Hub", "Tester", "Hub", "Reviewer"])
        allowed = [request["artifacts"]["allowed"] for request in self.hub_requests(agents)]
        self.assertEqual(allowed, [
            ["analyst", "planner"],
            ["analyst", "planner", "coder"],
            ["analyst", "planner", "coder", "tester"],
            ["analyst", "planner", "coder", "tester", "reviewer"]])
        self.assertEqual([(d["next"], d["source"]) for d in self.decisions(output)], [
            ("analyst", "rules"), ("planner", "llm"), ("coder", "llm"), ("tester", "llm"), ("reviewer", "llm"),
            ("finish", "rules")])
        self.assertEqual(output["verdict"], "PASS")

    async def test_failing_tests_retry_the_coder_by_rules(self):
        agents = HubAgents(pass_on_round=2)
        output = await self.run_star(agents, mode="rules")
        self.assertEqual(self.roles(agents), ["Analyst", "Planner", "Coder", "Tester",
                                              "Coder", "Tester", "Reviewer"])
        self.assertEqual(output["verdict"], "PASS")
        second = [request for role, request in agents.calls if role == "Coder"][1]
        self.assertEqual(second["incoming_message"]["from"], "Tester")

    async def test_hub_can_pick_any_available_role_after_a_test_failure(self):
        agents = HubAgents(pass_on_round=2, choices=BEFORE_TEST_RESULT + [
            {"next": "planner", "reason": "plan misses the None case", "note": "cover None"}])
        output = await self.run_star(agents)
        self.assertEqual(self.roles(agents), ["Analyst", "Planner", "Coder", "Tester", "Planner",
                                              "Coder", "Tester", "Reviewer"])
        request = self.hub_requests(agents)[3]
        self.assertEqual(request["artifacts"]["allowed"], ["analyst", "planner", "coder", "tester", "reviewer"])
        self.assertEqual(request["artifacts"]["default"], "coder")
        self.assertEqual(request["artifacts"]["summary"]["tests"]["failed"], 1)
        self.assertNotIn("requirement", request)  # the Hub never receives the raw chat
        replanned = [r for role, r in agents.calls if role == "Planner"][1]
        self.assertEqual(replanned["incoming_message"]["from"], "Hub")
        self.assertEqual(replanned["artifacts"]["hub_note"], "cover None")
        self.assertEqual(output["verdict"], "PASS")

    async def test_hub_may_send_the_run_back_to_the_analyst_when_the_spec_looks_wrong(self):
        agents = HubAgents(pass_on_round=2, choices=BEFORE_TEST_RESULT + [
            {"next": "analyst", "reason": "tests contradict the spec", "note": "re-read the issue"}])
        output = await self.run_star(agents)
        self.assertEqual(self.roles(agents), ["Analyst", "Planner", "Coder", "Tester", "Analyst",
                                              "Planner", "Coder", "Tester", "Reviewer"])
        again = [request for role, request in agents.calls if role == "Analyst"][1]
        self.assertEqual(again["incoming_message"]["from"], "Hub")
        self.assertEqual(again["artifacts"]["previous_spec"]["summary"], "Handle empty input")
        self.assertEqual(again["artifacts"]["test_report"]["failed"], 1)
        self.assertEqual(again["artifacts"]["hub_note"], "re-read the issue")
        # With a new spec the plan is stale, so the rule default is the Planner and the summary says so.
        after_analyst = self.hub_requests(agents)[4]
        self.assertEqual(after_analyst["artifacts"]["default"], "planner")
        self.assertTrue(after_analyst["artifacts"]["summary"]["spec_changed_since_plan"])
        self.assertNotIn("reviewer", after_analyst["artifacts"]["allowed"])  # its test report was invalidated
        self.assertEqual(output["verdict"], "PASS")
        self.assertFalse(output["spec_changed"] or output["plan_changed"])

    async def test_tester_may_rerun_on_the_same_patch_after_the_planner_changed_the_plan(self):
        calls = {"tester": 0}

        def flaky_tester(output, request):
            calls["tester"] += 1
            passed = calls["tester"] >= 2
            return {**output, "status": "passed" if passed else "failed", "failed": 0 if passed else 1,
                    "blocking_issues": [] if passed else ["Contract not met"]}

        agents = HubAgents(overrides={"Tester": flaky_tester}, choices=BEFORE_TEST_RESULT + [
            {"next": "planner", "reason": "the interface contract is wrong"},
            {"next": "tester", "reason": "only the contract changed; re-check the patch"}])
        output = await self.run_star(agents)
        self.assertEqual(self.roles(agents), ["Analyst", "Planner", "Coder", "Tester", "Planner",
                                              "Tester", "Reviewer"])
        after_replan = self.hub_requests(agents)[4]
        self.assertEqual(after_replan["artifacts"]["allowed"], ["analyst", "planner", "coder", "tester"])
        self.assertEqual(after_replan["artifacts"]["default"], "coder")
        self.assertTrue(after_replan["artifacts"]["summary"]["plan_changed_since_patch"])
        self.assertEqual(self.roles(agents).count("Coder"), 1)
        self.assertEqual(output["verdict"], "PASS")

    async def test_tester_may_be_asked_again_on_an_unchanged_patch(self):
        agents = HubAgents(choices=BEFORE_TEST_RESULT + [{"next": "tester", "reason": "confirm the result"}])
        output = await self.run_star(agents)
        self.assertEqual(self.roles(agents).count("Tester"), 2)
        self.assertEqual(self.roles(agents).count("Coder"), 1)
        self.assertEqual(output["verdict"], "PASS")

    async def test_a_role_without_its_inputs_is_never_offered(self):
        agents = HubAgents(choices=[SKIP, {"next": "reviewer", "reason": "skip ahead"}])
        output = await self.run_star(agents)
        second = self.hub_requests(agents)[1]
        self.assertEqual(second["artifacts"]["allowed"], ["analyst", "planner", "coder"])  # no patch, no test report yet
        fallback = [d for d in self.decisions(output) if d["source"] == "fallback"]
        self.assertEqual([(d["next"], d["default"]) for d in fallback], [("coder", "coder")])
        self.assertIn("not in the allowed set", fallback[0]["reason"])
        self.assertEqual(output["verdict"], "PASS")

    async def test_choice_outside_the_allowed_set_falls_back_to_the_rule(self):
        agents = HubAgents(pass_on_round=2, choices=BEFORE_TEST_RESULT + [{"next": "finish", "reason": "give up"}])
        output = await self.run_star(agents)
        fallback = [d for d in self.decisions(output) if d["source"] == "fallback"]
        self.assertEqual([(d["next"], d["default"]) for d in fallback], [("coder", "coder")])  # no review yet: cannot finish
        self.assertEqual(output["verdict"], "PASS")

    async def test_unparseable_hub_output_falls_back_without_aborting(self):
        agents = HubAgents(pass_on_round=2, choices=BEFORE_TEST_RESULT + [{"unexpected": "shape"}])
        output = await self.run_star(agents)
        fallback = [d for d in self.decisions(output) if d["source"] == "fallback"]
        self.assertEqual(len(fallback), 1)
        self.assertIn("invalid_output", fallback[0]["reason"])
        self.assertEqual(output["verdict"], "PASS")
        self.assertIsNone(output["error"])

    async def test_review_rejection_can_go_back_to_coder_with_feedback(self):
        agents = HubAgents(overrides={"Reviewer": reject_first_review},
                           choices=BEFORE_TEST_RESULT + [SKIP, {"next": "coder", "reason": "fix the None case"}])
        output = await self.run_star(agents)
        self.assertEqual(self.roles(agents), ["Analyst", "Planner", "Coder", "Tester", "Reviewer",
                                              "Coder", "Tester", "Reviewer"])
        second = [request for role, request in agents.calls if role == "Coder"][1]
        self.assertEqual(second["incoming_message"]["from"], "Reviewer")
        self.assertEqual(second["artifacts"]["review_report"]["verdict"], "REJECT")
        self.assertEqual(output["review_rounds"], 1)
        self.assertEqual(output["verdict"], "PASS")

    async def test_llm_hub_may_finish_after_a_rejection(self):
        agents = HubAgents(overrides={"Reviewer": reject_always},
                           choices=BEFORE_TEST_RESULT + [SKIP, {"next": "finish", "reason": "out of scope"}])
        output = await self.run_star(agents)
        after_review = self.hub_requests(agents)[4]
        self.assertEqual(after_review["artifacts"]["allowed"], ["analyst", "planner", "coder", "tester", "reviewer", "finish"])
        self.assertEqual(output["verdict"], "REJECT")
        self.assertEqual(output["stop_reason"], "reviewer_rejected")
        self.assertEqual(self.roles(agents).count("Coder"), 1)

    async def test_uncovered_acceptance_criteria_default_to_the_planner(self):
        agents = HubAgents(overrides={"Reviewer": uncovered_first})
        output = await self.run_star(agents, mode="rules")
        self.assertEqual(self.roles(agents), ["Analyst", "Planner", "Coder", "Tester", "Reviewer",
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

    async def test_review_before_the_rounds_run_out_does_not_claim_they_did(self):
        agents = HubAgents(pass_on_round=100, choices=BEFORE_TEST_RESULT + [{"next": "reviewer", "reason": "get a verdict"}])
        output = await self.run_star(agents)
        reviews = [event for event in output["events"] if event["event"] == "review_evaluated"]
        self.assertEqual(reviews[0]["reason"], "reviewer_rejected")  # asked early: rounds were still left
        self.assertEqual(reviews[-1]["reason"], "max_rounds_reached")  # the final review after the last round
        self.assertEqual(self.roles(agents).count("Coder"), 3)

    async def test_coder_is_withdrawn_once_its_rounds_are_used(self):
        agents = HubAgents(pass_on_round=100)
        await self.run_star(agents, rounds=1)
        last = self.hub_requests(agents)[-1]
        self.assertNotIn("coder", last["artifacts"]["allowed"])

    async def test_dispatch_cap_forces_the_run_to_finish(self):
        agents = HubAgents()
        output = await self.run_star(agents, mode="rules", dispatches=3)
        self.assertEqual(self.roles(agents), ["Analyst", "Planner", "Coder"])
        self.assertEqual(output["stop_reason"], "max_dispatches_reached")
        self.assertEqual(output["result"]["status"], "rejected")
        last = self.decisions(output)[-1]
        self.assertEqual((last["source"], last["next"]), ("forced", "finish"))

    async def test_endless_choices_are_cut_off_by_the_dispatch_cap(self):
        agents = HubAgents(choices=[SKIP, SKIP] + [{"next": "analyst", "reason": "again"}] * 30)
        output = await self.run_star(agents, dispatches=6)
        self.assertLessEqual(output["hub_dispatches"], 6)
        self.assertEqual(output["stop_reason"], "max_dispatches_reached")

    async def test_hub_only_dispatches_roles_in_the_catalog(self):
        agents = HubAgents(pass_on_round=2, overrides={"Reviewer": reject_always},
                           choices=BEFORE_TEST_RESULT + [{"next": "planner", "reason": "x"}])
        output = await self.run_star(agents)
        self.assertLessEqual({d["next"] for d in self.decisions(output)} - {"finish"}, CATALOG)
        self.assertLessEqual(set(self.roles(agents)), {role.capitalize() for role in CATALOG} | {"Environment"})  # Environment runs once, before the Hub


class HubPolicyTests(unittest.TestCase):
    @staticmethod
    def state(**changes):
        base = {"limits": {"max_rounds": 3, "max_review_rounds": 2, "max_dispatches": 12},
                "rounds_completed": 0, "round_number": 0, "review_rounds": 0, "spec": None, "plan": None,
                "patch": None, "plan_changed": False, "spec_changed": False, "test_report": None,
                "review_report": None, "verdict": None, "ac_catalog": {"AC1": "a", "AC2": "b"}}
        return {**base, **changes}

    def test_each_role_needs_its_inputs(self):
        self.assertEqual(hub_options(self.state(), False), (["analyst"], "analyst"))
        self.assertEqual(hub_options(self.state(spec={}), False), (["analyst", "planner"], "planner"))
        self.assertEqual(hub_options(self.state(spec={}, plan={}), False),
                         (["analyst", "planner", "coder"], "coder"))
        patched = self.state(spec={}, plan={}, patch="p", round_number=1)
        self.assertEqual(hub_options(patched, False), (["analyst", "planner", "coder", "tester"], "tester"))
        tested = {**patched, "test_report": {"x": 1}, "rounds_completed": 1}
        self.assertEqual(hub_options(tested, True), (["analyst", "planner", "coder", "tester", "reviewer"], "reviewer"))

    def test_a_new_spec_or_plan_changes_the_default_but_not_the_choice(self):
        base = self.state(spec={}, plan={}, patch="p", round_number=1)
        self.assertEqual(hub_options({**base, "plan_changed": True}, False),
                         (["analyst", "planner", "coder", "tester"], "coder"))
        self.assertEqual(hub_options({**base, "spec_changed": True}, False),
                         (["analyst", "planner", "coder", "tester"], "planner"))

    def test_failed_tests_keep_every_role_open_except_finish(self):
        failed = self.state(spec={}, plan={}, patch="p", test_report={"x": 1}, round_number=1, rounds_completed=1)
        self.assertEqual(hub_options(failed, False),
                         (["analyst", "planner", "coder", "tester", "reviewer"], "coder"))
        reviewed = {**failed, "review_report": {"covered_ac_ids": ["AC1", "AC2"]}}
        self.assertEqual(hub_options(reviewed, False)[0], ["analyst", "planner", "coder", "tester", "reviewer", "finish"])

    def test_nothing_but_a_final_review_remains_when_the_coder_has_no_rounds(self):
        exhausted = self.state(spec={}, plan={}, patch="p", test_report={"x": 1}, round_number=3, rounds_completed=3)
        self.assertEqual(hub_options(exhausted, False), (["reviewer"], "reviewer"))
        reviewed = {**exhausted, "review_report": {"covered_ac_ids": ["AC1", "AC2"]}}
        self.assertEqual(hub_options(reviewed, False), (["finish"], "finish"))

    def test_a_rejection_opens_every_role_while_review_rounds_remain(self):
        rejected = self.state(spec={}, plan={}, patch="p", test_report={"x": 1}, round_number=1, rounds_completed=1,
                              review_report={"covered_ac_ids": ["AC1", "AC2"]}, verdict="REJECT")
        everything = ["analyst", "planner", "coder", "tester", "reviewer", "finish"]
        self.assertEqual(hub_options(rejected, True), (everything, "coder"))
        partial = {**rejected, "review_report": {"covered_ac_ids": ["AC1"]}}
        self.assertEqual(hub_options(partial, True)[1], "planner")
        self.assertEqual(hub_options({**rejected, "review_rounds": 2}, True), (["finish"], "finish"))
        self.assertEqual(hub_options({**rejected, "round_number": 3}, True), (["finish"], "finish"))

    def test_an_accepted_run_can_only_finish(self):
        accepted = self.state(spec={}, plan={}, patch="p", test_report={"x": 1}, round_number=1, rounds_completed=1,
                              review_report={"covered_ac_ids": ["AC1", "AC2"]}, verdict="PASS")
        self.assertEqual(hub_options(accepted, True), (["finish"], "finish"))


class ReTestAfterReviewTests(unittest.IsolatedAsyncioTestCase):
    """A rejection that only asks for more test evidence goes back to the Tester, then to a fresh review."""

    async def test_retest_gets_the_review_and_goes_back_to_the_reviewer(self):
        reviews = {"n": 0}

        def evidence_first(output, request):
            reviews["n"] += 1
            if reviews["n"] == 1:
                return {**output, "verdict": "REJECT", "blocking_issues": ["Run the full regression suite on this patch"]}
            return output

        agents = HubAgents(overrides={"Reviewer": evidence_first},
                           choices=BEFORE_TEST_RESULT + [SKIP, {"next": "tester", "reason": "only evidence is missing",
                                                                "note": "run the full suite"}])
        state = initial_chain_state(
            WorkItem(run_id="run-1", task_id="task-1", work_id="task-1", scope="task",
                     requirement="Return zero for empty input", baseline_ref="base-commit"),
            ChainLimits(max_rounds=3, hard_max_rounds=4, max_review_rounds=2, max_dispatches=12,
                        timeout_seconds=10.0, agent_timeout_seconds=2.0), "star", "llm")
        graph = build_chain_graph(ChainNodes(agents, hub_shows_default=True), topology=get_topology("star"))
        output = await graph.ainvoke(state, {"recursion_limit": 120})
        roles = [role for role, _ in agents.calls if role != "Hub"]
        self.assertEqual(roles, ["Analyst", "Planner", "Coder", "Tester", "Reviewer", "Tester", "Reviewer"])
        retest = [request for role, request in agents.calls if role == "Tester"][1]
        self.assertEqual(retest["incoming_message"]["from"], "Hub")
        self.assertEqual(retest["artifacts"]["review_report"]["verdict"], "REJECT")
        self.assertEqual(retest["artifacts"]["hub_note"], "run the full suite")
        # After the re-test the old verdict no longer stands: the rule default is a fresh review.
        after_retest = [request for role, request in agents.calls if role == "Hub"][-1]
        self.assertEqual(after_retest["artifacts"]["default"], "reviewer")
        self.assertIsNone(after_retest["artifacts"]["summary"]["review"])
        self.assertEqual(output["review_rounds"], 1)
        self.assertEqual(output["verdict"], "PASS")

    async def test_endless_retests_are_bounded_by_review_rounds(self):
        agents = HubAgents(overrides={"Reviewer": reject_always},
                           choices=BEFORE_TEST_RESULT + [SKIP] + [{"next": "tester", "reason": "again"}, SKIP] * 6)
        output = await StarHubTests.run_star(self, agents, review_rounds=2)
        self.assertLessEqual([role for role, _ in agents.calls].count("Tester"), 3)
        self.assertEqual(output["review_rounds"], 2)
        self.assertEqual(output["verdict"], "REJECT")
