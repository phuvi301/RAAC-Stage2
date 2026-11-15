"""Theo dõi agent (dòng log từ trace) và tóm tắt thay đổi sau khi agent sửa bản nháp theo góp ý."""

import asyncio

from raac.pre_executor.events import describe, summarize_changes
from raac.pre_executor.flows.base import run_role
from raac.pre_executor.contracts import ClarifyOutput


def test_trace_events_become_short_log_lines():
    assert describe({"event": "agent_started", "role": "Clarifier"}) == "▶ Clarifier bắt đầu"
    assert describe({"event": "llm_request", "turn": 2}) == "→ gọi model (lượt 2)"
    assert describe({"event": "llm_public_summary", "text": "Đang đọc   yêu cầu"}) == "💭 Đang đọc yêu cầu"
    assert describe({"event": "tool_call_started", "tool_name": "read_file",
                     "arguments": {"path": "README.md", "other": 1}}) == '🔧 read_file(path="README.md")'
    assert describe({"event": "llm_call", "usage": {"prompt_tokens": 8000, "completion_tokens": 900},
                     "latency_ms": 2500}) == "   token vào 8,000 / ra 900, 2.5 s"
    assert describe({"event": "llm_call", "error": "ConnectError"}) == "   ✗ lỗi gọi model: ConnectError"
    assert "vi phạm 2 luật" in describe({"event": "flow_rule_violation", "problems": 2, "attempt": 1, "first": "x"})
    assert describe({"event": "checkpoint_timing"}) is None and describe({"event": "llm_output_delta"}) is None


def test_changes_list_added_edited_and_removed_items():
    old = {"features": [{"id": "F-1", "name": "Giỏ hàng"}, {"id": "F-2", "name": "Đánh giá"}],
           "success_criteria": [], "non_functional_requirements": [], "constraints": ["COD"]}
    new = {"features": [{"id": "F-1", "name": "Giỏ hàng có voucher"}, {"id": "F-3", "name": "Mã giảm giá"}],
           "success_criteria": [], "non_functional_requirements": [], "constraints": ["COD"]}
    assert summarize_changes(old, new, "requirements") == [
        "~ chức năng F-1 Giỏ hàng có voucher", "+ chức năng F-3 Mã giảm giá", "- chức năng F-2 Đánh giá"]
    assert summarize_changes(None, new, "requirements") == [] and summarize_changes(new, new, "requirements") == []


def test_a_rule_violation_is_noted_in_the_trace():
    noted = []

    class Runtime:
        run_id, task_id = "chain-x", "task-x"

        def __init__(self):
            self.outputs = [{"complete": False, "questions": []}, {"complete": True, "questions": []}]

        async def run(self, role_name, request):
            return self.outputs.pop(0) if False else {"complete": True, "questions": []}

        def note(self, event, **data):
            noted.append((event, data))

    calls = iter([["bad thing"], []])
    asyncio.run(run_role(Runtime(), "clarify", "Clarifier", ClarifyOutput, {}, lambda output: next(calls)))
    assert noted == [("flow_rule_violation", {"flow": "clarify", "attempt": 1, "problems": 1, "first": "bad thing"})]
