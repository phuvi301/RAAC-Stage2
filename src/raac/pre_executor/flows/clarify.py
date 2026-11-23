"""Flow clarify -> role `clarifier` (Clarification Manager). Chỉ hỏi và trả lời: không góp ý, không approve.

Cách làm theo GitHub Spec Kit /clarify (học cấu trúc, prompt tự viết): mỗi vòng agent quét đầu vào theo 10 nhóm độ
phủ của Spec Kit (contracts.COVERAGE_CATEGORIES), đánh dấu clear/partial/missing/not_applicable/deferred, rồi hỏi tối đa 5 câu có
tác động lớn nhất về các nhóm partial/missing; mỗi câu là trắc nghiệm có phương án khuyến nghị hoặc trả lời ngắn.

Vào:  request (yêu cầu thô HOẶC một bản spec) + các câu đã hỏi và câu trả lời hiện có trong answers.md
      + độ phủ của vòng trước + workspace_mode (greenfield/brownfield, tính bằng code).
Ra:   mỗi lần chạy một vòng câu hỏi mới (clarify.json) -> answers.md (câu hỏi + đáp án gợi ý).
      Người dùng trả lời (TUI hoặc sửa answers.md) rồi chạy tiếp; agent hỏi vòng sau dựa trên câu trả lời.
Kết thúc: khi agent báo đủ thông tin (complete) hoặc hết max_clarify_rounds, flow TỰ CHỐT (`finish`):
      Clarification = [{id, question, answer, assumed}] ghi vào clarification.json — đầu vào của flow business_analyst.
      `assumed` = người dùng để nguyên đáp án gợi ý. Sửa answers.md sau khi chốt thì chạy clarify lại để chốt lại.
      Kèm coverage.json: độ phủ cuối của từng nhóm (clear | resolved | deferred | not_applicable | outstanding).
Luật (bằng code, vi phạm thì agent được gửi lại kèm lỗi): độ phủ liệt kê đủ 10 nhóm, mỗi nhóm một lần; không được báo
complete khi còn nhóm missing; not_applicable phải có lý do; câu hỏi chỉ nhắm nhóm partial/missing; trắc nghiệm có 2-5
lựa chọn và phương án khuyến nghị nằm trong các lựa chọn; không hỏi lại câu đã hỏi; tối đa 5 câu một vòng (schema).
auto_assume (CLI --auto-assume, cho chạy tự động/benchmark): tự dùng phương án khuyến nghị qua mọi vòng (mọi câu được
ghi trung thực `assumed: true`) tới khi agent báo đủ hoặc hết max_clarify_rounds, rồi tự chốt.
"""

import re

from .. import gate
from ..artifacts import Artifacts
from ..contracts import COVERAGE_CATEGORIES, ClarifyOutput
from ..render import parse_answers, render_answers
from ._checks import workspace_mode
from .base import run_role

FLOW = "clarify"
ROLE = "Clarifier"


def _normalized(text: str) -> str:
    return re.sub(r"\W+", " ", text.lower()).strip()


def rounds(artifacts: Artifacts) -> list[dict]:
    return (artifacts.read_json("clarify.json") or {}).get("rounds", [])


def clarification(artifacts: Artifacts) -> list[dict]:
    """Câu hỏi + câu trả lời hiện tại (đọc answers.md); câu không sửa so với gợi ý là giả định."""
    answers = parse_answers(artifacts.read_text("answers.md") or "")
    result = []
    for round_ in rounds(artifacts):
        for question in round_["questions"]:
            answer = answers.get(question["id"], question.get("default_answer", ""))
            result.append({"id": question["id"], "category": question.get("category", ""),
                           "question": question["text"], "answer": answer,
                           "assumed": answer.strip() == question.get("default_answer", "").strip()})
    return result


def save_answers(artifacts: Artifacts, updates: dict[str, str]) -> None:
    """Ghi câu trả lời (vd. nhập trong TUI) vào answers.md, giữ nguyên các câu khác."""
    answers = {item["id"]: item["answer"] for item in clarification(artifacts)}
    answers.update(updates)
    artifacts.write_text("answers.md", render_answers(rounds(artifacts), answers))


def finished(artifacts: Artifacts, config) -> bool:
    history = rounds(artifacts)
    return bool(history) and (history[-1]["complete"] or len(history) >= config.max_clarify_rounds)


def final_coverage(artifacts: Artifacts) -> list[dict]:
    """Độ phủ cuối: lấy độ phủ của vòng cuối; nhóm partial/missing đã có câu trả lời ở vòng đó thành `resolved`,
    còn lại thành `outstanding` (giống báo cáo cuối của Spec Kit)."""
    history = rounds(artifacts)
    if not history:
        return []
    answered = {question.get("category") for question in history[-1]["questions"]}
    result = []
    for item in history[-1].get("coverage", []):
        status = item["status"]
        if status in ("partial", "missing"):
            status = "resolved" if item["category"] in answered else "outstanding"
        result.append({**item, "status": status})
    return result


def finish(artifacts: Artifacts) -> list[dict]:
    """Chốt câu trả lời hiện tại: ghi clarification.json + coverage.json và mở đường cho flow business_analyst."""
    result = clarification(artifacts)
    artifacts.write_json("clarification.json", result)
    artifacts.write_json("coverage.json", final_coverage(artifacts))
    gate.approve(artifacts, FLOW)
    return result


