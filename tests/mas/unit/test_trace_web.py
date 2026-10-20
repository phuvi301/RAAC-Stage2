"""The visual viewer presents actual traffic and never executes a traced action."""

from contextlib import contextmanager
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from raac import mas_cli as cli
from raac.common.trace_web import TraceFeed, create_trace_server, present_event


@contextmanager
def running_server(path):
    server = create_trace_server(path, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


class TraceWebTests(unittest.TestCase):
    def test_transport_failure_exposes_retry_blocked_by_unknown_usage(self):
        row = present_event({"event": "llm_call", "agent_id": "planner", "error": "ReadTimeout",
            "status": None, "usage": None, "retry_scheduled": False,
            "retry_blocked_reason": "usage_unavailable"}, 0)
        self.assertIn("Không gọi lại", row["summary"])
        self.assertEqual(row["tone"], "error")
        self.assertEqual(row["payloads"][0]["data"]["retry_blocked_reason"], "usage_unavailable")

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / "llm.jsonl"

    def write(self, *events):
        self.path.write_text("".join(json.dumps(event, ensure_ascii=False) + "\n" for event in events), encoding="utf-8")

    def test_directions_and_call_id_connect_agent_request_to_system_response(self):
        context = {"agent_id": "coder", "agent_call_id": "role-call-1", "llm_call_id": "llm-3",
                   "tool_name": "read_file", "tool_call_id": "same-call", "round": 2, "turn": 3}
        request = present_event({**context, "event": "tool_call_started", "arguments": {"path": "src/app.py", "start_line": 1, "end_line": 50}}, 0)
        response = present_event({**context, "event": "tool_call_completed", "observation": {"ok": True, "result": {"path": "src/app.py", "content": "source"}}}, 100)
        self.assertEqual((request["source"], request["target"]), ("Coder", "Hệ thống"))
        self.assertEqual((response["source"], response["target"]), ("Hệ thống", "Coder"))
        self.assertEqual(request["tool_call_id"], response["tool_call_id"])
        self.assertEqual(request["llm_call_id"], response["llm_call_id"])
        self.assertEqual(request["agent_call_id"], "role-call-1")
        self.assertIn("dòng 1–50", request["summary"])
        self.assertEqual(response["payloads"][0]["data"]["result"]["content"], "source")

    def test_tool_success_does_not_disguise_failed_test_or_nonzero_exit_as_pass(self):
        for result in ({"status": "failed", "tests_run": 2, "failed": 1}, {"exit_code": 1}, {"execution": {"exit_code": 2}}):
            row = present_event({"event": "tool_call_completed", "tool_name": "run_tests", "observation": {"ok": True, "result": result}}, 1)
            self.assertEqual(row["tone"], "error")
        row = present_event({"event": "tool_call_completed", "observation": {"ok": True, "result": {"tests_run": 2, "failed": 0, "status": "passed"}}}, 2)
        self.assertEqual(row["tone"], "success")

    def test_adapter_validation_is_labeled_as_system_action(self):
        row = present_event({"event": "tool_call_started", "agent_id": "tester", "origin": "adapter_validation", "tool_name": "run_tests"}, 0)
        self.assertEqual(row["source"], "Hệ thống")
        self.assertIn("Tự kiểm chứng", row["title"])

    def test_natural_language_agent_messages_are_visible_and_report_copies_are_optional(self):
        row = present_event({"event": "llm_response", "agent_id": "coder", "message": {
            "content": "I found the failing boundary case; I will update the implementation."}}, 0)
        self.assertFalse(row["technical"])
        self.assertIn("boundary case", row["summary"])
        report = present_event({"event": "llm_response", "message": {"content": '{"summary":"saved"}'}}, 1)
        self.assertTrue(report["technical"])

    def test_handoff_and_actual_llm_context_remain_available_without_duplicate_metadata(self):
        request = {"requirement": "AC: exact contract", "incoming_message": {"from": "Planner"}}
        row = present_event({"event": "agent_started", "agent_id": "coder", "request": request}, 0)
        self.assertEqual(row["source"], "Planner")
        self.assertEqual(row["payloads"][0]["data"], request)
        payload = {"messages": [{"role": "system", "content": "actual role prompt"}], "tools": []}
        row = present_event({"event": "llm_request", "request": payload, "provenance": {"framework_source_hash": "metadata-only"}}, 1)
        self.assertTrue(row["technical"])
        self.assertEqual(row["payloads"][0]["data"], payload)
        self.assertNotIn("metadata-only", json.dumps(row))

    def test_read_incrementally_waits_for_partial_line_and_preserves_source(self):
        self.write({"event": "agent_started", "agent_id": "coder"})
        feed = TraceFeed(self.path)
        first = feed.read()
        with self.path.open("ab") as stream:
            stream.write(b'{"event":"agent_completed","output":')
        pending = feed.read(first["cursor"])
        self.assertEqual(pending["events"], [])
        self.assertEqual(pending["cursor"], first["cursor"])
        with self.path.open("ab") as stream:
            stream.write(b'{"summary":"saved"}}\n')
        original = self.path.read_bytes()
        last = feed.read(pending["cursor"])
        self.assertEqual(last["events"][0]["summary"], "saved")
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_feed_paginates_skips_bad_lines_and_resets_after_truncation(self):
        self.write(*({"event": "llm_call", "usage": {"prompt_tokens": i, "completion_tokens": 1}} for i in range(56)))
        feed = TraceFeed(self.path)
        first = feed.read()
        self.assertEqual(len(first["events"]), 50)
        self.assertTrue(first["has_more"])
        last = feed.read(first["cursor"])
        self.assertEqual(len(last["events"]), 6)
        self.assertFalse(last["has_more"])
        self.path.write_text('invalid\n[]\n{"event":"run_completed","status":"aborted"}', encoding="utf-8")
        reset = feed.read(last["cursor"])
        self.assertTrue(reset["reset"])
        self.assertEqual(len(reset["warnings"]), 2)
        self.assertEqual(reset["events"][0]["status"], "aborted")

    def test_feed_redacts_nested_tool_secrets_and_preserves_literal_html(self):
        self.write({"event": "llm_request", "request": {"messages": [{"role": "user", "content": json.dumps({"password": "nested-secret", "text": "<img src=x onerror=alert(1)>"})}], "authorization": "Bearer private-key"}})
        data = TraceFeed(self.path).read()
        serialized = json.dumps(data)
        self.assertNotIn("nested-secret", serialized)
        self.assertNotIn("private-key", serialized)
        self.assertIn("[REDACTED]", serialized)
        self.assertIn("<img src=x onerror=alert(1)>", serialized)

    def test_http_serves_only_viewer_assets_and_redacted_feed(self):
        self.write({"event": "run_completed", "status": "aborted", "stop_reason": "budget_exceeded", "usage": {"calls": 22, "total_tokens": 201519}})
        original = self.path.read_bytes()
        with running_server(self.path) as url:
            for asset, content_type in (("/", "text/html"), ("/viewer.js", "text/javascript"), ("/usage.mjs", "text/javascript"), ("/viewer.css", "text/css"), ("/api/events", "application/json")):
                with urlopen(url + asset, timeout=3) as response:
                    self.assertIn(content_type, response.headers["Content-Type"])
                    self.assertIn("frame-ancestors 'none'", response.headers["Content-Security-Policy"])
            with urlopen(url + "/api/events", timeout=3) as response:
                result = json.load(response)
                self.assertEqual(result["events"][0]["usage"]["total_tokens"], 201519)
            for target, code in (("/api/events?cursor=-1", 400), ("/../llm.jsonl", 404), ("/unknown", 404)):
                with self.assertRaises(HTTPError) as error:
                    urlopen(url + target, timeout=3)
                self.assertEqual(error.exception.code, code)
                error.exception.close()
            with self.assertRaises(HTTPError) as error:
                urlopen(Request(url, headers={"Host": "unrelated.example"}), timeout=3)
            self.assertEqual(error.exception.code, 403)
            error.exception.close()
        self.assertEqual(self.path.read_bytes(), original)

    def test_cli_web_does_not_load_model_or_execute_mas(self):
        with patch("sys.argv", ["raac", "show-log", "--run-id", "chain-demo", "--web", "--no-open", "--port", "0", "--agent", "coder"]), \
                patch("raac.common.trace_web.serve_trace") as serve, \
                patch.object(cli, "execute") as execute, patch("sys.stdout", io.StringIO()):
            self.assertEqual(cli.main(), 0)
        execute.assert_not_called()
        self.assertEqual(serve.call_args.args[0].name, "llm.jsonl")
        self.assertEqual(serve.call_args.kwargs["port"], 0)
        self.assertFalse(serve.call_args.kwargs["open_browser"])
        self.assertEqual(serve.call_args.kwargs["agent"], "coder")
