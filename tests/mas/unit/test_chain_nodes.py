"""Run real LangGraph nodes with fake agents; no API or sandbox is used."""

import asyncio
import json
from copy import deepcopy
import time
import unittest

from langgraph.checkpoint.memory import InMemorySaver
from pydantic import ValidationError

from raac.collaboration.levels.l3_collaboration import (
    ChainLimits, ChainNodes, WorkItem, build_chain_graph, initial_chain_state,
)
from raac.collaboration.levels.l3_collaboration.chain_state import RoleHandoff, role_output_model
from raac.collaboration.topologies import get_topology


def commit_payload(data):
    """Serialize existing scenario fixtures using the commit's wire fields."""
    if not isinstance(data, dict):
        return data
    if "acceptance_criteria" in data:
        return {**data, "requirements": [{"id": "R1", "description": data["summary"]}],
                "acceptance_criteria": [item["description"] if isinstance(item, dict) else item
                                        for item in data["acceptance_criteria"]], "open_questions": []}
    if "steps" in data:
        return {**data, "steps": [{"id": int(str(step["id"]).removeprefix("S")),
                "description": step["approach"], "files": [step["target"].split("::")[0]],
                "depends_on": [], "ac_ids": step["ac_ids"]} for step in data["steps"]]}
    if "tests_run" in data:
        names = data["test_cases"] or ["<no tests executed>"]
        status = data["status"]
        results = [{"test": name, "status": ("error" if status == "error" else
                    "failed" if index < data["failed"] else "passed" if data["test_cases"] else "skipped"),
                    "details": data["summary"]} for index, name in enumerate(names)]
        return {"tests_to_run": names, "results": results,
                "verdict": "fail" if any(item["status"] in {"failed", "error"} for item in results) else "pass",
                "covered_ac_ids": data["covered_ac_ids"], "blocking_issues": data["blocking_issues"]}
    if "verdict" in data:
        rejected = data["verdict"] == "REJECT"
        comments = [{"severity": "blocker", "message": issue} for issue in data["blocking_issues"]]
        if rejected and not comments:
            comments = [{"severity": "major", "message": "Fixture review rejects this change"}]
        return {"decision": "reject" if rejected else "approve", "summary": data["summary"],
                "comments": comments, "covered_ac_ids": data["covered_ac_ids"]}
    if "summary" in data:
        return data.get("patch", "")
    return data


class FakeAgents:
    def __init__(self, *, pass_on_round=1, verdict="PASS", overrides=None):
        self.pass_on_round = pass_on_round
        self.verdict = verdict
        self.overrides = overrides or {}
        self.calls = []

    async def __call__(self, role, request):
        self.calls.append((role, deepcopy(request)))
        if role == "Analyst":
            output = {
                "summary": "Handle empty input", "constraints": [],
                "acceptance_criteria": [{"id": "AC1", "description": "Empty input returns zero"}],
            }
        elif role == "Planner":
            output = {
                "steps": [{"id": "S1", "ac_ids": ["AC1"], "target": "current operation",
                           "approach": "Add an empty input guard; keep existing behavior"}],
                "covered_ac_ids": ["AC1"],
            }
        elif role == "Environment":
            output = {"status": "ready", "summary": "Fake verified environment", "blocking_issues": []}
        elif role == "Coder":
            output = {
                "patch": f"cumulative patch for attempt {request['round']}",
                "summary": "Add an empty input guard",
            }
        elif role == "Tester":
            passed = request["round"] >= self.pass_on_round
            output = {
                "patch_hash": request["artifacts"]["patch_hash"],
                "status": "passed" if passed else "failed",
                "tests_run": 2, "failed": 0 if passed else 1,
                "test_cases": ["empty input", "non-empty input"],
                "covered_ac_ids": ["AC1"],
                "blocking_issues": [] if passed else ["Empty input raises"],
                "summary": "Structured fake report, not a real test run",
            }
        else:
            output = {
                "patch_hash": request["artifacts"]["patch_hash"],
                "verdict": self.verdict, "covered_ac_ids": ["AC1"],
                "blocking_issues": [], "summary": "Fake review",
            }
        override = self.overrides.get(role)
        output = override(output, request) if override else output
        if not isinstance(output, dict):
            return output
        if role == "Coder":
            patch = f"--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n-old\n+attempt_{request['round']}\n"
            return RoleHandoff(patch, {"summary": output["summary"]})
        if role == "Environment":
            return RoleHandoff(output, {"verified": True, "configuration": {"cwd": "."}})
        payload = commit_payload(output)
        if role in {"Tester", "Reviewer"}:
            runtime = {key: output[key] for key in ("patch_hash", "status", "tests_run", "failed", "test_cases", "summary") if key in output}
            return RoleHandoff(payload, runtime)
        return payload


