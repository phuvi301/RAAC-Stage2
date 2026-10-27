"""Real local commands in disposable fixture repos; never runs the user's project."""

import asyncio
from pathlib import Path
import sys
import tempfile
import subprocess
import json
import os
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


    async def test_large_structured_report_is_complete_with_small_stdout_limit(self):
        script = self.root / "large_suite.py"
        script.write_text("""from pathlib import Path
import xml.etree.ElementTree as ET
root = ET.Element('testsuite')
for i in range(962):
    case = ET.SubElement(root, 'testcase', classname='suite', name='case_' + str(i))
    if i % 2:
        ET.SubElement(case, 'failure', message='expected fixture failure').text = 'details-' * 160
ET.ElementTree(root).write('suite.xml', encoding='utf-8')
""")
        executor = LocalExecutor(LocalExecutionPolicy(timeout_seconds=8.0, max_output_bytes=1024))
        report = await executor.run_tests(self.root, command=["python", "large_suite.py"], report_path="suite.xml")
        self.assertEqual(report["status"], "failed", report)
        self.assertEqual(report["tests_run"], 962)
        self.assertEqual(report["failed"], 481)
        self.assertEqual(len(report["results"]), 962)
        self.assertEqual(len(report["test_cases"]), 962)
        self.assertGreater(len(json.dumps(report)), 131072)
        self.assertLess(len(report["execution"]["stdout"].encode()), 1024)
        executor.policy.max_report_bytes = 1024
        rejected = await executor.run_tests(self.root, command=["python", "large_suite.py"], report_path="suite.xml")
        self.assertEqual(rejected["status"], "error")
        self.assertEqual(rejected["tests_run"], 0)

    async def test_pipe_transports_close_before_a_separate_event_loop_exits(self):
        script = self.root / "check_cleanup.py"
        script.write_text("""import asyncio, gc, warnings, sys
from pathlib import Path
from raac.harness.layer3_execution.local_executor import LocalExecutor, LocalExecutionPolicy
warnings.simplefilter('always', ResourceWarning)
async def main():
    executor = LocalExecutor(LocalExecutionPolicy(timeout_seconds=1.0, max_output_bytes=1024))
    for i in range(3):
        result = await executor.run_command(Path.cwd(), command=[sys.executable, '-c', "print('x' * 200000)"])
        assert result['status'] == 'error', result
    task = asyncio.create_task(executor.run_command(Path.cwd(), command=[sys.executable, '-c', 'import time; time.sleep(30)']))
    await asyncio.sleep(.15)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
asyncio.run(main())
gc.collect()
""")
        completed = subprocess.run([sys.executable, str(script)], cwd=self.root,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                   env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[3] / "src")},
                                   timeout=15, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertNotIn("unclosed transport", completed.stderr)
        self.assertNotIn("closed pipe", completed.stderr)
        self.assertNotIn("Exception ignored", completed.stderr)


    async def test_nested_cwd_resolves_relative_venv_for_commands_and_tests(self):
        project = self.root / "xarray"
        project.mkdir()
        created = await self.executor.run_command(self.root, command=[
            sys.executable, "-m", "venv", "--without-pip", str(project / ".venv")])
        self.assertEqual(created["exit_code"], 0, created)
        relative = str(Path(".venv") / ("Scripts/python.exe" if os.name == "nt" else "bin/python"))
        expected = str((project / relative).absolute())
        # A competing root venv must not replace the nested project's runtime.
        root_candidate = self.root / relative
        root_candidate.parent.mkdir(parents=True)
        root_candidate.write_text("wrong root interpreter")
        for command in ([relative, "-c", "import sys; print(sys.executable)"],
                        ["python", "-c", "import sys; print(sys.executable)"]):
            result = await self.executor.run_command(self.root, command=command, cwd="xarray")
            self.assertEqual(result["exit_code"], 0, result)
            self.assertEqual(result["command"][0], expected)
            self.assertIn(expected, result["stdout"])
        (project / "test_case.py").write_text(
            "import unittest\nclass Case(unittest.TestCase):\n def test_ok(self): self.assertEqual(1, 1)\n")
        tested = await self.executor.run_tests(self.root,
            command=[relative, "-m", "unittest", "discover"], cwd="xarray")
        self.assertEqual(tested["status"], "passed", tested)
        self.assertEqual(tested["tests_run"], 1)
        self.assertEqual(tested["command"][0], expected)

    async def test_missing_relative_executable_reports_child_cwd_without_root_fallback(self):
        (self.root / "nested").mkdir()
        (self.root / "tools").mkdir()
        (self.root / "tools/launcher").write_text("root tool is unrelated")
        result = await self.executor.run_command(self.root,
            command=[str(Path("tools") / "launcher")], cwd="nested")
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error_kind"], "FileNotFoundError")
        self.assertEqual(result["command"][0], str(self.root / "nested/tools/launcher"))
        self.assertIn("Cannot start command", result["summary"])
