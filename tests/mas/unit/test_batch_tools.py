"""Batching keeps per-item observations, execution bounds and Tester permissions."""

import hashlib
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest

from raac.harness.layer3_execution.local_executor import LocalExecutor, LocalExecutionPolicy
from raac.harness.layer3_execution.tool_orchestrator import ToolOrchestrator
from raac.harness.layer3_execution.tools.workspace import WorkspaceTools
from raac.registry.loader import load_registry


PROJECT = Path(__file__).resolve().parents[3]


class BatchToolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.project = Path(temp.name)
        self.repo = self.project / "repo"
        (self.repo / "src").mkdir(parents=True)
        (self.repo / "tests").mkdir()
        (self.project / "requirement").mkdir()
        (self.project / "requirement/user_chat.txt").write_text("Fix this project")
        (self.repo / "src/main.py").write_text("one\ntwo\nthree\nfour\n")
        (self.repo / "tests/test_main.py").write_text("expected = 5\nunchanged = True\n")
        executor = LocalExecutor(LocalExecutionPolicy(timeout_seconds=4.0))
        self.workspace = WorkspaceTools(self.project, executor)
        self.events = []
        self.tools = ToolOrchestrator(self.workspace, timeout_seconds=8,
            trace=lambda context, event, **data: self.events.append({**context, "event": event, **data}))
        registry = load_registry(PROJECT / "registry")
        self.coder, self.tester, self.analyst = (registry.get(role) for role in ("Coder", "Tester", "Analyst"))
        self.context = {"agent_call_id": "coder-call", "llm_call_id": "llm-call"}

    async def test_read_many_files_and_ranges_preserves_errors_and_child_trace_ids(self):
        response = await self.tools.execute(self.analyst, "read_files", {"files": [
            {"path": "src/main.py", "start_line": 2, "end_line": 3},
            {"path": "tests/test_main.py"}, {"path": "../outside"},
            {"path": "src/main.py", "start_line": 4, "end_line": 4}]}, tool_call_id="parent-read")
        result = response["result"]
        self.assertEqual((result["requested"], result["completed"], result["failed"]), (4, 4, 1))
        self.assertEqual(result["results"][0]["observation"]["result"]["content"], "two\nthree\n")
        self.assertFalse(result["results"][2]["observation"]["ok"])
        self.assertEqual(result["results"][3]["observation"]["result"]["content"], "four\n")
        children = [event for event in self.events if event.get("origin") == "batch_item"]
        self.assertEqual(len(children), 8)
        self.assertTrue(all(event["parent_tool_call_id"] == "parent-read" for event in children))
        self.assertEqual(len({event["tool_call_id"] for event in children}), 4)

    async def test_batch_structure_limits_are_checked_before_any_child_runs(self):
        for name, args, role in (("read_files", {"files": [{"path": "src/main.py"}] * 9}, self.analyst),
                                 ("run_commands", {"commands": [{"command": ["python", "--version"]}] * 5}, self.coder)):
            result = await self.tools.execute(role, name, args)
            self.assertEqual(result["error_kind"], "ValidationError")
        self.assertFalse(any(event.get("origin") == "batch_item" for event in self.events))

    async def test_commands_share_one_llm_call_and_execute_in_order_with_own_environment(self):
        result = (await self.tools.execute(self.coder, "run_commands", {"commands": [
            {"command": ["python", "-c", "from pathlib import Path; Path('first.txt').write_text('ready')"]},
            {"command": ["python", "-c", "import os; from pathlib import Path; print(Path('../first.txt').read_text(), os.environ['RAAC_BATCH_TEST'])"],
             "cwd": "tests", "env": {"RAAC_BATCH_TEST": "batch"}}]}, context=self.context))["result"]
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["failed"], 0)
        self.assertIn("ready batch", result["results"][1]["observation"]["result"]["stdout"])
        evidence = self.tools.execution_evidence(self.context, "unused")
        self.assertEqual(evidence[1]["cwd"], str(self.repo / "tests"))
        children = [event for event in self.events if event.get("origin") == "batch_item"]
        self.assertTrue(all(event["llm_call_id"] == "llm-call" for event in children))

    async def test_fail_fast_skips_later_side_effect_and_continue_collects_each_failure(self):
        commands = [{"command": ["python", "-c", "import sys; print('bad'); sys.exit(3)"]},
                    {"command": ["python", "-c", "from pathlib import Path; Path('later.txt').write_text('ran')"]}]
        result = (await self.tools.execute(self.coder, "run_commands", {"commands": commands}))["result"]
        self.assertEqual(result["failed"], 1)
        self.assertEqual(result["skipped"], [2])
        self.assertFalse((self.repo / "later.txt").exists())
        result = (await self.tools.execute(self.coder, "run_commands", {"commands": commands, "stop_on_error": False}))["result"]
        self.assertEqual(result["completed"], 2)
        self.assertEqual(result["failed"], 1)
        self.assertTrue((self.repo / "later.txt").exists())

    async def test_batch_timeout_uses_total_budget_and_does_not_start_remaining_commands(self):
        self.workspace.executor.policy.timeout_seconds = 0.4
        began = time.perf_counter()
        result = (await self.tools.execute(self.coder, "run_commands", {"commands": [
            {"command": ["python", "-c", "import time; time.sleep(2)"]},
            {"command": ["python", "-c", "from pathlib import Path; Path('never.txt').write_text('bad')"]}]}))["result"]
        self.assertLess(time.perf_counter() - began, 3)
        self.assertEqual(result["failed"], 1)
        self.assertEqual(result["skipped"], [2])
        self.assertFalse((self.repo / "never.txt").exists())

    async def test_timeout_schema_and_later_invalid_timeout_reject_entire_batch(self):
        schema = next(tool["function"]["parameters"] for tool in self.tools.declarations(self.coder)
                      if tool["function"]["name"] == "run_commands")
        self.assertEqual(schema["$defs"]["RunCommand"]["properties"]["timeout_seconds"]["anyOf"][0]["maximum"], 4.0)
        response = await self.tools.execute(self.coder, "run_commands", {"commands": [
            {"command": ["python", "-c", "raise RuntimeError('must not run')"]},
            {"command": ["python", "--version"], "timeout_seconds": 10.0}]})
        self.assertEqual(response["error_kind"], "TimeoutPolicyExceeded")
        self.assertFalse(any(event.get("origin") == "batch_item" for event in self.events))

    async def test_continuing_commands_still_share_total_deadline(self):
        self.workspace.executor.policy.timeout_seconds = 0.45
        result = (await self.tools.execute(self.coder, "run_commands", {"stop_on_error": False, "commands": [
            {"command": ["python", "-c", "import time; time.sleep(0.3)"]},
            {"command": ["python", "-c", "import time; time.sleep(0.3)"]},
            {"command": ["python", "-c", "from pathlib import Path; Path('never.txt').write_text('bad')"]}]}))["result"]
        self.assertIn(3, result["skipped"])
        self.assertEqual(result["status"], "failed")
        self.assertFalse((self.repo / "never.txt").exists())

    async def test_batch_cannot_bypass_underlying_role_permission(self):
        role = SimpleNamespace(spec=SimpleNamespace(id="limited", name="Coder", tools=["run_commands"]))
        response = await self.tools.execute(role, "run_commands", {"commands": [{"command": ["python", "--version"]}]})
        self.assertEqual(response["result"]["results"][0]["observation"]["error_kind"], "ToolNotAllowed")
        self.assertFalse((await self.tools.execute(self.analyst, "run_commands", {"commands": [{"command": ["python"]}]}))["ok"])

    async def test_tester_exact_edit_preserves_other_text_and_uses_tester_tracking(self):
        response = await self.tools.execute(self.tester, "replace_text", {
            "path": "tests/test_main.py", "old_text": "expected = 5", "new_text": "expected = 3"})
        self.assertTrue(response["ok"])
        self.assertEqual(response["result"]["replacements"], 1)
        self.assertEqual((self.repo / "tests/test_main.py").read_text(), "expected = 3\nunchanged = True\n")
        self.assertIn("tests/test_main.py", self.workspace.tester_paths)
        self.assertNotIn("tests/test_main.py", self.workspace.coder_paths)

    async def test_ambiguous_or_stale_edits_do_not_write_anything(self):
        self.workspace.edit_file(role="Tester", path="tests/test_main.py", content="same\nsame\n")
        original = (self.repo / "tests/test_main.py").read_bytes()
        for old, matches in (("same", 2), ("missing", 0)):
            result = await self.tools.execute(self.tester, "replace_text", {
                "path": "tests/test_main.py", "old_text": old, "new_text": "new"})
            self.assertEqual(result["error_kind"], "EditConflict")
            self.assertEqual(result["matches"], matches)
            self.assertEqual((self.repo / "tests/test_main.py").read_bytes(), original)

    async def test_tester_region_edit_cannot_touch_implementation_or_coder_owned_test(self):
        self.workspace.edit_file(role="Coder", path="tests/test_main.py", content="coder owned\n")
        for path, old in (("src/main.py", "one"), ("tests/test_main.py", "coder owned"), ("../outside", "old")):
            result = await self.tools.execute(self.tester, "replace_text", {"path": path, "old_text": old, "new_text": "bad"})
            self.assertFalse(result["ok"])
        self.assertEqual((self.repo / "src/main.py").read_text(), "one\ntwo\nthree\nfour\n")

    async def test_evidence_is_observed_scoped_and_marks_checks_stale_after_code_changes(self):
        self.workspace.edit_file(role="Coder", path="src/main.py", content="value = 1\n")
        await self.tools.execute(self.coder, "run_commands", {"commands": [
            {"command": ["python", "-c", "print('actual smoke')"]}]}, context=self.context)
        patch_hash = lambda: hashlib.sha256((self.workspace.baseline_ref + "\0" + self.workspace.current_patch()).encode()).hexdigest()
        evidence = self.tools.execution_evidence(self.context, patch_hash())
        self.assertEqual(len(evidence), 1)
        self.assertIn("actual smoke", evidence[0]["stdout"])
        self.assertTrue(evidence[0]["matches_final_patch"])
        self.workspace.edit_file(role="Coder", path="src/main.py", content="value = 2\n")
        self.assertFalse(self.tools.execution_evidence(self.context, patch_hash())[0]["matches_final_patch"])
        self.assertEqual(self.tools.execution_evidence({"agent_call_id": "next-invocation"}, patch_hash()), [])

    async def test_large_evidence_is_bounded_and_retains_only_latest_eight_receipts(self):
        for index in range(12):
            self.tools._record_evidence({**self.context, "tool_call_id": str(index)}, "run_command", {},
                {"ok": True, "result": {"command": ["arg" * 1000] * 64, "stdout": "x" * 10000,
                                        "stderr": "y" * 10000, "status": "completed", "exit_code": 0}})
        evidence = self.tools.execution_evidence(self.context, "unused")
        self.assertEqual([item["tool_call_id"] for item in evidence], [str(index) for index in range(4, 12)])
        self.assertTrue(all(len(item["stdout"]) <= 300 and len(item["stderr"]) <= 500 for item in evidence))
        self.assertTrue(all(sum(map(len, item["command"])) <= 480 and item["command_shortened"] for item in evidence))
