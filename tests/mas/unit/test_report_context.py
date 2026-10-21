"""Large reports stay complete in execution and small in model context."""

from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
import unittest

from raac.common.report_context import compact_test_report, compact_environment_report, encoded_size
from raac.collaboration.levels.l3_collaboration.chain_handoff import prepare_role_request
from raac.harness.layer2_core.context_manager import RoleContextPolicy, prepare_messages
from raac.harness.layer3_execution.tool_orchestrator import ToolOrchestrator
from raac.registry.loader import load_registry


def large_report():
    names = ["test_weighted.py::test_case_" + str(i) + "[" + "parameter" * 10 + "]" for i in range(969)]
    return {"status": "failed", "tests_run": 969, "failed": 6, "skipped": 2,
            "test_cases": names, "tests_to_run": names,
            "results": [{"test": name, "status": "failed" if i < 6 else "skipped" if i >= 967 else "passed",
                         "details": ("AssertionError: expected aligned values\n" + "trace " * 1000) if i < 6 else ""}
                        for i, name in enumerate(names)],
            "summary": "collection/test detail\n" * 2000,
            "verdict": "fail", "patch_hash": "current-patch", "covered_ac_ids": ["AC1", "AC2"],
            "blocking_issues": ["Six observed failures"],
            "execution": {"exit_code": 1, "command": ["python", "-m", "pytest"], "cwd": "."}}