def check_against(asked: list[str]):
    seen = {_normalized(text) for text in asked}

    def check(output: ClarifyOutput) -> list[str]:
        problems = [f"question already asked: {q.text}" for q in output.questions if _normalized(q.text) in seen]
        listed = [item.category for item in output.coverage]
        problems += [f"unknown coverage category {name}" for name in listed if name not in COVERAGE_CATEGORIES]
        problems += [f"coverage lists {name} more than once" for name in sorted({n for n in listed if listed.count(n) > 1})]
        problems += [f"coverage is missing category {name}" for name in COVERAGE_CATEGORIES if name not in listed]
        status = {item.category: item.status for item in output.coverage}
        missing = [name for name, value in status.items() if value == "missing"]
        if output.complete and missing:
            problems.append(f"complete is true while categories are missing: {missing}; ask about them or mark them "
                            "not_applicable/deferred with a reason")
        problems += [f"{item.category} is not_applicable without a reason in note" for item in output.coverage
                     if item.status == "not_applicable" and not item.note.strip()]
        for q in output.questions:
            if q.category not in COVERAGE_CATEGORIES:
                problems.append(f"{q.id} has unknown category {q.category!r}")
            elif status.get(q.category) not in ("partial", "missing"):
                problems.append(f"{q.id} asks about {q.category}, which is {status.get(q.category)}; "
                                "only ask about partial or missing categories")
            if len(q.options) == 1 or len(q.options) > 5:
                problems.append(f"{q.id} must have 2-5 options, or none for a short answer")
            elif q.options and q.default_answer not in q.options:
                problems.append(f"{q.id}: default_answer must be one of its options (the recommended one)")
        return problems
    return check


def _done(artifacts: Artifacts, config) -> dict:
    history = rounds(artifacts)
    reason = "agent thấy đã đủ thông tin" if history[-1]["complete"] else f"đã hết {config.max_clarify_rounds} vòng"
    items = finish(artifacts)
    coverage = artifacts.read_json("coverage.json") or []
    counts = {}
    for item in coverage:
        counts[item["status"]] = counts.get(item["status"], 0) + 1
    return {"status": "done", "reason": reason, "round": len(history), "answered": len(items), "coverage": counts,
            "assumed": sum(item["assumed"] for item in items), "answers": str(artifacts.path("answers.md")),
            "clarification": str(artifacts.path("clarification.json"))}


async def _ask_round(artifacts: Artifacts, runtime, config, request: str) -> dict:
    """Một vòng hỏi: gọi role, đánh số câu hỏi, ghi clarify.json + answers.md (điền sẵn đáp án khuyến nghị)."""
    history = rounds(artifacts)
    current = clarification(artifacts)
    output = await run_role(
        runtime, FLOW, ROLE, ClarifyOutput,
        {"request": request, "answers": current, "round": len(history) + 1,
         "max_rounds": config.max_clarify_rounds, "coverage_categories": COVERAGE_CATEGORIES,
         "previous_coverage": history[-1].get("coverage", []) if history else [],
         "workspace_mode": workspace_mode(artifacts.workspace)},
        check_against([item["question"] for item in current]),
        retries=config.max_flow_retries, round_number=len(history),
    )

    # Đánh số lại Q1, Q2 ... xuyên suốt các vòng để câu trả lời trong answers.md không bị trùng id.
    start = sum(len(round_["questions"]) for round_ in history)
    questions = [{**question.model_dump(), "id": f"Q{start + index}"}
                 for index, question in enumerate(output.questions, start=1)]
    history.append({"round": len(history) + 1, "run_id": runtime.run_id, "complete": output.complete,
                    "summary": output.summary, "coverage": [item.model_dump() for item in output.coverage],
                    "questions": questions})
    artifacts.write_json("clarify.json", {"rounds": history})
    artifacts.write_text("answers.md", render_answers(history, {item["id"]: item["answer"] for item in current}))
    gate.invalidate_from(artifacts, FLOW)
    return {"status": "questions", "round": len(history), "questions": len(questions), "complete": output.complete,
            "run_id": runtime.run_id, "answers": str(artifacts.path("answers.md"))}


async def run(artifacts: Artifacts, runtime, config, *, note: str | None = None, auto_assume: bool = False) -> dict:
    if note:
        raise ValueError("clarify chỉ hỏi và trả lời, không nhận góp ý")
    request = artifacts.request()
    if not request:
        raise ValueError("Chưa có yêu cầu: chạy clarify với --prompt hoặc --prompt-file")
    if not auto_assume:
        if finished(artifacts, config):
            return _done(artifacts, config)
        result = await _ask_round(artifacts, runtime, config, request)
        if not result["questions"]:  # agent thấy đủ ngay, không còn gì để trả lời
            return {**_done(artifacts, config), "run_id": runtime.run_id}
        return result
    # Tự động: answers.md đã điền sẵn đáp án khuyến nghị cho câu mới, nên mọi câu chưa sửa là assumed=True.
    while not finished(artifacts, config):
        result = await _ask_round(artifacts, runtime, config, request)
        if not result["questions"]:
            break
    return {**_done(artifacts, config), "run_id": runtime.run_id, "auto_assumed": True}
