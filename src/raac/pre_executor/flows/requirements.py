"""Flow requirements -> role `requirement_analyst` (Product Requirement Analyst).

Vào:  request + Clarification đã chốt (clarification.json) + góp ý của người dùng; khi revise thêm bản nháp hiện tại.
Ra:   requirements.json (+ requirements.md để đọc): tổng quan sản phẩm, CHỨC NĂNG CỐT LÕI (F-n) và YÊU CẦU PHI CHỨC
      NĂNG (NFR-n có metric), ràng buộc, giả định, ngoài phạm vi, câu hỏi mở. Xem contracts.RequirementsOutput.
      Chi tiết từng chức năng (user flow, quy tắc nghiệp vụ, yêu cầu chi tiết, AC) là việc của flow profile.
Vòng duyệt: bản nháp -> người dùng góp ý (revise) -> agent sửa tiếp trên bản nháp -> ... -> approve -> profile chạy.
Luật (bằng code): id F-n/NFR-n duy nhất; tên chức năng không trùng; căn cứ (`sources`) chỉ là "request" hoặc id câu
hỏi clarify có thật; mọi câu clarify người dùng để nguyên gợi ý phải có trong assumptions (`clarification_id`).
"""

import re

from .. import gate
from ..artifacts import Artifacts
from ..contracts import RequirementsOutput
from ..render import render_requirements
from .base import run_role

FLOW = "requirements"
ROLE = "RequirementAnalyst"


def _normalized(text: str) -> str:
    return re.sub(r"\W+", " ", text.lower()).strip()


def check_against(clarification: list[dict]):
    known = {item["id"] for item in clarification}
    assumed = {item["id"] for item in clarification if item["assumed"]}

    def check(output: RequirementsOutput) -> list[str]:
        problems = []
        for label, ids in (("feature", [f.id for f in output.features]),
                           ("NFR", [n.id for n in output.non_functional_requirements])):
            duplicates = sorted({value for value in ids if ids.count(value) > 1})
            problems += [f"duplicate {label} id {value}" for value in duplicates]
        names = [_normalized(f.name) for f in output.features]
        problems += [f"duplicate feature name {f.name}" for f in output.features if names.count(_normalized(f.name)) > 1][:1]
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
               "user_feedback": artifacts.feedback_history(FLOW)}
    if note and current:
        payload["current_draft"] = current  # sửa tiếp trên bản nháp theo góp ý, không viết lại từ đầu
    output = await run_role(runtime, FLOW, ROLE, RequirementsOutput, payload, check_against(clarification),
                            retries=config.max_flow_retries)
    data = output.model_dump()
    artifacts.write_json("requirements.json", data)
    path = artifacts.write_text("requirements.md", render_requirements(data))
    gate.invalidate_from(artifacts, FLOW)
    return {"status": "draft", "run_id": runtime.run_id, "features": len(data["features"]),
            "nfrs": len(data["non_functional_requirements"]), "path": str(path),
            "message": (f"requirements: bản nháp {len(data['features'])} chức năng cốt lõi, "
                        f"{len(data['non_functional_requirements'])} NFR. Xem: {path}\n"
                        f"  Góp ý: revise requirements --workspace ... --note \"...\" | vừa ý: approve requirements")}
