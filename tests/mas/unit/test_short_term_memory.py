"""Persistent Coder memory compacts on writes, never while building model requests."""

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from pydantic import ValidationError

from raac.common.tracing import JSONLTrace
from raac.harness.layer2_core.memory.short_term import (
    ShortTermMemory, ShortTermMemoryPolicy, estimated_tokens,
)


class ShortTermMemoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name) / "memory"
        self.identity = {"run_id": "run-1", "task_id": "task", "work_id": "work", "scope": "task",
                         "agent_id": "coder", "baseline_ref": "snapshot:one", "prompt_hash": "prompt-v1"}
        self.policy = ShortTermMemoryPolicy(max_tokens=1024, target_tokens=700)
        self.redactor = JSONLTrace(Path(self.temp.name) / "unused.jsonl", secrets=("private-test-value",))

    def open(self, **identity):
        return ShortTermMemory(self.directory, {**self.identity, **identity}, self.policy,
                               redact=self.redactor.redact)

    def test_persists_actions_and_reloads_only_the_exact_scope(self):
        memory = self.open()
        memory.append("tool", round_number=1, tool="edit_file", arguments={"path": "app.py", "content": "x = 1"},
                      observation={"ok": True, "result": {"path": "app.py"}})
        self.assertEqual(self.open().view(), memory.view())
        for changes in ({"run_id": "run-2"}, {"work_id": "other-work"}, {"agent_id": "tester"},
                        {"baseline_ref": "snapshot:two"}, {"prompt_hash": "prompt-v2"}):
            with self.subTest(changes=changes):
                self.assertEqual(self.open(**changes).view()["entries"], [])
        self.assertEqual(len(list(self.directory.glob("*.json"))), 1)

    def test_compaction_is_triggered_only_when_writes_cross_the_threshold(self):
        from raac.harness.layer2_core.memory import short_term
        memory = self.open()
        with patch.object(short_term, "compact_entries", wraps=short_term.compact_entries) as compact:
            memory.append("tool", round_number=1, tool="run_command", arguments={"command": ["python", "--version"]},
                          observation={"ok": True, "result": {"exit_code": 0, "stdout": "Python"}})
            self.assertEqual(compact.call_count, 0)
            before = memory.path.read_bytes()
            for _ in range(3):
                self.open().view()
                memory.view()
            self.assertEqual(memory.path.read_bytes(), before)
            self.assertEqual(compact.call_count, 0)
            stats = memory.append("assistant", round_number=1, content="large-history-marker " * 2000)
            self.assertTrue(stats["compacted"])
            self.assertEqual(compact.call_count, 1)
            self.assertLessEqual(estimated_tokens(memory.view()["entries"]), self.policy.max_tokens)
            self.assertLess(memory.path.stat().st_size, memory.journal_path.stat().st_size)
            self.assertIn("large-history-marker " * 100, memory.journal_path.read_text())
            after = memory.path.read_bytes()
            memory.view()
            self.assertEqual(memory.path.read_bytes(), after)
            self.assertEqual(compact.call_count, 1)

    def test_compaction_keeps_previous_failures_and_latest_handoff(self):
        memory = self.open()
        memory.append("tool", round_number=1, tool="run_tests", arguments={},
                      observation={"ok": True, "result": {"status": "failed", "failed": 1,
                          "summary": "boundary-regression-marker", "stderr": "traceback " * 1500}})
        memory.append("handoff", round_number=1, summary="saved-app-marker", repository_changes=[{"path": "app.py"}])
        for i in range(8):
            memory.append("assistant", round_number=2, content=f"long note {i} " * 1000)
        rendered = json.dumps(memory.view())
        self.assertIn("boundary-regression-marker", rendered)
        self.assertIn("saved-app-marker", rendered)
        self.assertLessEqual(estimated_tokens(memory.view()["entries"]), self.policy.max_tokens)
        self.assertEqual(len(memory.journal_path.read_text().splitlines()), 10)
        self.assertEqual(self.open().view(), memory.view())

    def test_repeated_compaction_preserves_record_envelopes_with_large_metadata(self):
        memory = self.open()
        for index in range(12):
            memory.append("tool", round_number=1, tool="edit_file", arguments={
                "path": "src/" + str(index) + "long-path" * 100,
                "content": "source" * 3000}, observation={"ok": True, "result": {"path": "app.py"}})
            self.assertLessEqual(estimated_tokens(memory.view()["entries"]), self.policy.max_tokens)
            for entry in memory.view()["entries"]:
                self.assertIn("kind", entry)
                self.assertIn("sequence", entry)
        self.assertEqual(self.open().view(), memory.view())

    def test_redacts_both_live_memory_and_the_durable_journal(self):
        memory = self.open()
        memory.append("tool", round_number=1, tool="read_file", arguments={}, observation={
            "api_key": "private-test-value", "stdout": "Bearer example-token", "content": "private-test-value"})
        for path in (memory.path, memory.journal_path):
            text = path.read_text()
            self.assertNotIn("private-test-value", text)
            self.assertNotIn("Bearer example-token", text)
            self.assertIn("REDACTED", text)

    def test_memory_view_cannot_mutate_the_store(self):
        memory = self.open()
        memory.append("handoff", round_number=1, summary="saved")
        before = deepcopy(memory.view())
        memory.view()["entries"].clear()
        self.assertEqual(memory.view(), before)

    def test_invalid_policy_is_rejected(self):
        for values in ({"max_tokens": 1024, "target_tokens": 1024}, {"max_tokens": 100},
                       {"enabled": "true"}):
            with self.subTest(values=values), self.assertRaises(ValidationError):
                ShortTermMemoryPolicy.model_validate(values)
