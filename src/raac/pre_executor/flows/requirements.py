"""Flow requirements -> role `requirement_analyst` (Product Requirement Analyst).

Vào:  request + Clarification đã chốt (clarification.json) + góp ý của người dùng; khi revise thêm bản nháp hiện tại.
Ra:   requirements.json (+ requirements.md để đọc): tổng quan sản phẩm, CHỨC NĂNG CỐT LÕI (F-n) và YÊU CẦU PHI CHỨC
      NĂNG (NFR-n có metric), ràng buộc, giả định, ngoài phạm vi, câu hỏi mở. Xem contracts.RequirementsOutput.
      Chi tiết từng chức năng (user flow, quy tắc nghiệp vụ, yêu cầu chi tiết, AC) là việc của flow profile.
Vòng duyệt: bản nháp -> người dùng góp ý (revise) -> agent sửa tiếp trên bản nháp -> ... -> approve -> profile chạy.
Tham khảo: GitHub Spec Kit `templates/spec-template.md` và `templates/commands/specify.md` (chức năng như user story
có lý do ưu tiên + cách kiểm độc lập; success criteria đo được, không nói tới công nghệ; key entities; tối đa 3 câu hỏi
mở; mặc định hợp lý thì ghi vào assumptions thay vì hỏi), ISO/IEC 25010 (đặc tính chất lượng cho NFR).
Luật (bằng code): id F-n/SC-n/NFR-n duy nhất; tên chức năng không trùng; mỗi chức năng có lý do ưu tiên và cách kiểm
độc lập; có ít nhất một success criterion và không nhắc công nghệ; tối đa 3 câu hỏi mở; căn cứ (`sources`) chỉ là
"request" hoặc id câu hỏi clarify có thật; mọi câu clarify người dùng để nguyên gợi ý phải có trong assumptions.
"""

import re

from .. import gate
from ..artifacts import Artifacts
from ..contracts import MAX_OPEN_QUESTIONS, RequirementsOutput
from ..events import summarize_changes
from ..render import render_requirements
from .base import run_role

FLOW = "requirements"
ROLE = "RequirementAnalyst"


# Từ cho thấy một success criterion đang nói về cách làm thay vì kết quả (ví dụ xấu của Spec Kit: API, database...).
IMPLEMENTATION_WORDS = re.compile(
    r"\b(api|endpoint|database|cơ sở dữ liệu|sql|nosql|redis|cache|framework|react|vue|angular|django|laravel|"
    r"server|máy chủ|query|truy vấn|microservice|kubernetes|docker)\b", re.IGNORECASE)


def _normalized(text: str) -> str:
    return re.sub(r"\W+", " ", text.lower()).strip()


def check_against(clarification: list[dict]):
    known = {item["id"] for item in clarification}
    assumed = {item["id"] for item in clarification if item["assumed"]}

    def check(output: RequirementsOutput) -> list[str]:
        problems = []
        for label, ids in (("feature", [f.id for f in output.features]),
                           ("success criterion", [s.id for s in output.success_criteria]),
                           ("NFR", [n.id for n in output.non_functional_requirements])):
            duplicates = sorted({value for value in ids if ids.count(value) > 1})
            problems += [f"duplicate {label} id {value}" for value in duplicates]
        names = [_normalized(f.name) for f in output.features]
        problems += [f"duplicate feature name {f.name}" for f in output.features if names.count(_normalized(f.name)) > 1][:1]
        problems += [f"{f.id} needs why_priority and independent_test" for f in output.features
                     if not f.why_priority.strip() or not f.independent_test.strip()]
        if not output.success_criteria:
            problems.append("add at least one measurable success criterion (SC-1 ...)")
        problems += [f"{sc.id} mentions implementation ({IMPLEMENTATION_WORDS.search(sc.outcome + ' ' + sc.metric).group(0)});"
                     " state a user or business outcome instead" for sc in output.success_criteria
                     if IMPLEMENTATION_WORDS.search(sc.outcome + " " + sc.metric)]
        if len(output.open_questions) > MAX_OPEN_QUESTIONS:
            problems.append(f"at most {MAX_OPEN_QUESTIONS} open questions: keep the most critical (scope, then security"
                            " and privacy, then user experience) and record sensible defaults as assumptions")
        for feature in output.features:
            unknown = [source for source in feature.sources if source != "request" and source not in known]
            if unknown:
                problems.append(f"{feature.id} cites unknown sources {unknown}; use \"request\" or clarification ids")
        cited = {item.clarification_id for item in output.assumptions if item.clarification_id}
        problems += [f"assumption cites unknown clarification id {value}" for value in sorted(cited - known)]
        problems += [f"answer {value} kept the suggested default: record it in assumptions with clarification_id"
                     for value in sorted(assumed - cited)]
        return problems
    return check


async def run(artifacts: Artifacts, runtime, config, *, note: str | None = None) -> dict:
    gate.require_runnable(artifacts, FLOW)
    clarification = artifacts.read_json("clarification.json") or []
    if note:
        artifacts.add_feedback(FLOW, note)
    current = artifacts.read_json("requirements.json")
    payload = {"request": artifacts.request(), "clarification": clarification,
               "coverage": artifacts.read_json("coverage.json") or [],
               "user_feedback": artifacts.feedback_history(FLOW)}
    if note and current:
        payload["current_draft"] = current  # sửa tiếp trên bản nháp theo góp ý, không viết lại từ đầu
    output = await run_role(runtime, FLOW, ROLE, RequirementsOutput, payload, check_against(clarification),
                            retries=config.max_flow_retries)
    data = output.model_dump()
    artifacts.write_json("requirements.json", data)
    path = artifacts.write_text("requirements.md", render_requirements(data))
    gate.invalidate_from(artifacts, FLOW)
    changes = summarize_changes(current, data, FLOW)
    return {"status": "draft", "changes": changes, "run_id": runtime.run_id, "features": len(data["features"]),
            "nfrs": len(data["non_functional_requirements"]), "success_criteria": len(data["success_criteria"]),
            "path": str(path),
            "message": (f"requirements: bản nháp {len(data['features'])} chức năng cốt lõi, "
                        f"{len(data['success_criteria'])} tiêu chí thành công, "
                        f"{len(data['non_functional_requirements'])} NFR. Xem: {path}\n"
                        + (f"  Thay đổi so với bản trước ({len(changes)}):\n" + "\n".join(f"    {line}" for line in changes)
                           + "\n" if changes else "")
                        + f"  Góp ý: revise requirements --workspace ... --note \"...\" | vừa ý: approve requirements")}
