"""Read audit JSONL as a terminal timeline without creating another artifact."""

import json
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from raac.llm.usage import cached_tokens

from .memory_viewer import MemoryFeed
from .tracing import JSONLTrace


def _mapping(value):
    return value if isinstance(value, dict) else {}


def _text(value, limit=180):
    text = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", str(value))
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit] + "..."


def _summary(value):
    if isinstance(value, str):
        try:
            return _summary(json.loads(value))
        except (ValueError, RecursionError):
            return _text(value)
    if not isinstance(value, dict):
        return _text(value)
    if "requested" in value and isinstance(value.get("results"), list):
        return f"Lô: {value.get('completed', 0)}/{value['requested']} mục đã chạy, lỗi={value.get('failed', 0)}, bỏ qua={len(value.get('skipped', []))}"
    if "tests_run" in value:
        message = f"test={value.get('status')}, chạy={value['tests_run']}, lỗi={value.get('failed', '?')}"
        if value.get("subtest_failures"):
            message += f", subtest lỗi={value['subtest_failures']}"
        return message
    if "verdict" in value:
        return f"verdict={value['verdict']}; {_text(value.get('summary', ''))}"
    if "summary" in value:
        return _text(value["summary"])
    if "approach" in value:
        return _text(value["approach"])
    if "path" in value:
        content = value.get("content")
        return f"{value['path']}" + (f" ({len(content)} ký tự)" if isinstance(content, str) else " (đã ghi)")
    if "matches" in value:
        return f"{len(value['matches'])} kết quả tìm kiếm"
    return _text(json.dumps(value, ensure_ascii=False))


def _details(value, indent=4):
    prefix = " " * indent
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (ValueError, RecursionError):
            for line in value.splitlines():
                yield prefix + re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", line)
        else:
            yield from _details(parsed, indent)
    elif isinstance(value, dict):
        for key, item in value.items():
            yield prefix + _text(key) + ":"
            yield from _details(item, indent + 2)
    elif isinstance(value, list):
        for index, item in enumerate(value, 1):
            yield prefix + f"[{index}]"
            yield from _details(item, indent + 2)
    else:
        yield prefix + _text(value)


