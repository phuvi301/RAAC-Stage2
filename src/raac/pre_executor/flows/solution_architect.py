"""Flow solution_architect -> role `solution_architect` (Solution Architect, tầng Macro).

Thay cho tech_select + architecture + phần runtime/test mục tiêu của environment (các stub cũ).
Vào:  đặc tả BA đã duyệt (artifacts.read_ba_spec(): ba_spec.json; requirement_profile.json của workspace cũ) + request +
      Clarification + workspace_mode (greenfield/brownfield, tính bằng code) + góp ý; khi revise thêm bản nháp hiện tại.
Ra:   solution_architecture.json (+ .md): tech stack, thư mục, module MOD-n, data model DM-n, hợp đồng API-n, kiến trúc
      bảo mật, runtime/test contract, ràng buộc toàn cục CON-n (chỉ-đọc với tầng Micro L1–L3), quyết định ADR-n.
      Không đi vào hàm helper nội bộ hay từng bước sửa file (việc của tầng Micro trên repo thật).
Luật (bằng code): id MOD/DM/API/CON/ADR duy nhất; tên module/thư viện/data model không trùng; thư mục không trùng;
đường dẫn tương đối POSIX; module chỉ phụ thuộc module có thật, không tự phụ thuộc, không vòng; mọi F-n có module phụ
trách và module chỉ trỏ F-n có thật; data model/API trỏ module có thật; API trỏ FR-n có thật; NFR trong
security_architecture có thật; có NFR security thì security_architecture phải nhắc đủ id và có nội dung; env_vars
không chứa giá trị trông như secret thật. Khi BA có id ENT-n/INT-n: data_model.entity_ids chỉ trỏ ENT có thật và mọi
ENT được một data model (entity_ids) hoặc module (owns_entities) nhận; api_contract.integration_ids chỉ trỏ INT có thật
và mọi INT có hợp đồng phủ (workspace cũ không có id thì bỏ qua). Ràng buộc brownfield (giữ stack hiện có) nằm ở
prompt, không kiểm cứng.
"""

import re

from .. import gate
from ..artifacts import Artifacts
from ..contracts import SolutionArchitectureOutput
from ..events import summarize_changes
from ..render import render_solution_architecture
from ._checks import duplicates, find_cycle, validate_relative_path, workspace_mode
from .base import run_role

FLOW = "solution_architect"
ROLE = "SolutionArchitect"
OUTPUT_FILE = "solution_architecture.json"
# Giá trị trông như khoá thật (OpenAI/Anthropic, GitHub, Google, AWS, Slack). Không chép giá trị vào thông điệp lỗi.
SECRET_LIKE = re.compile(r"(sk-[A-Za-z0-9_-]{8,}|ghp_[A-Za-z0-9]{8,}|github_pat_\w{8,}|AIza[0-9A-Za-z_-]{8,}"
                         r"|AKIA[0-9A-Z]{12,}|xox[abprs]-[\w-]{8,})")


def security_nfr_ids(ba_spec: dict) -> list[str]:
    return [nfr["id"] for nfr in ba_spec.get("non_functional_requirements", []) if nfr.get("category") == "security"]