def exchange(name, report, identifier):
    return [{"role": "assistant", "content": None, "tool_calls": [{"id": identifier, "type": "function",
             "function": {"name": name, "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": identifier, "content": json.dumps({"ok": True, "result": report})}]


class ReportContextTests(unittest.TestCase):
    def test_handoff_bounds_all_three_lists_and_preserves_control_evidence(self):
        report = large_report()
        original = deepcopy(report)
        view = compact_test_report(report)
        self.assertLessEqual(encoded_size(view), 8000)
        for key in ("status", "tests_run", "failed", "skipped", "verdict", "patch_hash", "covered_ac_ids", "blocking_issues"):
            self.assertEqual(view[key], report[key])
        self.assertLess(len(view["tests_to_run"]), 969)
        self.assertEqual(view["results"][0]["status"], "failed")
        self.assertIn("AssertionError", view["results"][0]["details"])
        self.assertEqual(view["report_context"]["total_case_results"], 969)
        self.assertEqual(view["report_context"]["observed_failure_results"], 6)
        self.assertEqual(view["report_context"]["tests_to_run_count"], 969)
        self.assertEqual(report, original)
        self.assertEqual(compact_test_report(view), view)

    def test_environment_streams_and_smoke_are_compact_without_losing_failed_exit(self):
        env = {"status": "ready", "configuration": {"python_executable": "selected-python"},
               "verification": [{"exit_code": 2, "stdout": "node-id " * 20000, "stderr": "ImportError: missing package\n" * 2000}],
               "smoke_report": {**large_report(), "failure_samples": large_report()["results"][:6]}}
        # Real Environment smoke reports have aggregates and failure samples only.
        for key in ("results", "test_cases", "tests_to_run", "execution", "verdict", "covered_ac_ids", "patch_hash", "blocking_issues"):
            env["smoke_report"].pop(key, None)
        original = deepcopy(env)
        view = compact_environment_report(env)
        self.assertLessEqual(encoded_size(view), 8000)
        self.assertEqual(view["verification"][0]["exit_code"], 2)
        self.assertIn("ImportError", view["verification"][0]["stderr"])
        self.assertEqual(view["verification"][0]["stdout_chars"], 160000)
        self.assertEqual(view["smoke_report"]["failed"], 6)
        self.assertEqual(env, original)

    def test_handoff_keeps_one_environment_copy_and_resolves_existing_references(self):
        env = {"status": "ready", "verification": [{"stdout": "node " * 30000}],
               "observation_ref": "env-id", "configuration": {"cwd": "."}}
        report = large_report()
        request = {"environment_report": env, "artifacts": {"environment_report": deepcopy(env), "test_report": report},
                   "incoming_message": {"environment": deepcopy(env), "content": deepcopy(report)}}
        original = deepcopy(request)
        view = prepare_role_request(request, role="Coder")
        self.assertNotIn("environment_report", view["artifacts"])
        self.assertEqual(view["incoming_message"]["environment_ref"], "environment_report")
        self.assertEqual(view["incoming_message"]["content_ref"], "artifacts.test_report")
        self.assertLess(encoded_size(view), 11000)
        self.assertEqual(request, original)

    def test_recent_and_historical_test_reports_remain_small_with_valid_protocol(self):
        base = [{"role": "system", "content": "Rules"}, {"role": "user", "content": "Task"}]
        messages = base + exchange("run_tests", large_report(), "tests")
        original = deepcopy(messages)
        prepared, _ = prepare_messages(messages, RoleContextPolicy())
        self.assertEqual(prepared[-1]["tool_call_id"], "tests")
        self.assertLessEqual(len(prepared[-1]["content"]), 8000)
        self.assertEqual(json.loads(prepared[-1]["content"])["result"]["failed"], 6)
        messages += exchange("list_files", {"files": []}, "recent")
        prepared, metrics = prepare_messages(messages, RoleContextPolicy(max_checkpoint_tokens=256))
        self.assertFalse(metrics["checkpoint_protected_over_budget"])
        self.assertLessEqual(metrics["checkpoint_estimated_tokens"], 256)
        self.assertEqual(prepared[-1]["tool_call_id"], "recent")
        self.assertIn('"failed":6', json.dumps(prepared).replace('\\"', '"').replace(" ", ""))
        self.assertEqual(messages[:len(original)], original)

    def test_configure_tool_nested_outputs_are_compacted_before_next_model_call(self):
        report = {"ready": False, "status": "failed", "verification": [
            {"exit_code": 1, "stdout": "collection " * 20000, "stderr": "ImportError: package"}], "observation_ref": "env"}
        base = [{"role": "system", "content": "Rules"}, {"role": "user", "content": "Task"}]
        prepared, _ = prepare_messages(base + exchange("configure_environment", report, "env"), RoleContextPolicy())
        self.assertLess(len(prepared[-1]["content"]), 8000)
        result = json.loads(prepared[-1]["content"])["result"]
        self.assertEqual(result["verification"][0]["exit_code"], 1)
        self.assertEqual(result["observation_ref"], "env")


class ReportDetailTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.registry = load_registry(Path(__file__).resolve().parents[3] / "registry")
        self.report = large_report()
        self.workspace = SimpleNamespace(run_tests=AsyncMock(return_value=self.report))
        self.tools = ToolOrchestrator(self.workspace)
        self.context = {"run_id": "one-run"}
        self.observed = await self.tools.execute(self.registry.get("Tester"), "run_tests", {}, context=self.context)
        self.reference = self.observed["result"]["observation_ref"]

    async def read(self, **args):
        return await self.tools.execute(self.registry.get("Reviewer"), "read_tool_result",
                                        {"observation_ref": self.reference, **args}, context=self.context)

    async def test_exact_case_and_traceback_can_be_read_by_pages_without_rerun(self):
        page = await self.read(path=["test_cases"], offset=500, limit=3)
        self.assertEqual(page["result"]["value"], self.report["test_cases"][500:503])
        self.assertEqual(page["result"]["total"], 969)
        self.assertEqual(page["result"]["next_offset"], 503)
        trace = self.report["results"][0]["details"]
        first = await self.read(path=["results", 0, "details"], limit=4000)
        second = await self.read(path=["results", 0, "details"], offset=4000, limit=4000)
        self.assertEqual(first["result"]["value"] + second["result"]["value"], trace)
        self.workspace.run_tests.assert_awaited_once()
        # Views and reads cannot mutate the stored observation.
        page["result"]["value"][0] = "changed"
        again = await self.read(path=["test_cases"], offset=500, limit=1)
        self.assertEqual(again["result"]["value"], [self.report["test_cases"][500]])

    async def test_cross_run_unknown_refs_and_roles_without_permission_are_rejected(self):
        role = self.registry.get("Reviewer")
        args = {"observation_ref": self.reference}
        self.assertFalse((await self.tools.execute(role, "read_tool_result", args, context={"run_id": "other"}))["ok"])
        self.assertFalse((await self.read(path=["results", -1]))["ok"])
        self.assertFalse((await self.read(path=["absent"]))["ok"])
        self.assertFalse((await self.tools.execute(self.registry.get("Analyst"), "read_tool_result", args, context=self.context))["ok"])

    async def test_large_nested_values_return_bounded_explicit_preview(self):
        response = await self.read(path=["results"], limit=10)
        self.assertTrue(response["result"]["preview_only"])
        self.assertLess(encoded_size(response), 6500)
        self.assertEqual(response["result"]["next_offset"], 10)

    async def test_environment_collection_text_can_be_read_exactly_from_its_ref(self):
        verification = [{"exit_code": 0, "stdout": "selected-python"},
                        {"exit_code": 0, "stdout": "collection node\n" * 10000}]
        manager = SimpleNamespace(configure=AsyncMock(return_value={
            "ready": True, "status": "completed", "verification": verification}))
        self.workspace.environment = manager
        observed = await self.tools.execute(self.registry.get("Environment"), "configure_environment", {
            "python_executable": "selected-python", "test_command": ["python", "-m", "pytest"],
            "check_command": ["python", "--version"]}, context=self.context)
        reference = observed["result"]["observation_ref"]
        self.assertEqual(manager.observation_ref, reference)
        page = await self.tools.execute(self.registry.get("Coder"), "read_tool_result", {
            "observation_ref": reference, "path": ["verification", 1, "stdout"], "offset": 20, "limit": 100},
            context=self.context)
        self.assertEqual(page["result"]["value"], verification[1]["stdout"][20:120])
        self.assertEqual(page["result"]["total"], 160000)
        manager.configure.assert_awaited_once()

    async def test_runtime_ref_and_full_results_survive_chain_report_conversion(self):
        from raac.collaboration.levels.l3_collaboration.chain_state import execution_report
        wire = {key: self.report[key] for key in ("tests_to_run", "results", "verdict", "covered_ac_ids", "blocking_issues")}
        runtime = {key: self.observed["result"][key] for key in
                   ("tests_run", "failed", "status", "summary", "test_cases", "observation_ref")}
        report = execution_report(wire, runtime, "current-patch").model_dump()
        self.assertEqual(report["observation_ref"], self.reference)
        self.assertEqual(len(report["results"]), 969)
        self.assertEqual(len(report["tests_to_run"]), 969)
        view = prepare_role_request({"artifacts": {"test_report": report}}, role="Reviewer")
        self.assertEqual(view["artifacts"]["test_report"]["observation_ref"], self.reference)
        self.assertEqual(len(report["results"]), 969)
