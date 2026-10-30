"""Readable audit output hides repeated metadata and preserves the original log."""

import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from raac import mas_cli as cli
from raac.common.trace_viewer import TraceViewer, show_trace


class TraceViewerTests(unittest.TestCase):
    def test_cli_accepts_benchmark_id_for_terminal_and_web(self):
        run_id = "benchmark-3dc0162bbfff472f91c6ba7affae9235"
        for flags, target in [(["--follow", "--details"], "raac.common.trace_viewer.show_trace"),
                              (["--web", "--no-open"], "raac.common.trace_web.serve_trace")]:
            with self.subTest(flags=flags), patch(target) as viewer, \
                    patch.object(cli, "execute") as execute, patch("sys.stdout", io.StringIO()):
                self.assertEqual(cli.main(["show-log", "--run-id", run_id, *flags]), 0)
                self.assertEqual(viewer.call_args.args[0].parts[-3:], ("results", run_id, "llm.jsonl"))
                execute.assert_not_called()

    def test_cli_rejects_paths_as_run_ids(self):
        for run_id in ["benchmark-../outside", "benchmark-..\\outside", "../chain-demo", "other-demo"]:
            with self.subTest(run_id=run_id), patch("sys.stderr", io.StringIO()), \
                    patch("sys.stdout", io.StringIO()), self.assertRaises(SystemExit) as error:
                cli.main(["show-log", "--run-id", run_id])
            self.assertEqual(error.exception.code, 2)

    def test_unknown_usage_explains_why_transport_request_is_not_retried(self):
        rows = TraceViewer().render({"event": "llm_call", "agent_id": "planner",
            "status": None, "usage": None, "error": "ReadTimeout", "retry_scheduled": False,
            "retry_blocked_reason": "usage_unavailable"})
        self.assertIn("Không gọi lại", rows[0])
        self.assertIn("usage chưa xác định", rows[0])

    def test_run_totals_override_a_partial_pasted_log(self):
        viewer = TraceViewer()
        viewer.render({"event": "llm_call", "usage": {"prompt_tokens": 100, "completion_tokens": 10}})
        output = viewer.render({"event": "run_completed", "status": "aborted", "stop_reason": "budget_exceeded",
                                "usage": {"total_tokens": 203935, "calls": 21, "usage_complete": True}})
        self.assertIn("203,935", output[0])
        self.assertIn("21 HTTP attempt", output[0])

    def test_timeline_has_local_time_tool_outcome_and_budget_without_metadata_dump(self):
        viewer = TraceViewer()
        rows = [
            {"event": "agent_started", "timestamp": "2026-10-18T06:05:08+00:00", "agent_id": "coder",
             "provenance": {"effective_config": {"token_limit": 50000}, "framework_source_hash": "hidden-hash"},
             "request": {"incoming_message": {"from": "Planner"}}},
            {"event": "tool_call_started", "agent_id": "coder", "tool_name": "edit_file",
             "arguments": {"path": "solution.py", "content": "DO NOT DUMP THIS CODE"}},
            {"event": "llm_call", "agent_id": "tester", "usage": {"prompt_tokens": 44869, "completion_tokens": 5258}},
            {"event": "agent_failed", "agent_id": "tester", "code": "budget_exceeded"},
            {"event": "run_completed", "status": "aborted", "stop_reason": "budget_exceeded"},
        ]
        rendered = "\n".join(line for row in rows for line in viewer.render(row))
        self.assertIn("13:05:08", rendered)
        self.assertIn("Planner", rendered)
        self.assertIn("edit_file(path=solution.py", rendered)
        self.assertIn("50,127/50,000", rendered)
        self.assertNotIn("hidden-hash", rendered)
        self.assertNotIn("DO NOT DUMP THIS CODE", rendered)

    def test_details_and_filter(self):
        row = {"event": "llm_response", "agent_id": "coder", "message": {
            "content": json.dumps({"summary": "Saved", "notes": "line one\nline two"})}}
        output = "\n".join(TraceViewer(details=True).render(row))
        self.assertIn("line one\n", output)
        self.assertIn("line two", output)
        self.assertEqual(TraceViewer(agent="Tester").render(row), [])

    def test_tool_request_names_are_readable_and_malformed_fields_do_not_crash(self):
        viewer = TraceViewer()
        row = {"event": "llm_response", "message": {"tool_calls": [
            {"function": {"name": "run_tests"}}]}}
        self.assertIn("run_tests", viewer.render(row)[0])
        self.assertTrue(viewer.render({"event": "tool_call_started", "arguments": []}))
        self.assertEqual(viewer.render({"event": []}), [])

    def test_reader_skips_invalid_lines_redacts_and_does_not_change_input(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "llm.jsonl"
            original = "not-json\n" + json.dumps({"event": "agent_completed", "agent_id": "coder",
                "output": {"summary": "OK", "password": "do-not-print"}}) + "\n"
            path.write_text(original)
            lines = []
            show_trace(path, details=True, emit=lines.append)
            self.assertIn("JSON không hợp lệ", "\n".join(lines))
            self.assertNotIn("do-not-print", "\n".join(lines))
            self.assertEqual(path.read_text(), original)
            self.assertEqual(len(list(Path(directory).iterdir())), 1)

    def test_show_log_avoids_llm_execution_and_project_argument(self):
        with patch("sys.argv", ["raac", "show-log", "--run-id", "chain-demo"]), \
                patch("raac.common.trace_viewer.show_trace") as show, \
                patch.object(cli, "execute") as execute, patch("sys.stdout", io.StringIO()):
            self.assertEqual(cli.main(), 0)
        execute.assert_not_called()
        self.assertEqual(show.call_args.args[0].name, "llm.jsonl")

    def test_follow_waits_for_complete_line_and_stops_at_run_end(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "llm.jsonl"
            path.write_text('{"event":"run_completed","status":', encoding="utf-8")
            lines = []

            def finish_line(_):
                with path.open("a", encoding="utf-8") as stream:
                    stream.write('"completed","stop_reason":"tests_and_review_passed"}\n')

            with patch("raac.common.trace_viewer.time.sleep", side_effect=finish_line) as sleep:
                show_trace(path, follow=True, emit=lines.append)
            sleep.assert_called_once()
            self.assertIn("KẾT THÚC: completed", "\n".join(lines))