def check_against(ba_spec: dict, mode: str = "greenfield"):
    features = {feature["id"] for feature in ba_spec.get("features", [])}
    frs = {fr["id"] for fr in ba_spec.get("functional_requirements", [])}
    nfrs = {nfr["id"] for nfr in ba_spec.get("non_functional_requirements", [])}
    security = security_nfr_ids(ba_spec)
    entities = [e["id"] for e in ba_spec.get("key_entities", []) if isinstance(e, dict) and e.get("id")]
    integrations = [i["id"] for i in ba_spec.get("external_integrations", []) if isinstance(i, dict) and i.get("id")]

    def check(output: SolutionArchitectureOutput) -> list[str]:
        problems = duplicates("module id", [m.id for m in output.modules])
        problems += duplicates("data model id", [d.id for d in output.data_models])
        problems += duplicates("API contract id", [a.id for a in output.api_contracts])
        problems += duplicates("global constraint id", [c.id for c in output.global_constraints])
        problems += duplicates("decision id", [d.id for d in output.decisions])
        problems += duplicates("module name", [m.name.lower() for m in output.modules])
        problems += duplicates("library", [lib.name.lower() for lib in output.tech_stack.libraries])
        problems += duplicates("data model name", [d.name.lower() for d in output.data_models])
        problems += duplicates("folder path", [f.path.rstrip("/") for f in output.folders])
        for label, item_path in ([("folder", f.path) for f in output.folders]
                                 + [(f"module {m.id}", m.path) for m in output.modules]):
            error = validate_relative_path(item_path)
            if error:
                problems.append(f"{label}: {error}")

        modules = {m.id for m in output.modules}
        graph = {}
        for module in output.modules:
            if module.id in module.depends_on:
                problems.append(f"module {module.id} depends on itself")
            unknown = [dep for dep in module.depends_on if dep not in modules]
            if unknown:
                problems.append(f"module {module.id} depends on unknown modules {unknown}")
            graph[module.id] = [dep for dep in module.depends_on if dep != module.id]
            missing = [fid for fid in module.feature_ids if fid not in features]
            if missing:
                problems.append(f"module {module.id} refers to unknown features {missing}")
        cycle = find_cycle(graph)
        if cycle:
            problems.append("module dependency cycle " + " -> ".join(cycle))
        owned = {fid for module in output.modules for fid in module.feature_ids}
        problems += [f"feature {fid} is not owned by any module" for fid in sorted(features - owned)]

        for model in output.data_models:
            if model.module_id not in modules:
                problems.append(f"data model {model.id} refers to unknown module {model.module_id}")
        for contract in output.api_contracts:
            if contract.module_id not in modules:
                problems.append(f"API contract {contract.id} refers to unknown module {contract.module_id}")
            missing = [frid for frid in contract.fr_ids if frid not in frs]
            if missing:
                problems.append(f"API contract {contract.id} refers to unknown functional requirements {missing}")

        if entities:
            for model in output.data_models:
                unknown = [eid for eid in model.entity_ids if eid not in entities]
                if unknown:
                    problems.append(f"data model {model.id} refers to unknown entities {unknown}")
            mapped = ({eid for model in output.data_models for eid in model.entity_ids}
                      | {eid for module in output.modules for eid in module.owns_entities})
            problems += [f"entity {eid} is not mapped to any data model entity_ids or module owns_entities"
                         for eid in entities if eid not in mapped]
        if integrations:
            for contract in output.api_contracts:
                unknown = [iid for iid in contract.integration_ids if iid not in integrations]
                if unknown:
                    problems.append(f"API contract {contract.id} refers to unknown integrations {unknown}")
            served = {iid for contract in output.api_contracts for iid in contract.integration_ids}
            problems += [f"integration {iid} is not covered by any API contract integration_ids"
                         for iid in integrations if iid not in served]

        architecture = output.security_architecture
        missing = [nid for nid in architecture.nfr_ids if nid not in nfrs]
        if missing:
            problems.append(f"security_architecture refers to unknown NFRs {missing}")
        if security:
            uncovered = [nid for nid in security if nid not in architecture.nfr_ids]
            if uncovered:
                problems.append(f"security_architecture.nfr_ids must include the security NFRs {uncovered}")
            if not (architecture.auth_strategy.strip() or architecture.authorization_model.strip()
                    or architecture.sensitive_data_rules or architecture.trust_boundaries):
                problems.append("security NFRs exist: describe auth, authorization, sensitive data rules or trust "
                                "boundaries in security_architecture")
        for name, value in output.runtime_contract.env_vars.items():
            if SECRET_LIKE.search(value):
                problems.append(f"env var {name} looks like a real secret: describe it instead of giving a value")
        return problems
    return check


async def run(artifacts: Artifacts, runtime, config, *, note: str | None = None) -> dict:
    gate.require_runnable(artifacts, FLOW)
    ba_spec = artifacts.read_ba_spec()
    mode = workspace_mode(artifacts.workspace)
    if note:
        artifacts.add_feedback(FLOW, note)
    current = artifacts.read_json(OUTPUT_FILE)
    payload = {"request": artifacts.request(), "clarification": artifacts.read_json("clarification.json") or [],
               "ba_spec": ba_spec, "workspace_mode": mode, "user_feedback": artifacts.feedback_history(FLOW)}
    if note and current:
        payload["current_draft"] = current  # sửa tiếp trên bản nháp, giữ phần người dùng không yêu cầu đổi
    output = await run_role(runtime, FLOW, ROLE, SolutionArchitectureOutput, payload, check_against(ba_spec, mode),
                            retries=config.max_flow_retries)
    data = output.model_dump()
    artifacts.write_json(OUTPUT_FILE, data)
    path = artifacts.write_text("solution_architecture.md", render_solution_architecture(data))
    gate.invalidate_from(artifacts, FLOW)
    changes = summarize_changes(current, data, FLOW)
    return {"status": "draft", "run_id": runtime.run_id, "path": str(path), "changes": changes,
            "workspace_mode": mode,
            "message": (f"solution_architect: bản nháp {data['tech_stack']['language']['name']}, "
                        f"{len(data['modules'])} module, {len(data['data_models'])} data model, "
                        f"{len(data['api_contracts'])} hợp đồng API, {len(data['global_constraints'])} ràng buộc "
                        f"({mode}). Xem: {path}\n"
                        + (f"  Thay đổi so với bản trước ({len(changes)}):\n" + "\n".join(f"    {line}" for line in changes)
                           + "\n" if changes else "")
                        + "  Góp ý: revise solution_architect --workspace ... --note \"...\" "
                          "| vừa ý: approve solution_architect")}
