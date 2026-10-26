"""Read-only, loopback web viewer for existing JSONL traces; no model calls."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
import webbrowser

from raac.collaboration.topologies import get_topology

from .trace_viewer import _mapping, _summary, _text
from .tracing import JSONLTrace
from .memory_viewer import MemoryFeed
from raac.llm.usage import cached_tokens


ASSETS = Path(__file__).with_name("trace_assets")
MAX_LINE_BYTES = 16 * 1024 * 1024
STOP_REASONS = {
    "budget_exceeded": "Vượt ngân sách token",
    "agent_call_limit": "Chạm giới hạn gọi LLM của role",
    "tests_and_review_passed": "Test đạt và Reviewer chấp thuận",
    "environment_setup_failed": "Môi trường chưa sẵn sàng; xem báo cáo Environment",
    "max_rounds_reached": "Đã đạt số vòng tối đa",
    "timeout": "Hết thời gian chạy",
}


def topology_payload(name):
    """Declared layout only (nodes and edges); progress is derived client-side from the log."""
    topology = get_topology(name)
    return {"topology_id": topology.topology_id, "version": topology.version,
            "entry_node": topology.entry_node, "exit_node": topology.exit_node,
            "steps": [{"node_id": step.node_id, "role": step.role} for step in topology.steps],
            "edges": [list(edge) for edge in topology.edges]}


def present_event(event, identifier):
    """Describe observed protocol traffic, without inventing agent reasoning."""
    kind = event.get("event")
    if not isinstance(kind, str):
        return None
    agent = str(event.get("agent_id") or event.get("role") or "MAS")
    role = agent.capitalize() if agent.lower() != "mas" else "MAS"
    row = {"id": str(identifier), "event": kind, "agent": agent.lower(),
           "timestamp": event.get("timestamp"), "round": event.get("round"), "turn": event.get("turn"),
           "source": "Hệ thống", "target": role, "title": kind, "summary": "",
           "tone": "info", "technical": False, "payloads": []}
    for name in ("run_id", "agent_call_id", "llm_call_id", "tool_call_id", "tool_name", "origin", "parent_tool_call_id", "batch_index", "batch_size"):
        if event.get(name) is not None:
            row[name] = event[name]

    def payload(label, data):
        if data is not None:
            row["payloads"].append({"label": label, "data": data})

    effective = _mapping(_mapping(event.get("provenance")).get("effective_config"))
    if type(effective.get("token_limit")) is int:
        row["token_limit"] = effective["token_limit"]
    if kind == "run_started":
        decision = _mapping(event.get("decision"))
        review = decision.get("max_review_rounds")
        row.update(source="Người dùng", target="MAS", title="Bắt đầu run", topology=decision.get("topology"),
                   max_rounds=decision.get("max_rounds"), max_review_rounds=review,
                   summary=f"Topology {decision.get('topology', '?')} · tối đa {decision.get('max_rounds', '?')} vòng"
                           + (f" · phản hồi Reviewer → Coder tối đa {review} vòng" if review is not None else ""))
        payload("Quyết định điều phối", decision)
        payload("Workspace", event.get("workspace"))
    elif kind == "command_output":
        row.update(source=role, target="Docker", title="Output " + str(event.get("stream", "")),
                   summary=_text(event.get("content", f"{event.get('bytes', 0)} bytes"), 280))
        payload("Output", event.get("content"))
    elif kind in {"workspace_changed", "candidate_exported", "benchmark_phase_started", "benchmark_phase_completed", "benchmark_phase_failed", "container_started", "container_removed"}:
        row.update(title=kind, summary=_text(event.get("phase") or event.get("paths") or event.get("patch_sha256") or event.get("container_id", ""), 280))
        payload("Chi tiết", {key: event[key] for key in ("phase", "paths", "patch_sha256", "container_id") if key in event})
    elif kind == "agent_started":
        request = _mapping(event.get("request"))
        sender = _mapping(request.get("incoming_message")).get("from", "Hệ thống")
        if str(sender).lower() in {"caller", "user"}:
            sender = "Người dùng"
        row.update(source=str(sender), title="Nhận nhiệm vụ", summary=_text(request.get("requirement", "Nhận dữ liệu bàn giao"), 280))
        payload("Nhiệm vụ và dữ liệu agent nhận", request)
    elif kind == "agent_completed":
        row.update(source=role, target="Hệ thống", title="Bàn giao kết quả", tone="success",
                   summary=_summary(event.get("output", {})))
        payload("Kết quả bàn giao", event.get("output"))
        if event.get("agent_id") == "environment":
            blocked = _mapping(event.get("output")).get("status") == "blocked"
            row.update(title="Môi trường chưa sẵn sàng" if blocked else "Môi trường đã xác minh",
                       tone="error" if blocked else "success")
            payload("Cấu hình và kiểm tra thực tế", event.get("runtime"))
    elif kind == "agent_failed":
        code = event.get("code") or event.get("error_kind")
        row.update(title="Agent dừng", tone="error", summary=STOP_REASONS.get(code, str(code or "Không rõ lỗi")))
        payload("Lỗi", {name: event[name] for name in ("code", "error_kind", "stack") if name in event})
    elif kind == "agent_final_call":
        row.update(title="Yêu cầu kết thúc lượt role", tone="warning",
                   summary="Lượt LLM cuối: trả báo cáo bàn giao, không gọi thêm tool")
    elif kind == "coder_patch_rebased":
        row.update(title="Chuẩn hóa đường dẫn patch", summary="Dùng Git worktree từ lệnh diff đã chạy để đưa đường dẫn về repo/.")
    elif kind == "coder_patch_rebuilt":
        row.update(title="Sinh lại patch từ file đã lưu", technical=False,
                   summary=("Diff mô tả code đã lưu; đã dùng patch chuẩn theo baseline run."
                            if event.get("reason") == "already_applied_diff" else
                            "Diff LLM sai định dạng; framework đã sinh và kiểm tra patch từ workspace."))
        payload("Nguồn patch", {key: event.get(key) for key in ("source", "reason", "patch_hash", "model_patch_chars", "patch_chars")})
    elif kind in {"memory_saved", "memory_loaded", "memory_compacted"}:
        titles = {"memory_saved": "Lưu memory Coder", "memory_loaded": "Nạp lại memory Coder",
                  "memory_compacted": "Compact memory đã lưu"}
        row.update(source="Hệ thống", target="Memory Coder", title=titles[kind], technical=kind == "memory_saved")
        if kind == "memory_compacted":
            row["summary"] = (f"{event.get('before_estimated_tokens', '?')} → {event.get('after_estimated_tokens', '?')} "
                              f"token ước lượng · revision {event.get('revision', '?')}")
        else:
            entry = _mapping(event.get("entry"))
            row["summary"] = f"Revision {event.get('revision', '?')}"
            if entry:
                row["summary"] += " · " + str(entry.get("tool") or entry.get("kind", ""))
            if event.get("after_estimated_tokens") is not None:
                row["summary"] += f" · ~{event['after_estimated_tokens']} token"
        payload("Thông tin memory", {key: event[key] for key in ("path", "revision", "compactions", "stored_entries") if key in event})
        payload("Hành động được ghi vào memory", event.get("entry"))
        payload("Memory được nạp" if kind == "memory_loaded" else "Memory sau compact", event.get("memory"))
    elif kind == "llm_request":
        request = _mapping(event.get("request"))
        row.update(title="Gửi context tới LLM", technical=True,
                   summary=f"{len(request.get('messages') or [])} message · {len(request.get('tools') or [])} tool khả dụng")
        payload("Request thực tế gửi LLM", request)
    elif kind == "llm_response":
        response = _mapping(event.get("message"))
        content = response.get("content")
        is_report = False
        if isinstance(content, str):
            try:
                is_report = isinstance(json.loads(content), (dict, list))
            except (ValueError, RecursionError):
                pass
        row.update(source=role, target="Hệ thống", title="Phản hồi LLM", technical=not bool(content) or is_report,
                   summary=_summary(response.get("content") or "Yêu cầu gọi tool"))
        if event.get("error"):
            row.update(tone="error", technical=False, summary=str(event["error"]))
        payload("Message LLM trả về", response)
    elif kind == "llm_attempt_started":
        row.update(title="Bắt đầu gọi LLM", technical=True, summary=f"HTTP attempt {event.get('attempt', 1)}")
    elif kind == "llm_call":
        usage = _mapping(event.get("usage"))
        row.update(title="Chi phí và thời gian LLM", technical=True, usage=usage,
                   summary=f"HTTP {event.get('status')} · {event.get('latency_ms', '?')} ms · token vào/ra {usage.get('prompt_tokens', '?')}/{usage.get('completion_tokens', '?')}")
        cached = cached_tokens(usage)
        row["summary"] += " · cache " + (f"{cached:,}" if cached is not None else "chưa có số liệu")
        if event.get("error"):
            row.update(title="Lỗi gọi LLM", tone="error", technical=False)
        if event.get("retry_blocked_reason") == "usage_unavailable":
            row["summary"] += " · Không gọi lại: usage của request chưa xác định"
        payload("HTTP và usage", {key: event[key] for key in ("status", "attempt", "latency_ms", "requested_model", "served_model", "usage", "error", "retry_scheduled", "retry_blocked_reason") if key in event})
    elif kind == "context_compacted":
        row.update(title="Rút gọn context", technical=True,
                   summary=f"{event.get('before_chars', '?')} → {event.get('after_chars', '?')} ký tự")
        payload("Thông số rút gọn", {key: value for key, value in event.items()
                if key.startswith("checkpoint_") or key in {"history_backend", "before_chars", "after_chars", "compacted_turns"}})
    elif kind == "tool_call_started":
        arguments = _mapping(event.get("arguments"))
        hints = []
        if "path" in arguments:
            hints.append(str(arguments["path"]))
        if "command" in arguments:
            hints.append(_text(arguments["command"], 200))
        if "query" in arguments:
            hints.append(_text(arguments["query"], 160))
        for key in ("files", "commands"):
            if isinstance(arguments.get(key), list):
                hints.append(f"{len(arguments[key])} {'file/vùng' if key == 'files' else 'command'}")
        if "start_line" in arguments or "end_line" in arguments:
            hints.append(f"dòng {arguments.get('start_line', 1)}–{arguments.get('end_line') or 'cuối file'}")
        row.update(source=role, target="Hệ thống", title="Gọi tool · " + str(event.get("tool_name", "?")), summary=" · ".join(hints))
        if event.get("origin") == "adapter_validation":
            row.update(source="Hệ thống", target="Tool", title="Tự kiểm chứng · " + str(event.get("tool_name", "?")))
        payload("Tham số gọi tool", arguments)
    elif kind == "tool_call_completed":
        observation = _mapping(event.get("observation"))
        result = _mapping(observation.get("result"))
        execution = _mapping(result.get("execution"))
        failed = (not observation.get("ok") or result.get("status") in {"failed", "error", "timeout", "timed_out", "output_limit", "output_limit_exceeded"}
                  or bool(result.get("failed")) or result.get("exit_code", 0) not in (None, 0)
                  or execution.get("exit_code", 0) not in (None, 0))
        row.update(title="Kết quả · " + str(event.get("tool_name", "?")), tone="error" if failed else "success",
                   summary=_summary(result) if observation.get("ok") else _text(observation.get("error") or observation.get("error_kind", "Tool lỗi")))
        row["latency_ms"] = event.get("latency_ms")
        payload("Kết quả hệ thống trả cho agent", observation)
    elif kind in {"tool_call_failed", "tool_arguments_invalid"}:
        row.update(title="Tool gặp lỗi · " + str(event.get("tool_name", "?")), tone="error",
                   summary=str(event.get("error_kind") or "Tham số JSON không hợp lệ"))
        payload("Lỗi tool", event.get("observation"))
    elif kind == "run_completed":
        reason = event.get("stop_reason")
        row.update(target="Người dùng", title="Kết thúc run", status=event.get("status"),
                   stop_reason=reason, usage=_mapping(event.get("usage")),
                   tone="success" if event.get("status") == "completed" else "error",
                   summary=f"{event.get('status', '?')} · {STOP_REASONS.get(reason, str(reason or 'Không có lý do dừng'))}")
        payload("Kết quả cuối run", {key: event.get(key) for key in ("status", "stop_reason", "usage", "error")})
    else:
        row.update(technical=True, title="Sự kiện · " + kind)
        payload("Dữ liệu sự kiện", {key: value for key, value in event.items() if key != "provenance"})
    return row


class TraceFeed:
    """Read complete appended records with a byte cursor, without modifying files."""

    def __init__(self, path):
        self.path = Path(path)
        self.redactor = JSONLTrace(self.path)

    def read(self, cursor=0):
        if type(cursor) is not int or cursor < 0:
            raise ValueError("Invalid cursor")
        size = self.path.stat().st_size
        reset = cursor > size
        cursor = 0 if reset else cursor
        events, warnings = [], []
        with self.path.open("rb") as stream:
            stream.seek(cursor)
            for _ in range(50):
                start = stream.tell()
                line = stream.readline(MAX_LINE_BYTES + 1)
                if not line:
                    break
                if len(line) > MAX_LINE_BYTES:
                    while line and not line.endswith(b"\n"):
                        line = stream.readline(MAX_LINE_BYTES)
                    warnings.append("Bỏ qua một dòng log quá 16 MiB; xem log gốc nếu cần.")
                    cursor = stream.tell()
                    continue
                if not line.strip():
                    cursor = stream.tell()
                    continue
                try:
                    event = json.loads(line.decode("utf-8-sig", errors="replace"))
                    if not isinstance(event, dict):
                        raise ValueError
                except (ValueError, RecursionError):
                    if not line.endswith(b"\n"):
                        break  # The writer may still be appending this record.
                    warnings.append(f"Bỏ qua dòng JSON không hợp lệ tại byte {start}.")
                else:
                    row = present_event(self.redactor.redact(event), start)
                    if row is not None:
                        events.append(row)
                cursor = stream.tell()
        return {"events": events, "cursor": cursor, "has_more": cursor < size and bool(line and line.endswith(b"\n")),
                "reset": reset, "warnings": warnings}


def create_trace_server(path, *, port=0, agent=None):
    feed = TraceFeed(path)
    memory_feed = MemoryFeed(path)
    with feed.path.open("rb"):
        pass  # Fail before opening the browser if the log is unavailable.

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def respond(self, status, body, content_type):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            port_number = self.server.server_address[1]
            hosts = {f"127.0.0.1:{port_number}", f"localhost:{port_number}"}
            if self.headers.get("Host") not in hosts:
                self.respond(403, b"Forbidden host", "text/plain")
                return
            request = urlsplit(self.path)
            assets = {"/": ("index.html", "text/html; charset=utf-8"),
                      "/viewer.css": ("viewer.css", "text/css; charset=utf-8"),
                      "/usage.mjs": ("usage.mjs", "text/javascript; charset=utf-8"),
                      "/memory.mjs": ("memory.mjs", "text/javascript; charset=utf-8"),
                      "/graph.mjs": ("graph.mjs", "text/javascript; charset=utf-8"),
                      "/viewer.js": ("viewer.js", "text/javascript; charset=utf-8")}
            try:
                if request.path in assets:
                    name, kind = assets[request.path]
                    self.respond(200, (ASSETS / name).read_bytes(), kind)
                elif request.path in {"/api/memory/history", "/api/memory/change"}:
                    query = parse_qs(request.query)
                    identifier = query.get("id", [None])[0]
                    result = (memory_feed.history(identifier, int(query.get("cursor", ["0"])[0]))
                              if request.path.endswith("history") else
                              memory_feed.change(identifier, int(query.get("revision", ["0"])[0])))
                    self.respond(200, json.dumps(result, ensure_ascii=False).encode(), "application/json; charset=utf-8")
                elif request.path == "/api/memory":
                    query = parse_qs(request.query)
                    identifier = query.get("id", [None])[0]
                    result = (memory_feed.journal(identifier, int(query.get("cursor", ["0"])[0]))
                              if identifier else memory_feed.snapshots())
                    self.respond(200, json.dumps(result, ensure_ascii=False).encode(), "application/json; charset=utf-8")
                elif request.path == "/api/topology":
                    name = parse_qs(request.query).get("name", ["chain"])[0]
                    try:
                        body = json.dumps(topology_payload(name), ensure_ascii=False).encode()
                    except ValueError:
                        self.respond(404, b"Unknown topology", "text/plain")
                    else:
                        self.respond(200, body, "application/json; charset=utf-8")
                elif request.path == "/api/events":
                    cursor = int(parse_qs(request.query).get("cursor", ["0"])[0])
                    result = feed.read(cursor)
                    result.update(log_name=feed.path.parent.name + "/" + feed.path.name, initial_agent=agent)
                    self.respond(200, json.dumps(result, ensure_ascii=False).encode(), "application/json; charset=utf-8")
                else:
                    self.respond(404, b"Not found", "text/plain")
            except ValueError:
                self.respond(400, b"Invalid cursor", "text/plain")
            except OSError:
                self.respond(503, b"Log is unavailable", "text/plain")

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def serve_trace(path, *, port=8765, agent=None, open_browser=True, emit=print):
    with create_trace_server(path, port=port, agent=agent) as server:
        url = f"http://127.0.0.1:{server.server_address[1]}/"
        emit(f"RAAC · Luồng trao đổi: {url}")
        emit("Chỉ đọc log, tự cập nhật. Ctrl+C để dừng trình xem.")
        if open_browser:
            webbrowser.open(url)
        try:
            server.serve_forever(poll_interval=0.25)
        except KeyboardInterrupt:
            pass
