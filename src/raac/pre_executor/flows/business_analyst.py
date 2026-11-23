"""Flow business_analyst -> role `business_analyst` (Business Analyst; gộp flow requirements + profile cũ).

Vào:  request + Clarification đã chốt (clarification.json) + coverage.json + workspace_mode + góp ý; khi revise thêm
      bản nháp hiện tại.
Ra:   ba_spec.json (+ ba_spec.md để đọc): đặc tả nghiệp vụ đầy đủ, mô tả WHAT (không chọn công nghệ):
      overview, features F-n, success_criteria SC-n, key_entities ENT-n, NFR-n, external_integrations INT-n,
      user_flows UF-n, business_rules BR-n, functional_requirements FR-n + AC FR-n.ACm (Given/When/Then), edge_cases
      EC-n, constraints, assumptions, out_of_scope, open_questions OQ-n. NFR/INT/assumption/OQ gắn `feature_ids`
      (rỗng = toàn hệ thống) để PM cắt context_slice đúng phạm vi từng task. Đầu vào của solution_architect và PM.
Tên "Requirement Profile" chỉ dành cho bộ 4 điểm của Requirement Analyzer ở tầng Micro (Proposal Mục 6.3).
Vòng duyệt: bản nháp -> góp ý (revise) -> agent sửa tiếp trên bản nháp -> ... -> approve.
Tham khảo: GitHub Spec Kit (spec-template, specify), ISO/IEC 25010 (NFR), EARS (câu chỉ thị), Gherkin (AC).
Luật (bằng code): id F/SC/ENT/NFR/INT/UF/BR/FR/AC/EC/OQ duy nhất; tên chức năng và thực thể không trùng; mỗi chức năng
có lý do ưu tiên + cách kiểm độc lập; có ít nhất một success criterion và không nêu tên công nghệ/vendor; căn cứ
`sources` chỉ là "request" hoặc Qn có thật; clarification_id có thật; mọi câu clarify giữ khuyến nghị phải có trong
assumptions; tối đa 5 câu hỏi mở; mọi feature_id/feature_ids trỏ F-n có thật; AC của FR-k đặt tên FR-k.ACm; mỗi
chức năng có FR; chức năng ưu tiên cao có user flow và trường hợp biên.
"""

import re

from .. import gate
from ..artifacts import Artifacts
from ..contracts import MAX_OPEN_QUESTIONS, BusinessAnalystOutput
from ..events import summarize_changes
from ..render import render_ba_spec
from ._checks import duplicates, workspace_mode
from .base import run_role

FLOW = "business_analyst"
ROLE = "BusinessAnalyst"
OUTPUT_FILE = "ba_spec.json"

# Chỉ chặn tên công nghệ/framework/vendor cụ thể trong success criteria; không chặn "api", "endpoint", "cli",
# "server" vì sản phẩm có thể chính là một REST API hay công cụ dòng lệnh.
TECH_VENDOR_WORDS = re.compile(
    r"\b(react|vue|angular|svelte|nextjs|django|fastapi|flask|laravel|spring|express|nestjs|"
    r"postgres|postgresql|mysql|sqlite|mongodb|redis|kafka|rabbitmq|elasticsearch|"
    r"docker|kubernetes|k8s|aws|gcp|azure|graphql|grpc)\b",
    re.IGNORECASE,
)


def _normalized(text: str) -> str:
    return re.sub(r"\W+", " ", text.lower()).strip()


