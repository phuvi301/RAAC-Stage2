"""Flow clarify -> role `clarifier` (Clarification Manager). Chỉ hỏi và trả lời: không góp ý, không approve.

Vào:  request (yêu cầu tổng quát của người dùng) + các câu đã hỏi và câu trả lời hiện có trong answers.md.
Ra:   mỗi lần chạy một vòng câu hỏi mới (clarify.json) -> answers.md (câu hỏi + đáp án gợi ý).
      Người dùng trả lời (TUI hoặc sửa answers.md) rồi chạy tiếp; agent hỏi vòng sau dựa trên câu trả lời.
Kết thúc: khi agent báo đủ thông tin (complete) hoặc hết max_clarify_rounds, flow TỰ CHỐT (`finish`):
      Clarification = [{id, question, answer, assumed}] ghi vào clarification.json — đầu vào của flow requirements.
      `assumed` = người dùng để nguyên đáp án gợi ý. Sửa answers.md sau khi chốt thì chạy clarify lại để chốt lại.
Luật: không hỏi lại câu đã hỏi; chưa đủ thì phải có câu hỏi; tối đa 5 câu một vòng (schema).
"""

import re

from .. import gate
from ..artifacts import Artifacts
from ..contracts import ClarifyOutput
from ..render import parse_answers, render_answers
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
            result.append({"id": question["id"], "question": question["text"], "answer": answer,
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


def finish(artifacts: Artifacts) -> list[dict]:
    """Chốt câu trả lời hiện tại: ghi clarification.json và mở đường cho flow requirements."""
    result = clarification(artifacts)
    artifacts.write_json("clarification.json", result)
    gate.approve(artifacts, FLOW)
    return result


def check_against(asked: list[str]):
    seen = {_normalized(text) for text in asked}

    def check(output: ClarifyOutput) -> list[str]:
        repeated = [question.text for question in output.questions if _normalized(question.text) in seen]
        return [f"question already asked: {text}" for text in repeated]
    return check


def _done(artifacts: Artifacts, config) -> dict:
    history = rounds(artifacts)
    reason = "agent thấy đã đủ thông tin" if history[-1]["complete"] else f"đã hết {config.max_clarify_rounds} vòng"
    items = finish(artifacts)
    return {"status": "done", "reason": reason, "round": len(history), "answered": len(items),
            "assumed": sum(item["assumed"] for item in items), "answers": str(artifacts.path("answers.md")),
            "clarification": str(artifacts.path("clarification.json"))}


async def run(artifacts: Artifacts, runtime, config, *, note: str | None = None) -> dict:
    if note:
        raise ValueError("clarify chỉ hỏi và trả lời, không nhận góp ý")
    request = artifacts.request()
    if not request:
        raise ValueError("Chưa có yêu cầu: chạy clarify với --prompt hoặc --prompt-file")
    if finished(artifacts, config):
        return _done(artifacts, config)

    history = rounds(artifacts)
    current = clarification(artifacts)
    output = await run_role(
        runtime, FLOW, ROLE, ClarifyOutput,
        {"request": request, "answers": current, "round": len(history) + 1,
         "max_rounds": config.max_clarify_rounds},
        check_against([item["question"] for item in current]),
        retries=config.max_flow_retries, round_number=len(history),
    )

    # Đánh số lại Q1, Q2 ... xuyên suốt các vòng để câu trả lời trong answers.md không bị trùng id.
    start = sum(len(round_["questions"]) for round_ in history)
    questions = [{**question.model_dump(), "id": f"Q{start + index}"}
                 for index, question in enumerate(output.questions, start=1)]
    history.append({"round": len(history) + 1, "run_id": runtime.run_id, "complete": output.complete,
                    "summary": output.summary, "questions": questions})
    artifacts.write_json("clarify.json", {"rounds": history})
    artifacts.write_text("answers.md", render_answers(history, {item["id"]: item["answer"] for item in current}))
    gate.invalidate_from(artifacts, FLOW)
    if not questions:  # agent thấy đủ ngay, không còn gì để trả lời
        return {**_done(artifacts, config), "run_id": runtime.run_id}
    return {"status": "questions", "round": len(history), "questions": len(questions), "complete": output.complete,
            "run_id": runtime.run_id, "answers": str(artifacts.path("answers.md"))}
