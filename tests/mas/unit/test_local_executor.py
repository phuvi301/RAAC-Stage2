"""Real local commands in disposable fixture repos; never runs the user's project."""

import asyncio
from pathlib import Path
import sys
import tempfile
import unittest

from raac.harness.layer3_execution.local_executor import LocalExecutor, LocalExecutionPolicy


class LocalExecutorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.executor = LocalExecutor(LocalExecutionPolicy(timeout_seconds=8.0))

    async def test_command_uses_real_cwd_env_and_reports_nonzero_exit(self):
        (self.root / "work").mkdir()
        result = await self.executor.run_command(self.root, command=["python", "-c",
            "import os,sys; from pathlib import Path; Path('built.txt').write_text(os.environ['RAAC_FIXTURE']); print('out'); print('err', file=sys.stderr); sys.exit(3)"],
            cwd="work", env={"RAAC_FIXTURE": "local"})
        self.assertEqual(result["exit_code"], 3)
        self.assertEqual(result["mode"], "local")
        self.assertEqual(result["command"][0], sys.executable)
        self.assertIn("out", result["stdout"])
        self.assertIn("err", result["stderr"])
        self.assertEqual((self.root / "work/built.txt").read_text(), "local")

    async def test_unittest_observes_real_counts_and_failures(self):
        (self.root / "checks").mkdir()
        (self.root / "checks/test_example.py").write_text(
            "import unittest\nclass Example(unittest.TestCase):\n def test_pass(self): self.assertEqual(2+2,4)\n def test_fail(self): self.assertEqual(2+2,5)\n")
        result = await self.executor.run_tests(self.root,
            command=["python", "-m", "unittest", "discover", "-s", "checks"])
        self.assertEqual(result["status"], "failed", result)
        self.assertEqual(result["tests_run"], 2)
        self.assertEqual(result["failed"], 1)
        self.assertEqual(len(result["test_cases"]), 2)
        outcomes = {row["test"].split(".")[-1]: row for row in result["results"]}
        self.assertEqual(outcomes["test_pass"]["status"], "passed")
        self.assertEqual(outcomes["test_fail"]["status"], "failed")
        self.assertIn("AssertionError", outcomes["test_fail"]["details"])

    async def test_custom_junit_command_uses_nested_cwd(self):
        (self.root / "verify").mkdir()
        (self.root / "verify/check.py").write_text(
            "from pathlib import Path\nPath('result.xml').write_text('<testsuite><testcase classname=\"custom\" name=\"check\"/></testsuite>')\n")
        result = await self.executor.run_tests(self.root, command=["python", "check.py"],
                                               cwd="verify", report_path="result.xml")
        self.assertEqual(result["status"], "passed", result)
        self.assertEqual(result["test_cases"], ["custom.check"])
        self.assertEqual(result["results"], [{"test": "custom.check", "status": "passed", "details": ""}])
        self.assertTrue((self.root / "verify/result.xml").exists())

    async def test_many_subtest_failures_count_as_one_failed_method(self):
        (self.root / "test_subcases.py").write_text(
            "import unittest\nclass Example(unittest.TestCase):\n"
            " def test_many(self):\n"
            "  for n in range(30):\n"
            "   with self.subTest(n=n): self.assertEqual(n,-1)\n"
            " def test_ok(self): self.assertTrue(True)\n")
        report = await self.executor.run_tests(self.root, command=["python", "-m", "unittest", "discover"])
        self.assertEqual(report["status"], "failed", report)
        self.assertEqual(report["tests_run"], 2)
        self.assertEqual(report["failed"], 1)
        self.assertEqual(report["subtest_failures"], 30)

    async def test_tool_declares_actual_timeout_and_returns_actionable_error(self):
        from types import SimpleNamespace
        from raac.harness.layer3_execution.tool_orchestrator import ToolOrchestrator
        workspace = SimpleNamespace(executor=self.executor)
        tools = ToolOrchestrator(workspace)
        role = SimpleNamespace(spec=SimpleNamespace(name="Coder", id="coder", tools=["run_command"]))
        schema = tools.declarations(role)[0]["function"]["parameters"]
        self.assertEqual(schema["properties"]["timeout_seconds"]["anyOf"][0]["maximum"], 8.0)
        result = await tools.execute(role, "run_command", {"command": ["python", "--version"], "timeout_seconds": 300.0})
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_kind"], "TimeoutPolicyExceeded")
        self.assertIn("8.0", result["error"])

    async def test_zero_tests_and_missing_runtime_are_errors(self):
        empty = await self.executor.run_tests(self.root, command=["python", "-m", "unittest", "discover"])
        self.assertEqual(empty["status"], "error", empty)
        missing = await self.executor.run_command(self.root, command=["raac_nonexistent_runtime_fixture"])
        self.assertEqual(missing["status"], "error")
        self.assertIsNone(missing["exit_code"])

    async def test_timeout_stops_parent_and_child(self):
        # If the child survives taskkill/process-group termination it leaves a marker.
        child = "import time; from pathlib import Path; time.sleep(1.5); Path('survived.txt').write_text('bad')"
        parent = "import subprocess,sys,time; subprocess.Popen([sys.executable,'-c'," + repr(child) + "]); time.sleep(30)"
        report = await self.executor.run_command(self.root, command=["python", "-c", parent], timeout_seconds=0.6)
        self.assertEqual(report["status"], "error", report)
        self.assertEqual(report["error_kind"], "TimeoutError")
        await asyncio.sleep(1.6)
        self.assertFalse((self.root / "survived.txt").exists())

    async def test_output_limit_and_cwd_escape(self):
        limited = LocalExecutor(LocalExecutionPolicy(timeout_seconds=5.0, max_output_bytes=1024))
        result = await limited.run_command(self.root, command=["python", "-c", "print('x' * 10000)"])
        self.assertEqual(result["error_kind"], "OutputLimitError", result)
        self.assertLessEqual(len(result["stdout"].encode()), 1024)
        with self.assertRaises(ValueError):
            await self.executor.run_command(self.root, command=["python", "--version"], cwd="../outside")