class TraceViewer:
    def __init__(self, *, details=False, agent=None):
        self.details = details
        self.agent = agent.lower() if agent else None
        self.total_tokens = 0
        self.calls = 0
        self.unknown_usage = False
        self.cached_tokens = 0
        self.unknown_cache = False
        self.token_limit = None

    def render(self, event):
        kind = event.get("event")
        if not isinstance(kind, str):
            return []
        effective = _mapping(_mapping(event.get("provenance")).get("effective_config"))
        if type(effective.get("token_limit")) is int:
            self.token_limit = effective["token_limit"]
        if kind == "llm_call":
            self.calls += 1
            usage = _mapping(event.get("usage"))
            tokens = [usage.get("prompt_tokens"), usage.get("completion_tokens")]
            if all(type(value) is int for value in tokens):
                self.total_tokens += sum(tokens)
            else:
                self.unknown_usage = True
            cached = cached_tokens(usage)
            if cached is None:
                self.unknown_cache = True
            else:
                self.cached_tokens += cached
        if kind == "run_completed":
            cached = cached_tokens(_mapping(event.get("usage")))
            self.unknown_cache = cached is None or _mapping(event.get("usage")).get("cache_usage_complete") is False
            if cached is not None:
                self.cached_tokens = cached
            # A pasted/tail log can omit earlier events. Prefer final run totals.
            final_usage = _mapping(event.get("usage"))
            if type(final_usage.get("total_tokens")) is int:
                self.total_tokens = final_usage["total_tokens"]
                self.unknown_usage = not final_usage.get("usage_complete", True)
                if type(final_usage.get("calls")) is int:
                    self.calls = final_usage["calls"]
        agent = event.get("agent_id", event.get("role", "MAS"))
        if self.agent and str(agent).lower() != self.agent and kind not in {"run_started", "run_completed"}:
            return []
        stamp = event.get("timestamp", "")
        try:
            stamp = datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone(
                timezone(timedelta(hours=7))).strftime("%H:%M:%S")
        except (ValueError, AttributeError):
            stamp = "--:--:--"
        role = str(agent).capitalize() if agent != "MAS" else "MAS"
        where = f"{role}"
        if event.get("round"):
            where += f" vòng {event['round']}"
        if event.get("turn"):
            where += f" / LLM {event['turn']}"
        content = None
        if kind == "run_started":
            decision = _mapping(event.get("decision"))
            message = f"BẮT ĐẦU {event.get('run_id')} | {decision.get('topology')} | tối đa {decision.get('max_rounds')} vòng"
        elif kind == "agent_started":
            request = _mapping(event.get("request"))
            sender = _mapping(request.get("incoming_message")).get("from", "user")
            message = f"Bắt đầu, nhận bàn giao từ {sender}"
            content = request
        elif kind == "agent_completed":
            content = event.get("output", {})
            message = "HOÀN TẤT | " + _summary(content)
            if event.get("agent_id") == "environment":
                content = {"output": content, "runtime": event.get("runtime", {})}
                message = "MÔI TRƯỜNG | " + _summary(event.get("output", {}))
        elif kind == "agent_failed":
            message = f"DỪNG | {event.get('code') or event.get('error_kind')}"
            content = {key: event.get(key) for key in ("code", "error_kind", "stack")}
        elif kind == "agent_final_call":
            message = "Lượt LLM cuối: trả báo cáo bàn giao, không gọi thêm tool"
        elif kind == "coder_patch_rebased":
            message = "Chuẩn hóa đường dẫn patch theo Git worktree đã quan sát"
        elif kind == "coder_patch_rebuilt":
            message = ("Diff đã có trong workspace; dùng patch chuẩn theo baseline run"
                       if event.get("reason") == "already_applied_diff" else
                       "Diff LLM sai định dạng; đã sinh và kiểm tra patch từ file Coder lưu")
        elif kind in {"memory_saved", "memory_loaded", "memory_compacted"}:
            title = {"memory_saved": "LƯU MEMORY", "memory_loaded": "NẠP MEMORY",
                     "memory_compacted": "COMPACT MEMORY"}[kind]
            message = f"{title} | revision {event.get('revision', '?')}"
            if kind == "memory_compacted":
                message += f" | ~{event.get('before_estimated_tokens', '?')} → ~{event.get('after_estimated_tokens', '?')} token"
            elif event.get("entry"):
                entry = _mapping(event["entry"])
                message += " | " + str(entry.get("tool") or entry.get("kind", ""))
            content = {key: event[key] for key in ("path", "entry", "memory", "stored_entries", "compactions") if key in event}
        elif kind == "llm_request":
            message = "Gửi yêu cầu tới LLM"
        elif kind == "context_compacted":
            message = f"Rút gọn lịch sử: {event.get('before_chars', '?')} → {event.get('after_chars', '?')} ký tự"
        elif kind == "llm_attempt_started" and event.get("attempt", 1) > 1:
            message = f"Gọi lại HTTP, attempt {event['attempt']}"
        elif kind == "llm_call":
            usage = _mapping(event.get("usage"))
            message = (f"HTTP {event.get('status')} | {event.get('latency_ms', '?')} ms | "
                       f"token vào/ra {usage.get('prompt_tokens', '?')}/{usage.get('completion_tokens', '?')}")
            cached = cached_tokens(usage)
            message += " | cache " + (f"{cached:,}" if cached is not None else "chưa có số liệu")
            if event.get("error"):
                message += " | " + str(event["error"])
            if event.get("retry_blocked_reason") == "usage_unavailable":
                message += " | Không gọi lại: usage chưa xác định"
        elif kind == "llm_response":
            response = _mapping(event.get("message"))
            content = response.get("content")
            calls = response.get("tool_calls") or []
            calls = calls if isinstance(calls, list) else []
            names = [_text(_mapping(_mapping(call).get("function")).get("name", "?"), 60) for call in calls]
            message = "LLM yêu cầu: " + ", ".join(names) if calls else "LLM trả lời | " + _summary(content or {})
            if event.get("error"):
                message = f"LLM trả lỗi | {event['error']}"
        elif kind == "command_output":
            message = f"Docker {event.get('stream', 'output')} | " + _text(event.get("content", f"{event.get('bytes', 0)} bytes"), 240)
            content = event.get("content")
        elif kind in {"workspace_changed", "candidate_exported", "benchmark_phase_started", "benchmark_phase_completed", "benchmark_phase_failed", "container_started", "container_removed"}:
            message = kind + " | " + _text(event.get("phase") or event.get("paths") or event.get("patch_sha256") or event.get("container_id", ""), 240)
        elif kind == "tool_call_started":
            arguments = _mapping(event.get("arguments"))
            hints = [f"{key}={_text(arguments[key], 80)}" for key in ("path", "query") if key in arguments]
            if arguments.get("command"):
                hints.append("command=" + _text(arguments["command"], 100))
            if arguments.get("cwd"):
                hints.append("cwd=" + _text(arguments["cwd"], 60))
            if isinstance(arguments.get("content"), str):
                hints.append(f"{len(arguments['content'])} ký tự")
            for key in ("files", "commands"):
                if isinstance(arguments.get(key), list):
                    hints.append(f"{key}={len(arguments[key])} mục")
            message = f"Gọi {event.get('tool_name')}({', '.join(hints)})"
            if event.get("origin") == "adapter_validation":
                message += " [adapter tự kiểm chứng]"
            elif event.get("origin") == "batch_item":
                message += f" [mục {event.get('batch_index')}/{event.get('batch_size')}]"
            content = arguments
        elif kind == "tool_call_completed":
            observation = _mapping(event.get("observation"))
            result = observation.get("result", {})
            outcome = _summary(result) if observation.get("ok") else _text(
                observation.get("error_kind") or observation.get("error"))
            message = f"{event.get('tool_name')}: {'OK' if observation.get('ok') else 'LỖI'} | {outcome}"
            content = observation
        elif kind in {"tool_call_failed", "tool_arguments_invalid"}:
            message = f"Tool {event.get('tool_name')}: LỖI | {event.get('error_kind') or 'arguments JSON không hợp lệ'}"
            content = event.get("observation")
        elif kind == "run_completed":
            message = f"KẾT THÚC: {event.get('status')} | {event.get('stop_reason')}"
            message += f" | {self.calls} HTTP attempt | token {'>=' if self.unknown_usage else ''}{self.total_tokens:,}"
            if self.token_limit is not None:
                message += f"/{self.token_limit:,}"
            message += " | cache " + ("chưa có số liệu" if self.unknown_cache else f"{self.cached_tokens:,}")
            content = event.get("error")
        else:
            return []
        lines = [f"{stamp} [{where}] {_text(message, 280)}"]
        if self.details and content is not None:
            lines.extend(_details(content))
        return lines


