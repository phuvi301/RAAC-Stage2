"""Working notes guide retries; journals preserve observations independently."""

from copy import deepcopy
import difflib
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

import httpx
from pydantic import ValidationError

from raac.common.memory_viewer import MemoryFeed
from raac.common.tracing import JSONLTrace
from raac.common.working_memory import WorkingMemoryNotes
from raac.harness.demo_agent import DemoAgentRunner
from raac.harness.layer2_core.memory.short_term import ShortTermMemory, ShortTermMemoryPolicy, action_receipt
from raac.harness.layer3_execution.tool_orchestrator import ToolOrchestrator
from raac.harness.layer3_execution.tools.workspace import WorkspaceTools
from raac.llm.client import LLMClient
from raac.llm.config import LLMConfig
from raac.registry.loader import load_registry

ROOT = Path(__file__).resolve().parents[3]
NOTES = {"conclusions": [{"statement": "app.py exists; the current value is 1", "evidence": ["app.py:1"]}],
         "ruled_out": [{"hypothesis": "The file is missing", "reason": "read_file returned its contents", "evidence": ["app.py:1"]}],
         "next_steps": ["Change value to 2, then verify the saved file"]}


class WorkingMemoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.log = self.root / "llm.jsonl"
        self.trace = JSONLTrace(self.log)
        self.identity = {"run_id": "notes-run", "work_id": "work", "agent_id": "coder"}
        self.trace.emit(self.identity, event="run_started")
        self.memory = ShortTermMemory(self.root / "memory", self.identity,
            ShortTermMemoryPolicy(max_tokens=1024, target_tokens=700), redact=self.trace.redact)

    def save(self, kind, **data):
        metrics = self.memory.append(kind, round_number=1,
            snapshot_transform=action_receipt if kind != "working_notes" else None, **data)
        self.trace.emit(self.identity, event="memory_saved", **metrics)
        if metrics["compacted"]:
            self.trace.emit(self.identity, event="memory_compacted", memory=self.memory.view(), **metrics)
        return metrics

    def test_notes_replace_previous_state_and_remain_first_after_compaction(self):
        self.save("working_notes", notes=NOTES)
        updated = {**NOTES, "next_steps": ["Inspect the saved value before another edit"]}
        self.save("working_notes", notes=updated)
        for index in range(40):
            self.save("tool", tool="run_command", arguments={"command": ["python", "--version"]},
                      observation={"ok": True, "result": {"exit_code": 0, "summary": "observation " + str(index)}})
        self.assertGreater(self.memory.compactions, 0)
        notes = [entry for entry in self.memory.entries if entry["kind"] == "working_notes"]
        self.assertEqual(len(notes), 1)
        self.assertEqual(self.memory.entries[0]["notes"], updated)
        journal = self.memory.journal_path.read_text()
        self.assertIn(NOTES["next_steps"][0], journal)
        before = self.memory.path.read_bytes()
        self.memory.view()
        self.assertEqual(before, self.memory.path.read_bytes())
        cleared = {"conclusions": [], "ruled_out": [], "next_steps": []}
        self.save("working_notes", notes=cleared)
        self.assertEqual(self.memory.entries[0]["notes"], cleared)

    def test_snapshot_uses_receipts_while_journal_retains_full_observations(self):
        text = "source-observation-marker\n" * 2000
        metrics = self.save("tool", tool="read_file", arguments={"path": "app.py"},
            observation={"ok": True, "result": {"path": "app.py", "content": text}})
        self.assertFalse(metrics["compacted"])
        self.assertNotIn("source-observation-marker", self.memory.path.read_text())
        self.assertIn("source-observation-marker", self.memory.journal_path.read_text())
        self.assertIn("content_sha256", self.memory.path.read_text())
        self.assertIn("journal_sequence:1", self.memory.path.read_text())
        self.assertLess(self.memory.path.stat().st_size, self.memory.journal_path.stat().st_size // 20)

    def test_viewer_replays_receipts_and_note_replacement_exactly(self):
        expected = [[]]
        self.save("tool", tool="read_file", arguments={"path": "app.py"},
                  observation={"ok": True, "result": {"content": "source " * 10000}})
        expected.append(deepcopy(self.memory.entries))
        self.save("working_notes", notes=NOTES)
        expected.append(deepcopy(self.memory.entries))
        self.save("working_notes", notes={**NOTES, "next_steps": ["Run the regression"]})
        expected.append(deepcopy(self.memory.entries))
        feed = MemoryFeed(self.log)
        before = {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        for revision in range(1, 4):
            change = feed.change(self.memory.path.stem, revision)
            self.assertTrue(change["available"])
            rebuilt = {(e["kind"], e["sequence"]): e for e in expected[revision - 1]}
            for entry in change["removed"]:
                del rebuilt[(entry["kind"], entry["sequence"])]
            for item in change["updated"]:
                entry = item["after"];rebuilt[(entry["kind"], entry["sequence"])] = entry
            for entry in change["added"]:
                rebuilt[(entry["kind"], entry["sequence"])] = entry
            self.assertEqual(rebuilt, {(e["kind"], e["sequence"]): e for e in expected[revision]})
        self.assertEqual(before, {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()})

    def test_history_replays_receipts_without_trace_events_and_reads_legacy_v1(self):
        self.memory.append("tool", round_number=1, snapshot_transform=action_receipt,
            tool="read_file", arguments={"path": "app.py"}, observation={"ok": True, "result": {"content": "source " * 4000}})
        expected = deepcopy(self.memory.entries)
        self.memory.append("working_notes", round_number=1, notes=NOTES)
        change = MemoryFeed(self.log).change(self.memory.path.stem, 2)
        self.assertTrue(change["available"])
        self.assertEqual(change["before"]["entries"], len(expected))
        self.assertEqual(change["updated"], [])
        self.assertEqual(change["added"][0]["notes"], NOTES)
        # Existing v1 files remain readable and are upgraded only on a write.
        document = json.loads(self.memory.path.read_text())
        document["version"] = "short-term-v1"
        self.memory.path.write_text(json.dumps(document), encoding="utf-8")
        before = self.memory.path.read_bytes()
        reopened = ShortTermMemory(self.root / "memory", self.identity, self.memory.policy, redact=self.trace.redact)
        self.assertEqual(reopened.entries, self.memory.entries)
        self.assertEqual(self.memory.path.read_bytes(), before)

    def test_notes_require_evidence_and_a_small_complete_state(self):
        self.assertEqual(WorkingMemoryNotes.model_validate(NOTES).model_dump(), NOTES)
        for value in ({"conclusions": []}, {**NOTES, "conclusions": [{"statement": "unverified", "evidence": []}]},
                      {**NOTES, "next_steps": ["x" * 241]},
                      {**NOTES, "conclusions": [{"statement": "x" * 240, "evidence": ["e" * 120, "f" * 120]}] * 6}):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                WorkingMemoryNotes.model_validate(value)


class WorkingMemoryRunnerTests(unittest.IsolatedAsyncioTestCase):
    async def test_model_updates_are_visible_in_next_call_and_retry_with_no_source_dump(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "project"
            (project / "repo").mkdir(parents=True)
            (project / "requirement").mkdir()
            (project / "requirement/user_chat.txt").write_text("Set value to 2")
            (project / "repo/app.py").write_text("value = 1\n")
            workspace = WorkspaceTools(project, SimpleNamespace())
            tools = ToolOrchestrator(workspace)
            registry = load_registry(ROOT / "registry")
            patch = "".join(difflib.unified_diff(["value = 1\n"], ["value = 2\n"], fromfile="a/app.py", tofile="b/app.py"))
            def tool(name, arguments):
                return {"role": "assistant", "content": None, "tool_calls": [{"id": name, "type": "function",
                        "function": {"name": name, "arguments": json.dumps(arguments)}}]}
            sequence = [tool("read_file", {"path": "app.py"}), tool("update_memory", NOTES),
                        tool("replace_text", {"path": "app.py", "old_text": "value = 1", "new_text": "value = 2"}),
                        {"role": "assistant", "content": patch}, {"role": "assistant", "content": patch}]
            payloads = []
            def handler(request):
                payloads.append(json.loads(request.content))
                message = sequence.pop(0)
                return httpx.Response(200, json={"model": "mock", "choices": [{"finish_reason": "tool_calls" if message.get("tool_calls") else "stop", "message": message}],
                                               "usage": {"prompt_tokens": 10, "completion_tokens": 5}})
            client = LLMClient(LLMConfig(model="mock", profiles={key: {"model": "mock"} for key in registry.roles}), log_path=root / "llm.jsonl", transport=httpx.MockTransport(handler), trace_content=True)
            request = {"run_id": "notes-run", "task_id": "task", "work_id": "task", "scope": "task", "round": 1,
                       "baseline_ref": workspace.baseline_ref, "output_schema": {}}
            try:
                runner = DemoAgentRunner(registry, client, tools, provenance={})
                first = await runner("Coder", request)
                second = await DemoAgentRunner(registry, client, tools, provenance={})("Coder", {**request, "round": 2})
            finally:
                await client.aclose()
            self.assertEqual(first.output, second.output)
            for index in (2, 3, 4):
                memory = json.loads(payloads[index]["messages"][1]["content"])["role_memory"]
                self.assertEqual(memory["entries"][0]["kind"], "working_notes")
                self.assertEqual(memory["entries"][0]["notes"], NOTES)
            current = json.loads(payloads[-1]["messages"][1]["content"])["role_memory"]
            read = next(e for e in current["entries"] if e.get("tool") == "read_file")
            self.assertNotIn("content", read["observation"]["result"])
            self.assertEqual(tools._memory_writers, {})
            for context in ({"run_id": "notes-run", "agent_call_id": "old"}, {"run_id": "other", "agent_call_id": "old"}):
                denied = await tools.execute(registry.get("Coder"), "update_memory", NOTES, context=context)
                self.assertEqual(denied["error_kind"], "MemoryUnavailable")
            tester = registry.get("Tester")
            denied = await tools.execute(tester, "update_memory", NOTES, context={})
            self.assertEqual(denied["error_kind"], "ToolNotAllowed")
