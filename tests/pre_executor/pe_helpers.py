"""Hàm dùng chung cho test pre_executor."""

from copy import deepcopy

from raac.pre_executor.contracts import COVERAGE_CATEGORIES


def with_coverage(output):
    """Câu trả lời giả của Clarifier chưa có độ phủ thì điền độ phủ hợp lệ: nhóm được hỏi là partial, còn lại clear.
    Test về luật độ phủ tự ghi `coverage` nên không bị đụng tới."""
    if not isinstance(output, dict) or "complete" not in output or "coverage" in output:
        return output
    output = deepcopy(output)
    for question in output["questions"]:
        question.setdefault("category", "functional_scope")
    asked = {question["category"] for question in output["questions"]}
    output["coverage"] = [{"category": name, "status": "partial" if name in asked else "clear", "note": ""}
                          for name in COVERAGE_CATEGORIES]
    return output
