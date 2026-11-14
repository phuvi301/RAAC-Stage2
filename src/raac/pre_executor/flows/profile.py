"""Flow profile -> role `requirement_profiler` (Requirement Profile).

Vào:  requirements.json đã duyệt (tổng quan, chức năng cốt lõi F-n, NFR) + request + Clarification + góp ý; khi
      revise thêm bản nháp hiện tại.
Ra:   requirement_profile.json (+ .md để đọc) — chi tiết hoá từng chức năng đã duyệt:
      user_flows (UF-n), business_rules (BR-n), functional_requirements (FR-n) kèm acceptance_criteria (FR-n.ACm).
      Tổng quan, features, NFR, ràng buộc, giả định, ngoài phạm vi được CHÉP NGUYÊN từ requirements (bằng code, không
      qua model) để các flow sau chỉ cần đọc Profile. Đây là "hợp đồng" cho tech_select, architecture, task_decomposer.
Vòng duyệt: bản nháp -> góp ý (revise) -> agent sửa tiếp trên bản nháp -> ... -> approve.
Luật (bằng code): id UF/BR/FR/AC duy nhất; AC của FR-n có dạng FR-n.ACm; FR, BR, UF chỉ trỏ feature đã duyệt; mỗi
feature có ít nhất một FR; mỗi feature high priority có ít nhất một user flow.
"""

from .. import gate
from ..artifacts import Artifacts
from ..contracts import ProfileOutput
from ..render import render_profile
from .base import run_role

FLOW = "profile"
ROLE = "RequirementProfiler"
CARRIED = ("overview", "features", "non_functional_requirements", "constraints", "assumptions", "out_of_scope")


def _duplicates(label: str, ids: list[str]) -> list[str]:
    return [f"duplicate {label} id {value}" for value in sorted({value for value in ids if ids.count(value) > 1})]


def check_against(requirements: dict):
    features = {feature["id"]: feature for feature in requirements["features"]}

    def check(output: ProfileOutput) -> list[str]:
        problems = _duplicates("user flow", [flow.id for flow in output.user_flows])
        problems += _duplicates("business rule", [rule.id for rule in output.business_rules])
        problems += _duplicates("functional requirement", [fr.id for fr in output.functional_requirements])
        problems += _duplicates("acceptance criterion",
                                [ac.id for fr in output.functional_requirements for ac in fr.acceptance_criteria])
        for fr in output.functional_requirements:
            if fr.feature_id not in features:
                problems.append(f"{fr.id} refers to unknown feature {fr.feature_id}")
            wrong = [ac.id for ac in fr.acceptance_criteria if not ac.id.startswith(fr.id + ".")]
            if wrong:
                problems.append(f"{fr.id} has acceptance criteria named {wrong}; use {fr.id}.AC1, {fr.id}.AC2 ...")
        for rule in output.business_rules:
            if rule.feature_id not in features:
                problems.append(f"{rule.id} refers to unknown feature {rule.feature_id}")
        for flow in output.user_flows:
            unknown = [fid for fid in flow.feature_ids if fid not in features]
            if unknown:
                problems.append(f"{flow.id} refers to unknown features {unknown}")
        covered = {fr.feature_id for fr in output.functional_requirements}
        problems += [f"feature {fid} has no functional requirement" for fid in features if fid not in covered]
        in_flows = {fid for flow in output.user_flows for fid in flow.feature_ids}
        problems += [f"high-priority feature {fid} appears in no user flow" for fid, feature in features.items()
                     if feature["priority"] == "high" and fid not in in_flows]
        return problems
    return check


async def run(artifacts: Artifacts, runtime, config, *, note: str | None = None) -> dict:
    gate.require_runnable(artifacts, FLOW)
    requirements = artifacts.read_json("requirements.json")
    if note:
        artifacts.add_feedback(FLOW, note)
    current = artifacts.read_json("requirement_profile.json")
    payload = {"request": artifacts.request(), "clarification": artifacts.read_json("clarification.json") or [],
               "requirements": requirements, "user_feedback": artifacts.feedback_history(FLOW)}
    if note and current:
        payload["current_draft"] = {key: current[key] for key in ProfileOutput.model_fields if key in current}
    output = await run_role(runtime, FLOW, ROLE, ProfileOutput, payload, check_against(requirements),
                            retries=config.max_flow_retries)
    detail = output.model_dump()
    data = {**{key: requirements.get(key, []) for key in CARRIED}, **detail,
            "open_questions": [*requirements.get("open_questions", []), *detail["open_questions"]]}
    artifacts.write_json("requirement_profile.json", data)
    path = artifacts.write_text("requirement_profile.md", render_profile(data))
    gate.invalidate_from(artifacts, FLOW)
    criteria = sum(len(fr["acceptance_criteria"]) for fr in data["functional_requirements"])
    return {"status": "draft", "run_id": runtime.run_id, "path": str(path),
            "message": (f"profile: bản nháp {len(data['user_flows'])} user flow, {len(data['business_rules'])} quy tắc "
                        f"nghiệp vụ, {len(data['functional_requirements'])} yêu cầu chi tiết, {criteria} AC. "
                        f"Xem: {path}\n"
                        f"  Góp ý: revise profile --workspace ... --note \"...\" | vừa ý: approve profile")}