def check_against(clarification: list[dict]):
    known = {item["id"] for item in clarification}
    assumed = {item["id"] for item in clarification if item.get("assumed")}

    def check(output: BusinessAnalystOutput) -> list[str]:
        acceptance = [ac.id for fr in output.functional_requirements for ac in fr.acceptance_criteria]
        problems = []
        for label, ids in (("feature id", [f.id for f in output.features]),
                           ("success criterion id", [s.id for s in output.success_criteria]),
                           ("entity id", [e.id for e in output.key_entities]),
                           ("NFR id", [n.id for n in output.non_functional_requirements]),
                           ("integration id", [i.id for i in output.external_integrations]),
                           ("user flow id", [u.id for u in output.user_flows]),
                           ("business rule id", [b.id for b in output.business_rules]),
                           ("functional requirement id", [fr.id for fr in output.functional_requirements]),
                           ("acceptance criterion id", acceptance),
                           ("edge case id", [e.id for e in output.edge_cases]),
                           ("open question id", [q.id for q in output.open_questions])):
            problems += duplicates(label, ids)
        problems += duplicates("feature name", [_normalized(f.name) for f in output.features])
        problems += duplicates("entity name", [_normalized(e.name) for e in output.key_entities])

        problems += [f"{f.id} needs why_priority and independent_test" for f in output.features
                     if not f.why_priority.strip() or not f.independent_test.strip()]
        if not output.success_criteria:
            problems.append("add at least one measurable success criterion (SC-1 ...)")
        for sc in output.success_criteria:
            found = TECH_VENDOR_WORDS.search(sc.outcome + " " + sc.metric)
            if found:
                problems.append(f"{sc.id} names a technology ({found.group(0)}); state a user or business outcome")

        for feature in output.features:
            unknown = [source for source in feature.sources if source != "request" and source not in known]
            if unknown:
                problems.append(f"{feature.id} cites unknown sources {unknown}; use \"request\" or clarification ids")
        cited = {item.clarification_id for item in output.assumptions if item.clarification_id}
        problems += [f"assumption cites unknown clarification id {value}" for value in sorted(cited - known)]
        problems += [f"answer {value} kept the suggested default: record it in assumptions with clarification_id"
                     for value in sorted(assumed - cited)]
        if len(output.open_questions) > MAX_OPEN_QUESTIONS:
            problems.append(f"at most {MAX_OPEN_QUESTIONS} open questions: keep the ones that change scope, security "
                            "or integration and record sensible defaults as assumptions")

        features = {f.id: f for f in output.features}
        single = ([("user flow", u.id, u.feature_ids) for u in output.user_flows]
                  + [("business rule", b.id, [b.feature_id]) for b in output.business_rules]
                  + [("functional requirement", fr.id, [fr.feature_id]) for fr in output.functional_requirements]
                  + [("edge case", e.id, [e.feature_id]) for e in output.edge_cases]
                  + [("NFR", n.id, n.feature_ids) for n in output.non_functional_requirements]
                  + [("integration", i.id, i.feature_ids) for i in output.external_integrations]
                  + [("open question", q.id, q.feature_ids) for q in output.open_questions]
                  + [("assumption", f"#{index}", a.feature_ids)
                     for index, a in enumerate(output.assumptions, start=1)])
        for label, item_id, feature_ids in single:
            unknown = [fid for fid in feature_ids if fid not in features]
            if unknown:
                problems.append(f"{label} {item_id} refers to unknown features {unknown}")
        for fr in output.functional_requirements:
            wrong = [ac.id for ac in fr.acceptance_criteria if not ac.id.startswith(fr.id + ".")]
            if wrong:
                problems.append(f"{fr.id} has acceptance criteria named {wrong}; use {fr.id}.AC1, {fr.id}.AC2 ...")

        covered = {fr.feature_id for fr in output.functional_requirements}
        problems += [f"feature {fid} has no functional requirement" for fid in features if fid not in covered]
        in_flows = {fid for flow in output.user_flows for fid in flow.feature_ids}
        with_cases = {case.feature_id for case in output.edge_cases}
        for fid, feature in features.items():
            if feature.priority == "high" and fid not in in_flows:
                problems.append(f"high-priority feature {fid} appears in no user flow")
            if feature.priority == "high" and fid not in with_cases:
                problems.append(f"high-priority feature {fid} has no edge case")
        return problems
    return check


async def run(artifacts: Artifacts, runtime, config, *, note: str | None = None) -> dict:
    gate.require_runnable(artifacts, FLOW)
    clarification = artifacts.read_json("clarification.json") or []
    mode = workspace_mode(artifacts.workspace)
    if note:
        artifacts.add_feedback(FLOW, note)
    current = artifacts.read_json(OUTPUT_FILE)
    payload = {"request": artifacts.request(), "clarification": clarification,
               "coverage": artifacts.read_json("coverage.json") or [], "workspace_mode": mode,
               "user_feedback": artifacts.feedback_history(FLOW)}
    if note and current:
        payload["current_draft"] = current  # sửa tiếp trên bản nháp theo góp ý, không viết lại từ đầu
    output = await run_role(runtime, FLOW, ROLE, BusinessAnalystOutput, payload, check_against(clarification),
                            retries=config.max_flow_retries)
    data = output.model_dump()
    artifacts.write_json(OUTPUT_FILE, data)
    path = artifacts.write_text("ba_spec.md", render_ba_spec(data))
    gate.invalidate_from(artifacts, FLOW)
    changes = summarize_changes(current, data, FLOW)
    criteria = sum(len(fr["acceptance_criteria"]) for fr in data["functional_requirements"])
    return {"status": "draft", "run_id": runtime.run_id, "path": str(path), "changes": changes,
            "features": len(data["features"]), "functional_requirements": len(data["functional_requirements"]),
            "acceptance_criteria": criteria,
            "message": (f"business_analyst: bản nháp {len(data['features'])} chức năng, "
                        f"{len(data['functional_requirements'])} yêu cầu chi tiết, {criteria} AC, "
                        f"{len(data['non_functional_requirements'])} NFR, {len(data['edge_cases'])} trường hợp biên, "
                        f"{len(data['key_entities'])} thực thể, {len(data['external_integrations'])} tích hợp ngoài, "
                        f"{len(data['open_questions'])} câu hỏi mở. Xem: {path}\n"
                        + (f"  Thay đổi so với bản trước ({len(changes)}):\n" + "\n".join(f"    {line}" for line in changes)
                           + "\n" if changes else "")
                        + "  Góp ý: revise business_analyst --workspace ... --note \"...\" "
                          "| vừa ý: approve business_analyst")}