def make_state(*, run_id="run-1", work_id="task-1", scope="task", rounds=3):
    return initial_chain_state(
        WorkItem(
            run_id=run_id, task_id="task-1", work_id=work_id, scope=scope,
            requirement="Return zero for empty input", baseline_ref="base-commit",
        ),
        ChainLimits(
            max_rounds=rounds, hard_max_rounds=4,
            timeout_seconds=10.0, agent_timeout_seconds=2.0,
        ),
    )


class ChainNodeTests(unittest.IsolatedAsyncioTestCase):
    async def run_graph(self, agents, state=None, **node_options):
        state = state or make_state()
        graph = build_chain_graph(ChainNodes(agents, **node_options))
        return await graph.ainvoke(
            state, {"recursion_limit": 6 * state["limits"]["max_rounds"] + 16},
        )

    async def test_all_nodes_advertise_and_parse_the_shared_json_contract(self):
        class JsonAgents(FakeAgents):
            async def __call__(self, role, request):
                result = await super().__call__(role, request)
                if isinstance(result, RoleHandoff):
                    wire = result.output if role == "Coder" else json.dumps(result.output)
                    return RoleHandoff(wire, result.runtime)
                return json.dumps(result)

        agents = JsonAgents()
        output = await self.run_graph(agents)
        self.assertEqual(output["result"]["verdict"], "PASS")
        self.assertEqual(len(agents.calls), 6)
        for role, request in agents.calls:
            self.assertEqual(request["output_schema"], role_output_model(role).model_json_schema())

    async def test_role_visibility_is_selected_by_chain_before_the_runner(self):
        class ObservedChanges(FakeAgents):
            async def __call__(self, role, request):
                output = await super().__call__(role, request)
                if role == "Coder":
                    patch = "--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n-old\n+" + "x" * 2100 + str(request["round"]) + "\n"
                    return RoleHandoff(patch, {**output.runtime,
                        "repository_changes": [{"path": "app.py", "deleted": False}]})
                return output

        agents = ObservedChanges(pass_on_round=2)
        state = make_state()
        original = deepcopy(state)
        output = await self.run_graph(agents, state)
        self.assertEqual(output["result"]["verdict"], "PASS")
        self.assertEqual([role for role, _ in agents.calls], [
            "Environment", "Analyst", "Planner", "Coder", "Tester", "Coder", "Tester", "Reviewer"])
        for role, request in agents.calls:
            with self.subTest(role=role, round=request["round"]):
                if role in {"Environment", "Analyst", "Reviewer"}:
                    self.assertEqual(request["requirement"], original["work"]["requirement"])
                else:
                    self.assertNotIn("requirement", request)
                if role in {"Coder", "Tester", "Reviewer"}:
                    self.assertEqual(request["artifacts"]["spec"], output["spec"])
                    self.assertEqual(request["artifacts"]["ac_catalog"], output["ac_catalog"])
                if role == "Tester":
                    self.assertEqual(request["artifacts"]["plan"], {"interface_contracts": {}})
                    self.assertNotIn("patch", request["artifacts"])
                    self.assertEqual(request["incoming_message"]["patch_ref"], "artifacts.patch_context")
                    self.assertEqual(request["repository_changes"], [{"path": "app.py", "deleted": False}])
                elif role == "Reviewer":
                    self.assertEqual(request["artifacts"]["patch"], output["patch"])
                    self.assertIn("decisions", request["artifacts"]["plan"])
                elif role == "Coder" and request["round"] == 2:
                    self.assertIn("previous_patch_context", request["artifacts"])
                    self.assertEqual(request["artifacts"]["test_report"]["failed"], 1)
        self.assertIn("steps", output["plan"])
        self.assertIn("requirement", output["work"])
        self.assertEqual(state, original)
        self.assertEqual(output["result"]["repository_changes"], [{"path": "app.py", "deleted": False}])

    async def test_inherited_ac_ids_survive_string_ac_format(self):
        state = make_state()
        state["work"]["acceptance_criteria"] = [{"id": "ROOT9", "description": "Empty input returns zero"}]
        nodes = ChainNodes(FakeAgents())
        update = await nodes.analyst(state)
        self.assertEqual(update["ac_catalog"], {"ROOT9": "Empty input returns zero"})
        self.assertEqual(update["spec"]["acceptance_criteria"], ["Empty input returns zero"])
        self.assertTrue(update["spec"]["requirements"])

    async def test_missing_plan_coverage_is_not_invented(self):
        agents = FakeAgents(overrides={"Planner": lambda output, request: {**output, "covered_ac_ids": []}})
        output = await self.run_graph(agents)
        self.assertEqual(output["stop_reason"], "incomplete_plan")
        self.assertNotIn("Coder", [role for role, _ in agents.calls])

    async def test_pass_first_attempt_runs_all_roles_once(self):
        agents = FakeAgents()
        output = await self.run_graph(agents)
        self.assertEqual([role for role, _ in agents.calls], [
            "Environment", "Analyst", "Planner", "Coder", "Tester", "Reviewer",
        ])
        self.assertEqual(output["result"]["verdict"], "PASS")
        self.assertEqual(output["rounds_completed"], 1)
        self.assertEqual(output["stop_reason"], "tests_and_review_passed")
        self.assertEqual(output["events"][-1]["event"], "chain_stopped")

    async def test_adapter_error_code_is_recorded_without_exception_body(self):
        from raac.common.errors import AgentExecutionError

        def fail_coder(output, request):
            raise AgentExecutionError("invalid_coder_patch")

        output = await self.run_graph(FakeAgents(overrides={"Coder": fail_coder}))
        self.assertEqual(output["stop_reason"], "invalid_coder_patch")
        self.assertEqual(output["error"], {"node": "coder", "kind": "invalid_coder_patch"})

    async def test_retry_only_coder_tester_and_deliver_feedback(self):
        agents = FakeAgents(pass_on_round=2)
        output = await self.run_graph(agents)
        self.assertEqual([role for role, _ in agents.calls], [
            "Environment", "Analyst", "Planner", "Coder", "Tester", "Coder", "Tester", "Reviewer",
        ])
        coders = [request for role, request in agents.calls if role == "Coder"]
        self.assertEqual(coders[0]["incoming_message"]["from"], "Planner")
        self.assertEqual(coders[1]["incoming_message"]["from"], "Tester")
        self.assertEqual(coders[1]["artifacts"]["test_report"]["failed"], 1)
        for role, request in agents.calls:
            if role in {"Analyst", "Planner", "Coder", "Tester"}:
                self.assertEqual(request["environment_report"], output["environment_report"])
            if role == "Environment":
                self.assertIsNone(request["environment_report"])
        self.assertEqual([role for role, _ in agents.calls].count("Environment"), 1)
        self.assertEqual(output["rounds_completed"], 2)
        self.assertEqual(output["result"]["verdict"], "PASS")

    async def test_always_fail_stops_at_cap_and_overrules_false_review_pass(self):
        agents = FakeAgents(pass_on_round=100)
        output = await self.run_graph(agents, make_state(rounds=3))
        roles = [role for role, _ in agents.calls]
        self.assertEqual(roles.count("Environment"), 1)
        self.assertEqual(roles.count("Coder"), 3)
        self.assertEqual(roles.count("Tester"), 3)
        self.assertEqual(roles.count("Reviewer"), 1)
        self.assertEqual(output["review_report"]["verdict"], "PASS")
        self.assertEqual(output["verdict"], "REJECT")
        self.assertEqual(output["stop_reason"], "max_rounds_reached")

    async def test_review_rejection_does_not_restart_coder(self):
        output = await self.run_graph(FakeAgents(verdict="REJECT"))
        self.assertEqual(output["rounds_completed"], 1)
        self.assertEqual(output["verdict"], "REJECT")
        self.assertEqual(output["stop_reason"], "reviewer_rejected")

    async def test_zero_tests_missing_coverage_or_blockers_cannot_pass(self):
        reports = (
            {"tests_run": 0, "test_cases": []},
            {"covered_ac_ids": []},
            {"blocking_issues": ["Unresolved requirement"]},
        )
        for changes in reports:
            with self.subTest(changes=changes):
                agents = FakeAgents(overrides={
                    "Tester": lambda output, request: {**output, **changes},
                })
                output = await self.run_graph(agents, make_state(rounds=1))
                self.assertEqual(output["verdict"], "REJECT")
                self.assertFalse(output["tests_passed"])

    async def test_old_patch_test_or_review_is_rejected(self):
        for role, reason in (("Tester", "stale_test_report"), ("Reviewer", "stale_review_report")):
            with self.subTest(role=role):
                agents = FakeAgents(overrides={
                    role: lambda output, request: {**output, "patch_hash": "old"},
                })
                output = await self.run_graph(agents)
                self.assertEqual(output["result"]["status"], "aborted")
                self.assertEqual(output["stop_reason"], reason)

    async def test_invalid_json_stops_without_running_downstream_roles(self):
        agents = FakeAgents(overrides={"Planner": lambda output, request: "not JSON"})
        output = await self.run_graph(agents)
        self.assertEqual(output["stop_reason"], "invalid_output")
        self.assertEqual([role for role, _ in agents.calls], ["Environment", "Analyst", "Planner"])

    async def test_plan_cannot_drop_acceptance_criteria(self):
        agents = FakeAgents(overrides={
            "Planner": lambda output, request: {**output, "covered_ac_ids": ["AC-other"]},
        })
        output = await self.run_graph(agents)
        self.assertEqual(output["stop_reason"], "incomplete_plan")

    async def test_plan_steps_must_cover_actual_ac_even_when_global_list_claims_coverage(self):
        agents = FakeAgents(overrides={
            "Planner": lambda output, request: {**output, "steps": [
                {**output["steps"][0], "ac_ids": ["AC-other"]}]},
        })
        output = await self.run_graph(agents)
        self.assertEqual(output["stop_reason"], "incomplete_plan")
        self.assertNotIn("Coder", [role for role, _ in agents.calls])

    async def test_plan_cannot_repeat_step_ids(self):
        agents = FakeAgents(overrides={
            "Planner": lambda output, request: {**output, "steps": output["steps"] * 2},
        })
        output = await self.run_graph(agents)
        self.assertEqual(output["stop_reason"], "invalid_output")

    async def test_analyst_cannot_weaken_inherited_subtask_criteria(self):
        state = make_state(scope="subtask", work_id="subtask-1")
        state["work"]["acceptance_criteria"] = [
            {"id": "AC1", "description": "Must preserve this requirement"},
        ]
        output = await self.run_graph(FakeAgents(), state)
        self.assertEqual(output["stop_reason"], "changed_acceptance_criteria")

    async def test_patch_change_invalidates_previous_test_and_review(self):
        agents = FakeAgents()
        nodes = ChainNodes(agents)
        state = make_state()
        state.update(await nodes.analyst(state))
        state.update(await nodes.planner(state))
        state.update(await nodes.coder(state))
        old_hash = state["patch_hash"]
        state.update(await nodes.tester(state))
        state["review_report"] = {"patch_hash": old_hash, "verdict": "PASS"}
        state.update(await nodes.coder(state))
        self.assertNotEqual(state["patch_hash"], old_hash)
        self.assertIsNone(state["test_report"])
        self.assertIsNone(state["review_report"])
        self.assertFalse(state["tests_passed"])

    async def test_budget_guard_stops_between_agents(self):
        agents = FakeAgents()
        output = await self.run_graph(
            agents, budget_guard=lambda state: len(agents.calls) < 2,
        )
        self.assertEqual(output["stop_reason"], "budget_exceeded")
        self.assertNotIn("Coder", [role for role, _ in agents.calls])

    async def test_deadline_and_agent_timeout_end_run(self):
        state = make_state()
        state["deadline_at"] = time.time() - 1
        agents = FakeAgents()
        output = await self.run_graph(agents, state)
        self.assertEqual(output["stop_reason"], "timeout")
        self.assertEqual(agents.calls, [])

        ready = FakeAgents()

        async def slow_agent(role, request):
            if role == "Environment":  # has no agent timeout; the first timed role is Analyst
                return await ready(role, request)
            await asyncio.sleep(1)

        state = make_state()
        state["limits"]["agent_timeout_seconds"] = 0.01
        output = await self.run_graph(slow_agent, state)
        self.assertEqual(output["stop_reason"], "timeout")

    async def test_test_infrastructure_error_is_not_a_code_retry(self):
        agents = FakeAgents(overrides={
            "Tester": lambda output, request: {**output, "status": "error"},
        })
        output = await self.run_graph(agents)
        self.assertEqual(output["stop_reason"], "test_execution_error")
        self.assertEqual(output["rounds_completed"], 1)
        self.assertIsNotNone(output["test_report"])
        self.assertNotIn("Reviewer", [role for role, _ in agents.calls])

    async def test_adapter_errors_are_sanitized(self):
        async def broken_agent(role, request):
            raise RuntimeError("SECRET-EXAMPLE should not reach events or result")

        output = await self.run_graph(broken_agent)
        self.assertEqual(output["stop_reason"], "agent_error")
        self.assertNotIn("SECRET-EXAMPLE", repr(output))

    async def test_reused_graph_does_not_inherit_another_work_items_state(self):
        agents = FakeAgents(pass_on_round=2)
        graph = build_chain_graph(ChainNodes(agents))
        first = await graph.ainvoke(make_state(), {"recursion_limit": 24})
        second = await graph.ainvoke(
            make_state(run_id="run-2", scope="subtask", work_id="subtask-2"),
            {"recursion_limit": 24},
        )
        self.assertEqual(first["rounds_completed"], second["rounds_completed"])
        self.assertTrue(all(event["run_id"] == "run-2" for event in second["events"]))
        self.assertEqual(second["result"]["scope"], "subtask")
        self.assertEqual(second["result"]["work_id"], "subtask-2")

    async def test_graph_checkpoint_resume_keeps_round_and_feedback(self):
        agents = FakeAgents(pass_on_round=2)
        saver = InMemorySaver()
        graph = build_chain_graph(ChainNodes(agents), checkpointer=saver)
        config = {"configurable": {"thread_id": "run-1:task-1"}, "recursion_limit": 24}
        interrupted = await graph.ainvoke(make_state(), config, interrupt_after=["tester"])
        self.assertEqual(interrupted["rounds_completed"], 1)
        completed = await graph.ainvoke(None, config)
        self.assertEqual(completed["verdict"], "PASS")
        self.assertEqual(completed["rounds_completed"], 2)
        self.assertEqual([role for role, _ in agents.calls].count("Analyst"), 1)
        self.assertEqual([role for role, _ in agents.calls].count("Coder"), 2)
        self.assertEqual([role for role, _ in agents.calls].count("Environment"), 1)


