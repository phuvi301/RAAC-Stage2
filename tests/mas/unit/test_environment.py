"""Environment gates: real isolated installation, observed readiness, clocks and cancellation."""

import asyncio
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
import zipfile

import httpx
from pydantic import ValidationError

from raac.collaboration.levels.l3_collaboration import ChainNodes, ChainLimits, WorkItem, initial_chain_state
from raac.collaboration.levels.l3_collaboration.chain_state import RoleHandoff, parse_role_output
from raac.collaboration.levels.l3_collaboration.chain_handoff import prepare_role_request
from raac.harness.demo_agent import DemoAgentRunner
from raac.harness.layer3_execution.local_executor import LocalExecutor, LocalExecutionPolicy
from raac.harness.layer3_execution.tool_orchestrator import ToolOrchestrator
from raac.harness.layer3_execution.tools.workspace import WorkspaceTools
from raac.llm.client import LLMClient
from raac.llm.config import LLMConfig
from raac.registry.loader import load_registry

ROOT = Path(__file__).resolve().parents[3]
READY = {"status": "ready", "summary": "Runtime verified", "blocking_issues": []}


def state():
    return initial_chain_state(
        WorkItem(run_id="environment-test", task_id="task", work_id="task", scope="task",
                 requirement="Fix behavior", baseline_ref="baseline"),
        ChainLimits(max_rounds=2, hard_max_rounds=4, timeout_seconds=10.0, agent_timeout_seconds=0.01))


