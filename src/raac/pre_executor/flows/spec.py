"""Flow spec -> role `business_analyst` (Business Analyst): sinh thẳng Requirement Spec.

Vào:  request (yêu cầu thô hoặc spec) + Clarification đã chốt (clarification.json) + độ phủ cuối (coverage.json)
      + góp ý của người dùng; khi revise thêm bản nháp hiện tại.
Ra:   requirement_spec.json (+ requirement_spec.md để đọc): tổng quan; chức năng cốt lõi F-n (lý do ưu tiên, cách kiểm
      độc lập, căn cứ); tiêu chí thành công SC-n; thực thể chính; NFR-n (ISO/IEC 25010); user flow UF-n; quy tắc nghiệp
      vụ BR-n; yêu cầu chi tiết FR-n (câu chỉ thị) kèm AC FR-n.ACm (Given/When/Then); trường hợp biên EC-n; ràng buộc;
      giả định; ngoài phạm vi; câu hỏi mở. Xem contracts.RequirementSpecOutput. Là đầu vào chuẩn cho các flow thiết kế.
Vòng duyệt: bản nháp -> góp ý (revise) -> agent sửa tiếp trên bản nháp -> ... -> approve.
Tham khảo: GitHub Spec Kit (`templates/spec-template.md`, `templates/commands/specify.md`), ISO/IEC 25010, Gherkin,
EARS (Mavin), MetaGPT (ProductManager viết PRD: mục tiêu, user story, requirement pool P0-P2, "Anything UNCLEAR").
Luật (bằng code): id F/SC/NFR/UF/BR/FR/AC/EC duy nhất; tên chức năng không trùng; mỗi chức năng có lý do ưu tiên và
cách kiểm độc lập; có ít nhất một SC và SC không nhắc công nghệ; tối đa 3 câu hỏi mở; căn cứ chỉ là "request" hoặc id
câu hỏi clarify có thật; mọi câu clarify giữ khuyến nghị phải có trong giả định; AC của FR-n đặt tên FR-n.ACm; FR, BR,
UF, EC chỉ trỏ chức năng có thật; mỗi chức năng có ít nhất một FR; chức năng ưu tiên cao có user flow và trường hợp biên.
"""

import re

from .. import gate
from ..artifacts import Artifacts
from ..contracts import MAX_OPEN_QUESTIONS, RequirementSpecOutput
from ..events import summarize_changes
from ..render import render_spec
from .base import run_role

FLOW = "spec"
ROLE = "BusinessAnalyst"

# Từ cho thấy một success criterion đang nói về cách làm thay vì kết quả (ví dụ xấu của Spec Kit: API, database...).
IMPLEMENTATION_WORDS = re.compile(
    r"\b(api|endpoint|database|cơ sở dữ liệu|sql|nosql|redis|cache|framework|react|vue|angular|django|laravel|"
    r"server|máy chủ|query|truy vấn|microservice|kubernetes|docker)\b", re.IGNORECASE)


def _normalized(text: str) -> str:
    return re.sub(r"\W+", " ", text.lower()).strip()


def _duplicates(label: str, ids: list[str]) -> list[str]:
    return [f"duplicate {label} id {value}" for value in sorted({value for value in ids if ids.count(value) > 1})]


def check_against(clarification: list[dict]):
    known = {item["id"] for item in clarification}
    assumed = {item["id"] for item in clarification if item["assumed"]}

    def check(output: RequirementSpecOutput) -> list[str]:
        features = {feature.id: feature for feature in output.features}
        problems = _duplicates("feature", [f.id for f in output.features])
        problems += _duplicates("success criterion", [s.id for s in output.success_criteria])
        problems += _duplicates("NFR", [n.id for n in output.non_functional_requirements])
        problems += _duplicates("user flow", [flow.id for flow in output.user_flows])
        problems += _duplicates("business rule", [rule.id for rule in output.business_rules])
        problems += _duplicates("functional requirement", [fr.id for fr in output.functional_requirements])
        problems += _duplicates("acceptance criterion",
                                [ac.id for fr in output.functional_requirements for ac in fr.acceptance_criteria])
        problems += _duplicates("edge case", [case.id for case in output.edge_cases])

        # Tổng quan và chức năng cốt lõi
        names = [_normalized(f.name) for f in output.features]
        problems += [f"duplicate feature name {f.name}" for f in output.features
                     if names.count(_normalized(f.name)) > 1][:1]
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

        # Chi tiết từng chức năng
        for fr in output.functional_requirements:
            if fr.feature_id not in features:
                problems.append(f"{fr.id} refers to unknown feature {fr.feature_id}")
            wrong = [ac.id for ac in fr.acceptance_criteria if not ac.id.startswith(fr.id + ".")]
            if wrong:
                problems.append(f"{fr.id} has acceptance criteria named {wrong}; use {fr.id}.AC1, {fr.id}.AC2 ...")
        problems += [f"{rule.id} refers to unknown feature {rule.feature_id}" for rule in output.business_rules
                     if rule.feature_id not in features]
        problems += [f"{case.id} refers to unknown feature {case.feature_id}" for case in output.edge_cases
                     if case.feature_id not in features]
        for flow in output.user_flows:
            unknown = [fid for fid in flow.feature_ids if fid not in features]
            if unknown:
                problems.append(f"{flow.id} refers to unknown features {unknown}")
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
    if note:
        artifacts.add_feedback(FLOW, note)
    current = artifacts.read_json("requirement_spec.json")
    payload = {"request": artifacts.request(), "clarification": clarification,
               "coverage": artifacts.read_json("coverage.json") or [],
               "user_feedback": artifacts.feedback_history(FLOW)}
    if note and current:
        payload["current_draft"] = current  # sửa tiếp trên bản nháp theo góp ý, không viết lại từ đầu
    output = await run_role(runtime, FLOW, ROLE, RequirementSpecOutput, payload, check_against(clarification),
                            retries=config.max_flow_retries)
    data = output.model_dump()
    artifacts.write_json("requirement_spec.json", data)
    path = artifacts.write_text("requirement_spec.md", render_spec(data))
    gate.invalidate_from(artifacts, FLOW)
    changes = summarize_changes(current, data, FLOW)
    criteria = sum(len(fr["acceptance_criteria"]) for fr in data["functional_requirements"])
    return {"status": "draft", "changes": changes, "run_id": runtime.run_id, "path": str(path),
            "message": (f"spec: bản nháp {len(data['features'])} chức năng, {len(data['success_criteria'])} tiêu chí "
                        f"thành công, {len(data['non_functional_requirements'])} NFR, {len(data['user_flows'])} user "
                        f"flow, {len(data['functional_requirements'])} yêu cầu, {criteria} AC, "
                        f"{len(data['edge_cases'])} trường hợp biên. Xem: {path}\n"
                        + (f"  Thay đổi so với bản trước ({len(changes)}):\n" + "\n".join(f"    {line}" for line in changes)
                           + "\n" if changes else "")
                        + "  Góp ý: revise spec --workspace ... --note \"...\" | vừa ý: approve spec")}