class ReviewLoopNodeTests(unittest.IsolatedAsyncioTestCase):
    async def run_loop(self, agents, *, rounds=3, review_rounds=2):
        state = initial_chain_state(
            WorkItem(run_id="run-1", task_id="task-1", work_id="task-1", scope="task",
                     requirement="Return zero for empty input", baseline_ref="base-commit"),
            ChainLimits(max_rounds=rounds, hard_max_rounds=4, max_review_rounds=review_rounds,
                        timeout_seconds=10.0, agent_timeout_seconds=2.0),
            "chain_review_loop",
        )
        graph = build_chain_graph(ChainNodes(agents), topology=get_topology("chain_review_loop"))
        return await graph.ainvoke(state, {"recursion_limit": 6 * rounds + 16})

    @staticmethod
    def reject_until(round_number):
        def override(output, request):
            rejected = request["round"] < round_number
            return {**output, "verdict": "REJECT" if rejected else "PASS",
                    "blocking_issues": ["Handle None too"] if rejected else []}
        return override

    async def test_reviewer_rejection_sends_feedback_back_to_coder(self):
        agents = FakeAgents(overrides={"Reviewer": self.reject_until(2)})
        output = await self.run_loop(agents)
        self.assertEqual([role for role, _ in agents.calls], [
            "Environment", "Analyst", "Planner", "Coder", "Tester", "Reviewer",
            "Coder", "Tester", "Reviewer",
        ])
        second_coder = [request for role, request in agents.calls if role == "Coder"][1]
        self.assertEqual(second_coder["incoming_message"]["from"], "Reviewer")
        self.assertEqual(second_coder["artifacts"]["review_report"]["verdict"], "REJECT")
        self.assertEqual(output["verdict"], "PASS")
        self.assertEqual(output["rounds_completed"], 2)
        self.assertEqual(output["result"]["topology"], "chain_review_loop")
        sent = [event for event in output["events"] if event["event"] == "review_feedback_sent"]
        self.assertEqual([(e["target"], e["topology"]) for e in sent], [("coder", "chain_review_loop")])
        self.assertIsNone(output["review_feedback"])

    async def test_loop_is_bounded_by_max_rounds(self):
        agents = FakeAgents(verdict="REJECT")
        output = await self.run_loop(agents, rounds=2)
        roles = [role for role, _ in agents.calls]
        self.assertEqual((roles.count("Coder"), roles.count("Reviewer")), (2, 2))
        self.assertEqual(output["verdict"], "REJECT")
        self.assertEqual(output["stop_reason"], "reviewer_rejected")

    async def test_review_feedback_rounds_have_their_own_cap(self):
        agents = FakeAgents(verdict="REJECT")
        output = await self.run_loop(agents, rounds=4, review_rounds=1)
        roles = [role for role, _ in agents.calls]
        self.assertEqual((roles.count("Coder"), roles.count("Reviewer")), (2, 2))
        self.assertEqual(output["review_rounds"], 1)
        sent = [e for e in output["events"] if e["event"] == "review_feedback_sent"]
        self.assertEqual([(e["review_round"], e["max_review_rounds"]) for e in sent], [(1, 1)])
        self.assertEqual(output["stop_reason"], "reviewer_rejected")

    async def test_zero_review_rounds_disables_the_feedback_edge(self):
        agents = FakeAgents(verdict="REJECT")
        output = await self.run_loop(agents, review_rounds=0)
        roles = [role for role, _ in agents.calls]
        self.assertEqual((roles.count("Coder"), roles.count("Reviewer")), (1, 1))
        self.assertEqual(output["review_rounds"], 0)

    async def test_failing_tests_do_not_use_the_review_loop(self):
        agents = FakeAgents(pass_on_round=100)
        output = await self.run_loop(agents, rounds=2)
        roles = [role for role, _ in agents.calls]
        self.assertEqual(roles.count("Reviewer"), 1)
        self.assertEqual(output["stop_reason"], "max_rounds_reached")

    async def test_plain_chain_never_loops_back_from_reviewer(self):
        agents = FakeAgents(verdict="REJECT")
        graph = build_chain_graph(ChainNodes(agents))
        output = await graph.ainvoke(make_state(rounds=3), {"recursion_limit": 40})
        self.assertEqual([role for role, _ in agents.calls].count("Coder"), 1)
        self.assertEqual(output["topology_id"], "chain")


class ChainInputTests(unittest.TestCase):
    def test_invalid_round_limits_rejected(self):
        for rounds in (0, -1, True, "2", 5):
            with self.subTest(rounds=rounds):
                with self.assertRaises(ValidationError):
                    ChainLimits(
                        max_rounds=rounds, hard_max_rounds=4,
                        timeout_seconds=10.0, agent_timeout_seconds=2.0,
                    )
