"""Theo dõi agent khi chạy flow (như observe của MAS) và tóm tắt thay đổi giữa hai bản nháp.

- describe(record): một sự kiện trace (llm.jsonl) -> một dòng log ngắn cho TUI, hoặc None nếu không cần hiện.
- summarize_changes(old, new, flow): sau khi agent sửa bản nháp theo góp ý, liệt kê mục thêm / bớt / sửa.
"""

import json

TOOL_ARGUMENT_KEYS = ("path", "paths", "pattern", "query", "command")


def _short(text, limit=160) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def describe(record: dict) -> str | None:
    event = record.get("event")
    if event == "agent_started":
        return f"▶ {record.get('role', 'agent')} bắt đầu"
    if event == "llm_request":
        return f"→ gọi model (lượt {record.get('turn', '?')})"
    if event == "llm_public_summary" and record.get("text"):
        return f"💭 {_short(record['text'])}"
    if event == "tool_call_started":
        arguments = record.get("arguments") if isinstance(record.get("arguments"), dict) else {}
        shown = ", ".join(f"{key}={_short(json.dumps(arguments[key], ensure_ascii=False), 60)}"
                          for key in TOOL_ARGUMENT_KEYS if key in arguments)
        return f"🔧 {record.get('tool_name', 'tool')}({shown})"
    if event == "tool_call_completed" and record.get("status") not in (None, "ok", "completed"):
        return f"   ✗ {record.get('tool_name', 'tool')}: {record.get('status')}"
    if event == "llm_call":
        usage = record.get("usage") if isinstance(record.get("usage"), dict) else None
        if record.get("error"):
            return f"   ✗ lỗi gọi model: {record['error']}"
        if usage:
            return (f"   token vào {usage.get('prompt_tokens') or 0:,} / ra {usage.get('completion_tokens') or 0:,}"
                    f"{', ' + format(record['latency_ms'] / 1000, '.1f') + ' s' if record.get('latency_ms') else ''}")
    if event == "flow_rule_violation":
        return (f"⚠ đầu ra vi phạm {record.get('problems', '?')} luật, gửi lại cho agent sửa"
                f" (lần {record.get('attempt', '?')}): {_short(record.get('first', ''), 120)}")
    if event == "agent_completed":
        return f"■ {record.get('role', 'agent')} xong"
    if event == "agent_failed":
        return f"✖ {record.get('role', 'agent')} lỗi: {record.get('error_kind', '?')}"
    return None


# Danh sách cần so cho từng flow: tên trường -> (nhãn, trường dùng để hiện tên mục).
CHANGE_FIELDS = {
    "requirements": {"features": ("chức năng", "name"), "success_criteria": ("tiêu chí thành công", "outcome"),
                     "non_functional_requirements": ("NFR", "requirement")},
    "profile": {"user_flows": ("user flow", "name"), "business_rules": ("quy tắc", "rule"),
                "functional_requirements": ("yêu cầu", "description"), "edge_cases": ("trường hợp biên", "situation")},
}


def summarize_changes(old: dict | None, new: dict, flow: str) -> list[str]:
    """['+F-9 Voucher', '~F-3 Đặt hàng', '-F-6 Đánh giá', ...]; rỗng nếu không có bản trước hoặc không đổi."""
    if not old:
        return []
    lines = []
    for field, (label, name_key) in CHANGE_FIELDS.get(flow, {}).items():
        before = {item["id"]: item for item in old.get(field, []) if isinstance(item, dict) and "id" in item}
        after = {item["id"]: item for item in new.get(field, []) if isinstance(item, dict) and "id" in item}
        for key in after:
            if key not in before:
                lines.append(f"+ {label} {key} {_short(after[key].get(name_key, ''), 70)}")
            elif after[key] != before[key]:
                lines.append(f"~ {label} {key} {_short(after[key].get(name_key, ''), 70)}")
        lines += [f"- {label} {key} {_short(before[key].get(name_key, ''), 70)}" for key in before if key not in after]
    for field in ("overview", "constraints", "assumptions", "out_of_scope", "open_questions", "key_entities"):
        if field in new and old.get(field) != new.get(field):
            lines.append(f"~ {field}")
    return lines
