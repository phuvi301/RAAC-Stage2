"""Memory viewers read only the selected run and preserve snapshots/journal bytes."""

from contextlib import contextmanager
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import urlopen

from raac import mas_cli
from raac.common.memory_viewer import MemoryFeed
from raac.common.trace_viewer import TraceViewer, show_trace
from raac.common.trace_web import create_trace_server, present_event
from raac.common.tracing import JSONLTrace
from raac.harness.layer2_core.memory.short_term import ShortTermMemory, ShortTermMemoryPolicy


@contextmanager
def server_for(path):
    server = create_trace_server(path, port=0)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=3)


class MemoryViewerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.log = self.root / "llm.jsonl"
        self.log.write_text(json.dumps({"event": "run_started", "run_id": "run-one"}) + "\n", encoding="utf-8")
        self.identity = {"run_id": "run-one", "work_id": "work", "agent_id": "coder", "baseline_ref": "base"}
        self.memory = ShortTermMemory(self.root / "memory", self.identity,
            ShortTermMemoryPolicy(max_tokens=1024, target_tokens=700), redact=JSONLTrace(self.log).redact)
        self.feed = MemoryFeed(self.log)

    def append(self, count=1):
        for _ in range(count):
            self.memory.append("tool", round_number=1, tool="edit_file", arguments={"path": "app.py"},
                               observation={"ok": True, "result": {"summary": "saved-memory-marker"}})

    def original_bytes(self):
        return {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}

    def test_snapshot_and_paged_journal_are_read_only(self):
        self.append(55)
        before = self.original_bytes()
        snapshots = self.feed.snapshots()["memories"]
        self.assertEqual(len(snapshots), 1)
        saved = snapshots[0]
        self.assertEqual(saved["revision"], 55)
        self.assertGreater(saved["compactions"], 0)
        self.assertTrue(saved["journal_available"])
        first = self.feed.journal(saved["id"])
        self.assertEqual(len(first["entries"]), 50)
        self.assertTrue(first["has_more"])
        second = self.feed.journal(saved["id"], first["cursor"])
        self.assertEqual([e["sequence"] for e in second["entries"]], [51, 52, 53, 54, 55])
        self.assertFalse(second["has_more"])
        self.assertEqual(self.original_bytes(), before)

    def test_journal_waits_for_partial_lines_and_resets_after_truncation(self):
        self.append()
        identifier = self.feed.snapshots()["memories"][0]["id"]
        first = self.feed.journal(identifier)
        record = json.dumps({"identity": self.identity, "entry": {"kind": "handoff", "sequence": 2}})
        with self.memory.journal_path.open("a", encoding="utf-8") as stream:
            stream.write(record[:20])
        pending = self.feed.journal(identifier, first["cursor"])
        self.assertEqual(pending["entries"], [])
        self.assertEqual(pending["cursor"], first["cursor"])
        with self.memory.journal_path.open("a", encoding="utf-8") as stream:
            stream.write(record[20:] + "\n")
        last = self.feed.journal(identifier, pending["cursor"])
        self.assertEqual(last["entries"][0]["kind"], "handoff")
        self.memory.journal_path.write_text(record + "\n", encoding="utf-8")
        reset = self.feed.journal(identifier, last["cursor"])
        self.assertTrue(reset["reset"])
        self.assertEqual(reset["entries"][0]["sequence"], 2)

    def test_scope_filter_and_path_validation(self):
        self.append()
        other = ShortTermMemory(self.root / "memory", {**self.identity, "run_id": "other-run"},
            self.memory.policy, redact=JSONLTrace(self.log).redact)
        other.append("assistant", round_number=1, content="other-run-marker")
        saved = self.feed.snapshots()
        self.assertEqual(len(saved["memories"]), 1)
        self.assertNotIn("other-run-marker", json.dumps(saved))
        for value in ("../llm", "coder-../../outside", "coder-x", str(self.log)):
            with self.subTest(identifier=value), self.assertRaises(ValueError):
                self.feed.journal(value)
        identifier = saved["memories"][0]["id"]
        with self.assertRaises(ValueError):
            self.feed.journal(identifier, -1)
        self.feed.directory = self.root.parent
        self.assertEqual(self.feed.snapshots()["memories"], [])

    def test_old_log_without_memory_is_supported(self):
        before = self.original_bytes()
        self.assertEqual(self.feed.snapshots(), {"memories": [], "warnings": []})
        self.assertEqual(self.original_bytes(), before)
        lines = []
        show_trace(self.log, memory=True, emit=lines.append)
        self.assertIn("chưa có memory", "\n".join(lines))

    def test_memory_events_have_clear_timeline_payloads_and_keep_role_call(self):
        for kind in ("memory_saved", "memory_loaded", "memory_compacted"):
            event = {"event": kind, "agent_id": "coder", "agent_call_id": "coder-round-two", "round": 2,
                     "revision": 8, "before_estimated_tokens": 7000, "after_estimated_tokens": 3900,
                     "entry": {"kind": "tool", "tool": "read_file"}, "memory": {"entries": [{"kind": "summary"}]}}
            row = present_event(event, 1)
            self.assertEqual(row["agent_call_id"], "coder-round-two")
            self.assertEqual(row["round"], 2)
            self.assertIn("memory", row["title"].lower())
            self.assertGreaterEqual(len(row["payloads"]), 2)
            terminal = "\n".join(TraceViewer(details=True).render(event))
            self.assertIn("MEMORY", terminal)
            self.assertIn("read_file", terminal)
            self.assertEqual(TraceViewer(agent="tester").render(event), [])

    def test_web_api_serves_memory_and_redacts_untrusted_sidecar_data(self):
        self.append()
        saved = json.loads(self.memory.path.read_text())
        saved["entries"][0]["password"] = "sidecar-secret-marker"
        saved["entries"][0]["content"] = "<img src=x onerror=alert(1)>"
        self.memory.path.write_text(json.dumps(saved), encoding="utf-8")
        before = self.original_bytes()
        with server_for(self.log) as url:
            with urlopen(url + "/api/memory", timeout=3) as response:
                payload = json.load(response)
            rendered = json.dumps(payload)
            self.assertNotIn("sidecar-secret-marker", rendered)
            self.assertIn("REDACTED", rendered)
            self.assertIn("<img src=x onerror=alert(1)>", rendered)
            identifier = payload["memories"][0]["id"]
            with urlopen(url + "/api/memory?id=" + identifier, timeout=3) as response:
                self.assertEqual(len(json.load(response)["entries"]), 1)
            with urlopen(url + "/memory.mjs", timeout=3) as response:
                self.assertIn("text/javascript", response.headers["Content-Type"])
            for target in ("/api/memory?id=..%2Fllm", "/api/memory?id=" + identifier + "&cursor=-1"):
                with self.assertRaises(HTTPError) as error:
                    urlopen(url + target, timeout=3)
                self.assertEqual(error.exception.code, 400)
                error.exception.close()
        self.assertEqual(self.original_bytes(), before)

    def test_terminal_memory_flag_forwards_without_running_mas(self):
        with patch("raac.common.trace_viewer.show_trace") as show, patch.object(mas_cli, "execute") as execute, \
             patch("sys.stdout", io.StringIO()):
            self.assertEqual(mas_cli.main(["show-log", "--log", str(self.log), "--memory", "--details"]), 0)
        self.assertTrue(show.call_args.kwargs["memory"])
        execute.assert_not_called()

    def test_terminal_sidecar_view_includes_details_and_preserves_bytes(self):
        self.append()
        before = self.original_bytes()
        lines = []
        show_trace(self.log, memory=True, details=True, emit=lines.append)
        rendered = "\n".join(lines)
        self.assertIn("MEMORY CODER", rendered)
        self.assertIn("saved-memory-marker", rendered)
        self.assertIn("edit_file", rendered)
        self.assertEqual(self.original_bytes(), before)

    def append_traced(self, count=1, content="small observation"):
        trace = JSONLTrace(self.log)
        for _ in range(count):
            metrics = self.memory.append("tool", round_number=1, tool="read_file", arguments={"path": "app.py"},
                                         observation={"ok": True, "result": {"content": content}})
            trace.emit(self.identity, event="memory_saved", **metrics)
            if metrics["compacted"]:
                trace.emit(self.identity, event="memory_compacted", memory=self.memory.view(), **metrics)

    def test_history_and_change_reconstruct_append_then_actual_compaction_without_writes(self):
        self.append_traced(3)
        previous = self.memory.view()["entries"]
        self.append_traced(content="large source fragment " * 1000)
        after = self.memory.view()["entries"]
        original = self.original_bytes()
        identifier = self.memory.path.stem
        rows = self.feed.history(identifier)
        self.assertEqual([row["revision"] for row in rows["revisions"]], [4, 3, 2, 1])
        self.assertTrue(rows["revisions"][0]["compacted"])
        first = self.feed.change(identifier, 1)
        self.assertTrue(first["available"])
        self.assertEqual(first["before"]["entries"], 0)
        self.assertEqual(first["added"][0]["sequence"], 1)
        change = self.feed.change(identifier, 4)
        self.assertTrue(change["available"])
        self.assertTrue(change["metadata"]["compacted"])
        rebuilt = {(e["kind"], e["sequence"]): e for e in previous}
        for entry in change["removed"]:
            del rebuilt[(entry["kind"], entry["sequence"])]
        for update in change["updated"]:
            entry = update["after"]
            rebuilt[(entry["kind"], entry["sequence"])] = entry
        for entry in change["added"]:
            rebuilt[(entry["kind"], entry["sequence"])] = entry
        self.assertEqual(rebuilt, {(e["kind"], e["sequence"]): e for e in after})
        self.assertEqual(self.original_bytes(), original)

    def test_history_paginates_and_change_works_for_old_run_without_compaction_events(self):
        # Large policy avoids compacting; old journals still have exact history.
        self.memory = ShortTermMemory(self.root / "memory", self.identity,
            ShortTermMemoryPolicy(max_tokens=64000, target_tokens=50000), redact=JSONLTrace(self.log).redact)
        self.append(55)
        identifier = self.memory.path.stem
        first = self.feed.history(identifier)
        self.assertEqual(len(first["revisions"]), 50)
        self.assertTrue(first["has_more"])
        second = self.feed.history(identifier, first["cursor"])
        self.assertEqual([r["revision"] for r in second["revisions"]], [5, 4, 3, 2, 1])
        self.assertFalse(second["has_more"])
        change = self.feed.change(identifier, 25)
        self.assertTrue(change["available"])
        self.assertEqual([e["sequence"] for e in change["added"]], [25])
        self.assertEqual(change["removed"], [])
        self.assertEqual(change["updated"], [])

    def test_missing_compaction_snapshot_is_not_presented_as_exact_history(self):
        self.append(55)
        identifier = self.memory.path.stem
        change = self.feed.change(identifier, 54)
        self.assertFalse(change["available"])
        self.assertIn("không thể so sánh chính xác", " ".join(change["warnings"]))

    def test_history_http_redacts_and_rejects_invalid_scope_revision_and_paths(self):
        self.append_traced(content="<img src=x onerror=alert(1)>")
        identifier = self.memory.path.stem
        saved = json.loads(self.memory.path.read_text(encoding="utf-8"))
        saved["entries"][0]["api_key"] = "history-secret-marker"
        self.memory.path.write_text(json.dumps(saved), encoding="utf-8")
        before = self.original_bytes()
        with server_for(self.log) as url:
            with urlopen(url + "/api/memory/history?id=" + identifier, timeout=3) as response:
                rows = json.load(response)
                self.assertEqual(rows["revisions"][0]["revision"], 1)
            with urlopen(url + "/api/memory/change?id=" + identifier + "&revision=1", timeout=3) as response:
                change = json.load(response)
                self.assertTrue(change["available"])
                self.assertIn("<img", json.dumps(change))
                self.assertNotIn("history-secret-marker", json.dumps(change))
                self.assertIn("REDACTED", json.dumps(change))
            for target in ("/api/memory/history?id=..%2Fllm", "/api/memory/history?id=" + identifier + "&cursor=-1",
                           "/api/memory/change?id=" + identifier + "&revision=999", "/api/memory/change?id=" + identifier + "&revision=0"):
                with self.assertRaises(HTTPError) as error:
                    urlopen(url + target, timeout=3)
                self.assertEqual(error.exception.code, 400)
                error.exception.close()
        self.assertEqual(self.original_bytes(), before)

    def test_compaction_changes_existing_entry_into_a_bounded_preview(self):
        self.append_traced(content="kept original source " * 85)
        before = self.memory.view()["entries"][0]
        self.assertEqual(self.memory.compactions, 0)
        self.append_traced(content="new source " * 1200)
        change = self.feed.change(self.memory.path.stem, 2)
        self.assertTrue(change["available"])
        self.assertTrue(change["metadata"]["compacted"])
        updated = next(item for item in change["updated"] if item["after"]["sequence"] == 1)
        self.assertEqual(updated["before"], before)
        self.assertIn("preview", json.dumps(updated["after"]))