def show_trace(path: Path, *, details=False, agent=None, follow=False, memory=False, emit=print):
    viewer = TraceViewer(details=details, agent=agent)
    redactor = JSONLTrace(path)
    emit("Giờ Việt Nam (UTC+7) | --details: mở nội dung | --agent: lọc role")
    with Path(path).open(encoding="utf-8-sig", errors="replace") as stream:
        line_number = 0
        benchmark = False
        while True:
            position = stream.tell()
            line = stream.readline()
            if not line or (follow and not line.endswith("\n")):
                if not follow:
                    break
                stream.seek(position)
                time.sleep(0.25)
                continue
            line_number += 1
            try:
                event = json.loads(line)
                if not isinstance(event, dict):
                    raise ValueError
            except ValueError:
                emit(f"[Dòng {line_number}] JSON không hợp lệ, bỏ qua")
                continue
            for rendered in viewer.render(redactor.redact(event)):
                emit(rendered)
            kind = event.get("event")
            benchmark = benchmark or kind == "benchmark_phase_started"
            finished = (kind in {"benchmark_phase_completed", "benchmark_phase_failed"}
                        and event.get("status") in {"completed", "error", "timeout", "interrupted"})
            if follow and (finished or (kind == "run_completed" and not benchmark)):
                break
    if memory:
        show_memory(path, details=details, agent=agent, emit=emit)


def show_memory(path, *, details=False, agent=None, emit=print):
    """Show snapshots and the complete paged action journal, without changing memory."""
    if agent and str(agent).lower() != "coder":
        return
    feed = MemoryFeed(path)
    data = feed.snapshots()
    for warning in data["warnings"]:
        emit(warning)
    if not data["memories"]:
        emit("MEMORY: run chưa có memory Coder đã lưu.")
    for saved in data["memories"]:
        identity = _mapping(saved.get("identity"))
        emit(f"MEMORY CODER | work {identity.get('work_id', '?')} | revision {saved.get('revision', '?')} "
             f"| ~{saved.get('estimated_tokens', '?')} token | compact {saved.get('compactions', 0)} lần")
        if details:
            for line in _details(saved):
                emit(line)
        if not saved.get("journal_available"):
            continue
        cursor = 0
        while True:
            page = feed.journal(saved["id"], cursor)
            for entry in page["entries"]:
                emit(f"  #{entry.get('sequence', '?')} · vòng {entry.get('round', '?')} · "
                     + str(entry.get("tool") or entry.get("kind", "?")))
                if details:
                    for line in _details(entry):
                        emit(line)
            for warning in page["warnings"]:
                emit(warning)
            if not page["has_more"]:
                break
            cursor = page["cursor"]