class EnvironmentTests(unittest.IsolatedAsyncioTestCase):
    async def test_runner_sends_existing_candidates_without_claiming_readiness(self):
        executable = self.workspace.root / ".venv39/Scripts/python.exe"
        executable.parent.mkdir(parents=True)
        executable.write_text("unverified executable fixture")
        payloads = []

        def handler(request):
            payloads.append(json.loads(request.content))
            return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": json.dumps(
                {"status": "blocked", "summary": "Fixture does not execute candidate", "blocking_issues": ["Not verified"]})},
                "finish_reason": "stop"}], "usage": {"prompt_tokens": 10, "completion_tokens": 5}})

        client = LLMClient(LLMConfig(model="fixture"), log_path=self.project / "candidates.jsonl",
                           transport=httpx.MockTransport(handler))
        runner = DemoAgentRunner(self.registry, client, self.tools, provenance={})
        try:
            output = await runner("Environment", {"run_id": "candidate-run", "task_id": "task",
                "work_id": "task", "scope": "task", "requirement": "Fix behavior", "output_schema": {}})
        finally:
            await client.aclose()
        request = json.loads(payloads[0]["messages"][1]["content"])
        self.assertEqual(request["execution_environment"]["python_candidates"], [str(executable.absolute())])
        self.assertIsNone(request["execution_environment"]["prepared"])
        self.assertFalse(output.runtime["verified"])

    async def test_configure_relative_python_is_resolved_from_nested_cwd(self):
        nested = self.workspace.root / "nested"
        nested.mkdir()
        runtime = nested / ".venv39" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
        runtime.parent.mkdir(parents=True)
        runtime.write_text("fixture executable; execution is mocked")
        from unittest.mock import AsyncMock
        self.executor.run_command = AsyncMock(return_value={"status": "completed", "exit_code": 0})
        relative = str(runtime.relative_to(nested))
        result = await self.workspace.environment.configure(
            python_executable=relative, cwd="nested", test_command=["python", "-m", "unittest", "discover"],
            check_command=["python", "-c", "import unittest"])
        self.assertTrue(result["ready"])
        self.assertEqual(result["configuration"]["python_executable"], str(runtime.absolute()))
        self.assertEqual(result["configuration"]["test_command"][0], str(runtime.absolute()))
        self.assertTrue(all(call.kwargs["command"][0] == str(runtime.absolute())
                            for call in self.executor.run_command.call_args_list))

    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name)
        (self.project / "repo").mkdir()
        (self.project / "requirement").mkdir()
        (self.project / "requirement/user_chat.txt").write_text("Fix behavior")
        self.executor = LocalExecutor(LocalExecutionPolicy(timeout_seconds=0.05))
        self.workspace = WorkspaceTools(self.project, self.executor)
        self.tools = ToolOrchestrator(self.workspace, timeout_seconds=0.05)
        self.registry = load_registry(ROOT / "registry")
        for key, spec in self.registry.roles.items():
            self.registry.roles[key] = spec.model_copy(update={"model": spec.model.model_copy(update={"ref": "default"})})
        self.role = self.registry.get("Environment")

    async def test_real_installation_and_shared_python_without_role_tool_timeout(self):
        # Only install our own tiny local wheel into a temporary venv; no network
        # or user packages are accessed or modified.
        root = self.workspace.root
        (root / "tests").mkdir()
        (root / "tests/test_dependency.py").write_text(
            "import unittest\nimport raac_env_fixture\nclass Case(unittest.TestCase):\n"
            "    def test_value(self): self.assertEqual(raac_env_fixture.VALUE, 42)\n")
        wheel = root / "raac_env_fixture-1.0-py3-none-any.whl"
        with zipfile.ZipFile(wheel, "w") as archive:
            archive.writestr("raac_env_fixture.py", "VALUE = 42\n")
            archive.writestr("raac_env_fixture-1.0.dist-info/METADATA", "Metadata-Version: 2.1\nName: raac-env-fixture\nVersion: 1.0\n")
            archive.writestr("raac_env_fixture-1.0.dist-info/WHEEL", "Wheel-Version: 1.0\nGenerator: raac-test\nRoot-Is-Purelib: true\nTag: py3-none-any\n")
            archive.writestr("raac_env_fixture-1.0.dist-info/RECORD", "")
        made = await self.tools.execute(self.role, "run_command", {
            "command": [sys.executable, "-m", "venv", ".venv"]})
        self.assertEqual(made["result"]["exit_code"], 0, made)
        python = root / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
        configuration = {"python_executable": str(python), "cwd": ".",
                         "test_command": ["python", "-m", "unittest", "discover", "-s", "tests"],
                         "check_command": ["python", "-c", "import unittest, raac_env_fixture"]}
        missing = await self.tools.execute(self.role, "configure_environment", configuration)
        self.assertTrue(missing["ok"])
        self.assertFalse(missing["result"]["ready"])
        self.assertIn("ModuleNotFoundError", missing["result"]["verification"][-1]["stderr"])
        installed = await self.tools.execute(self.role, "run_commands", {"commands": [
            {"command": [str(python), "-m", "pip", "install", "--no-index", "--no-deps", str(wheel)]},
            {"command": [str(python), "-c", "import time; time.sleep(.1)"]}]})
        self.assertEqual(installed["result"]["completed"], 2, installed)
        self.assertEqual(installed["result"]["failed"], 0, installed)
        configured = await self.tools.execute(self.role, "configure_environment", configuration)
        self.assertTrue(configured["result"]["ready"], configured)
        self.assertEqual(self.workspace.test_spec["command"][0], str(python.absolute()))
        # Normal roles keep finite tool clocks, but use Environment's interpreter.
        self.executor.policy.timeout_seconds = 10.0
        self.tools.timeout_seconds = 10.0
        coder = self.registry.get("Coder")
        observed = await self.tools.execute(coder, "run_command", {
            "command": [sys.executable, "-c", "import sys, raac_env_fixture; print(sys.executable); print(raac_env_fixture.VALUE)"]})
        self.assertEqual(observed["result"]["exit_code"], 0)
        self.assertIn(str(python.absolute()), observed["result"]["stdout"])
        tested = await self.tools.execute(self.registry.get("Tester"), "run_tests", {})
        self.assertEqual(tested["result"]["status"], "passed", tested)
        self.assertEqual(tested["result"]["tests_run"], 1)
        self.assertEqual(self.workspace.current_patch(), "")

    async def test_pytest_configuration_collects_selected_suite_not_just_version(self):
        # The installed host pytest can be used without installing anything.
        self.executor.policy.timeout_seconds = 10.0
        tests = self.workspace.root / "test_import.py"
        tests.write_text("import raac_missing_dependency_xyz\ndef test_case(): pass\n")
        arguments = {"python_executable": sys.executable, "cwd": ".",
                     "test_command": ["python", "-m", "pytest", "test_import.py"],
                     "check_command": ["python", "--version"]}
        failed = await self.tools.execute(self.role, "configure_environment", arguments)
        self.assertFalse(failed["result"]["ready"])
        check = failed["result"]["verification"][-1]
        self.assertIn("--collect-only", check["command"])
        self.assertIn("raac_missing_dependency_xyz", check["stdout"])
        self.assertIsNone(self.workspace.test_spec)
        tests.write_text("def test_case(): assert False\n")
        # Failing task assertions must not block environment readiness.
        good = await self.tools.execute(self.role, "configure_environment", arguments)
        self.assertTrue(good["result"]["ready"], good)
        self.assertEqual(good["result"]["smoke_report"]["status"], "failed")
        self.assertEqual(good["result"]["smoke_report"]["failed"], 1)
        self.assertTrue(good["result"]["smoke_report"]["failure_samples"])
        tests.write_text("import raac_missing_dependency_xyz\n")
        again = await self.tools.execute(self.role, "configure_environment", arguments)
        self.assertFalse(again["result"]["ready"])
        self.assertFalse(self.workspace.environment.ready)

    async def test_normal_commands_still_timeout_and_environment_can_be_cancelled(self):
        sleeping = {"command": [sys.executable, "-c", "import time; time.sleep(5)"]}
        normal = await self.tools.execute(self.registry.get("Coder"), "run_command", sleeping)
        self.assertTrue(not normal["ok"] or normal["result"]["status"] == "error")
        task = asyncio.create_task(self.tools.execute(self.role, "run_command", sleeping))
        await asyncio.sleep(0.1)
        self.assertFalse(task.done())
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task

    async def test_other_role_cannot_select_runtime_even_with_injected_allowlist(self):
        coder = self.registry.get("Coder")
        coder = type(coder)(coder.spec.model_copy(update={"tools": ["configure_environment"]}), coder.system_prompt, coder.prompt_hash)
        result = await self.tools.execute(coder, "configure_environment", {
            "python_executable": sys.executable, "test_command": ["python", "--version"],
            "check_command": ["python", "--version"]})
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_kind"], "ToolNotAllowed")

    async def test_ready_claim_without_observed_configuration_is_blocked(self):
        def handler(request):
            timeout = request.extensions["timeout"]
            self.assertTrue(all(value is None for value in timeout.values()), timeout)
            return httpx.Response(200, json={"model": "mock", "choices": [{"finish_reason": "stop",
                "message": {"role": "assistant", "content": json.dumps(READY)}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5}})
        client = LLMClient(LLMConfig(model="mock", timeout_seconds=0.01),
                           log_path=self.project / "log.jsonl", transport=httpx.MockTransport(handler))
        runner = DemoAgentRunner(self.registry, client, self.tools, provenance={})
        try:
            request = {**state()["work"], "round": 0, "artifacts": {}, "output_format": "json",
                       "output_schema": {}}
            report = await runner("Environment", request)
            self.assertEqual(report.output["status"], "blocked")
            self.assertFalse(report.runtime["verified"])
            self.assertIsNone(runner.role_timeout("Environment", 0.01))
            self.assertEqual(runner.role_timeout("Coder", 0.01), 0.01)
        finally:
            await client.aclose()

    async def test_environment_clock_pauses_run_deadline_and_failures_stop_handoff(self):
        async def slow(role, request):
            self.assertEqual(role, "Environment")
            await asyncio.sleep(0.08)
            return RoleHandoff(READY, {"verified": True, "configuration": {"cwd": "."}})
        current = state()
        current["deadline_at"] = time.time() + 0.04
        original = deepcopy(current)
        update = await ChainNodes(slow).environment(current)
        self.assertEqual(update["next_node"], "analyst")
        self.assertGreater(update["deadline_at"], time.time())
        self.assertEqual(current, original)
        async def failed(role, request):
            return RoleHandoff({"status": "blocked", "summary": "Missing dependency",
                                "blocking_issues": ["No compatible interpreter"]}, {"verified": False})
        update = await ChainNodes(failed).environment(state())
        self.assertEqual(update["next_node"], "result")
        self.assertEqual(update["stop_reason"], "environment_setup_failed")
        async def claimed(role, request):
            return READY
        update = await ChainNodes(claimed).environment(state())
        self.assertEqual(update["stop_reason"], "environment_setup_failed")

    async def test_output_and_timeout_config_are_strict(self):
        for output in ({**READY, "blocking_issues": ["still missing"]},
                       {"status": "blocked", "summary": "failed", "blocking_issues": []}):
            with self.assertRaises(ValueError):
                parse_role_output("Environment", output)
        spec = self.role.spec
        for value in (-1.0, 0.0, float("inf"), True):
            with self.assertRaises(ValidationError):
                type(spec).model_validate({**spec.model_dump(), "timeout_seconds": value})
        schemas = {item["function"]["name"]: item["function"]["parameters"] for item in self.tools.declarations(self.role)}
        self.assertEqual(schemas["run_command"]["properties"]["timeout_seconds"]["type"], "null")


    async def test_large_collection_audit_is_not_repeated_in_role_handoff(self):
        report = {**READY, "configuration": {"python_executable": sys.executable},
                  "verification": [{"status": "completed", "exit_code": 0, "stdout": "node-id " * 5000}]}
        request = {"environment_report": report, "artifacts": {"environment_report": deepcopy(report)}}
        original = deepcopy(request)
        selected = prepare_role_request(request, role="Coder")
        self.assertEqual(request, original)
        self.assertEqual(selected["environment_report"]["configuration"], report["configuration"])
        self.assertEqual(selected["environment_report"]["verification"][0]["stdout_chars"], 40000)
        self.assertLess(len(json.dumps(selected)), 5000)


    async def test_large_test_report_handoff_keeps_counts_and_actual_failure_examples(self):
        results = [{"test": "case_" + str(i), "status": "failed", "details": "AttributeError: dependency API removed\n" + "trace" * 400}
                   for i in range(962)]
        report = {"results": results, "tests_run": 962, "failed": 962, "test_cases": [item["test"] for item in results],
                  "covered_ac_ids": [], "blocking_issues": ["Dependency incompatible"], "summary": "Fail"}
        request = {"artifacts": {"test_report": report}, "incoming_message": {"from": "Tester", "content": report}}
        original = deepcopy(request)
        selected = prepare_role_request(request, role="Coder")
        received = selected["artifacts"]["test_report"]
        self.assertEqual(request, original)
        self.assertEqual(received["tests_run"], 962)
        self.assertEqual(received["failed"], 962)
        self.assertEqual(received["blocking_issues"], ["Dependency incompatible"])
        self.assertIn("AttributeError", received["results"][0]["details"])
        self.assertEqual(received["report_context"]["total_case_results"], 962)
        self.assertLess(len(json.dumps(selected)), 6000)
