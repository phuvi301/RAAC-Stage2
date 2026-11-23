"""Flow project_manager -> role `project_manager` (Project Manager, tầng Macro).

Thay cho task_decomposer (stub cũ).
Vào:  đặc tả BA đã duyệt (artifacts.read_ba_spec()) + solution_architecture.json đã duyệt + request + workspace_mode +
      góp ý; khi revise thêm bản nháp hiện tại (chỉ phần schema của model, bỏ phần do code tạo).
Ra:   tasks.json (+ tasks.md): DAG các task T1..Tn với metadata khách quan (kind, depends_on, id tham chiếu về BA/SA,
      target_paths, verification_scope, integration_notes, open_assumptions). Sau khi kiểm, CODE (không qua model)
      tính execution_order (topo, tái lập) và cắt context_slice cho từng task từ BA + SA, chép nguyên
      global_tech_stack / global_runtime_contract. assumptions, open_questions (OQ-n), external_integrations (INT-n)
      của BA được lọc theo feature_ids của task (mục không gắn feature = toàn cục, luôn giữ).
PM KHÔNG chấm 4 điểm (Complexity, Ambiguity, Architecture Impact, Security), không gán level hay risk: việc của
Requirement Analyzer just-in-time và Controller ở vòng lặp downstream (TaskSpec extra="forbid" chặn các trường đó).
Luật (bằng code): id/tiêu đề task và id milestone duy nhất; depends_on trỏ task có thật, không tự trỏ, không vòng, có
task gốc; greenfield: có task bootstrap gốc và mọi task khác phụ thuộc (trực tiếp/gián tiếp) vào bootstrap; target_paths
tương đối; id tham chiếu có thật trong BA/SA; AC FR-k.ACm kéo theo FR-k; milestone trỏ task có thật, mỗi task tối đa
một milestone; phủ 100% AC, MOD, NFR security, EC của feature ưu tiên cao; NFR security gắn feature_ids phải nằm ở
ít nhất một task chạm đúng feature đó; không gộp cả dự án lớn vào một task; task feature có FR hoặc AC.
"""

import heapq

from .. import gate
from ..artifacts import Artifacts
from ..contracts import ProjectManagerOutput, TaskSpec
from ..events import summarize_changes
from ..render import render_tasks
from ._checks import (
    duplicates,
    find_cycle,
    id_key,
    validate_relative_path,
    workspace_mode,
)
from .base import run_role
from .solution_architect import OUTPUT_FILE as SA_FILE
from .solution_architect import security_nfr_ids

FLOW = "project_manager"
ROLE = "ProjectManager"
OUTPUT_FILE = "tasks.json"
SCHEMA_VERSION = "tasks-v2"
SHARED_CONSTRAINT_CATEGORIES = ("architecture", "convention")  # áp dụng cho mọi task


def _ids(items) -> set[str]:
    return {item["id"] for item in items or []}


def _acceptance_ids(ba_spec: dict) -> list[str]:
    return [ac["id"] for fr in ba_spec.get("functional_requirements", []) for ac in fr.get("acceptance_criteria", [])]


def _scoped(items, features: set[str]) -> list:
    """Mục toàn cục (không có feature_ids; chuỗi kiểu cũ) hoặc gắn với một feature của task."""
    kept = []
    for item in items or []:
        scope = item.get("feature_ids") if isinstance(item, dict) else None
        if not scope or set(scope) & features:
            kept.append(item)
    return kept


def _text(item) -> str:
    return item["text"] if isinstance(item, dict) else str(item)


def _ancestors(task_id: str, graph: dict[str, list[str]]) -> set[str]:
    seen, stack = set(), list(graph.get(task_id, []))
    while stack:
        node = stack.pop()
        if node in seen or node not in graph:
            continue
        seen.add(node)
        stack.extend(graph[node])
    return seen


def check_against(ba_spec: dict, sa_data: dict, mode: str = "greenfield"):
    features = {feature["id"]: feature for feature in ba_spec.get("features", [])}
    frs = _ids(ba_spec.get("functional_requirements"))
    acceptance = _acceptance_ids(ba_spec)
    edge_cases = {case["id"]: case for case in ba_spec.get("edge_cases", [])}
    nfrs = _ids(ba_spec.get("non_functional_requirements"))
    security = security_nfr_ids(ba_spec)
    modules = [module["id"] for module in sa_data.get("modules", [])]
    references = (("feature_ids", "features", set(features)), ("fr_ids", "functional requirements", frs),
                  ("acceptance_criteria_ids", "acceptance criteria", set(acceptance)),
                  ("edge_case_ids", "edge cases", set(edge_cases)), ("nfr_ids", "NFRs", nfrs),
                  ("module_ids", "modules", set(modules)),
                  ("api_contract_ids", "API contracts", _ids(sa_data.get("api_contracts"))),
                  ("data_model_ids", "data models", _ids(sa_data.get("data_models"))),
                  ("constraint_ids", "global constraints", _ids(sa_data.get("global_constraints"))))

    def check(output: ProjectManagerOutput) -> list[str]:
        tasks = output.tasks
        known = {task.id for task in tasks}
        problems = duplicates("task id", [task.id for task in tasks])
        problems += duplicates("task title", [task.title.strip().lower() for task in tasks])
        problems += duplicates("milestone id", [milestone.id for milestone in output.milestones])

        graph = {}
        for task in tasks:
            if task.id in task.depends_on:
                problems.append(f"task {task.id} depends on itself")
            unknown = [dep for dep in task.depends_on if dep not in known]
            if unknown:
                problems.append(f"task {task.id} depends on unknown tasks {unknown}")
            graph[task.id] = [dep for dep in task.depends_on if dep != task.id]
        cycle = find_cycle(graph)
        if cycle:
            problems.append("task dependency cycle " + " -> ".join(cycle))
        roots = [task for task in tasks if not task.depends_on]
        if not roots:
            problems.append("at least one task must have no dependencies")
        if mode == "greenfield":
            bootstraps = {task.id for task in tasks if task.kind == "bootstrap"}
            if not any(task.kind == "bootstrap" for task in roots):
                problems.append("greenfield workspace: add a bootstrap task with no dependencies that sets up the "
                                "project skeleton, dependencies and test runner")
            elif len(tasks) > 1 and not cycle:
                problems += [f"task {task.id} must depend directly or indirectly on a bootstrap task"
                             for task in tasks if task.kind != "bootstrap"
                             and not (_ancestors(task.id, graph) & bootstraps)]

        for task in tasks:
            for target in task.target_paths:
                error = validate_relative_path(target)
                if error:
                    problems.append(f"task {task.id} target_paths: {error}")
            for field, label, allowed in references:
                unknown = [value for value in getattr(task, field) if value not in allowed]
                if unknown:
                    problems.append(f"task {task.id} refers to unknown {label} {unknown}")
            missing_fr = sorted({ac.split(".")[0] for ac in task.acceptance_criteria_ids} - set(task.fr_ids))
            if missing_fr:
                problems.append(f"task {task.id} lists acceptance criteria of {missing_fr} but not those ids in fr_ids")
            if task.kind == "feature" and not (task.fr_ids or task.acceptance_criteria_ids):
                problems.append(f"feature task {task.id} needs at least one fr_id or acceptance criterion")

        placed: dict[str, list[str]] = {}
        for milestone in output.milestones:
            unknown = [tid for tid in milestone.task_ids if tid not in known]
            if unknown:
                problems.append(f"milestone {milestone.id} refers to unknown tasks {unknown}")
            for tid in milestone.task_ids:
                placed.setdefault(tid, []).append(milestone.id)
        problems += [f"task {tid} appears in several milestones {ids}" for tid, ids in sorted(placed.items())
                     if len(ids) > 1]

        covered_ac = {ac for task in tasks for ac in task.acceptance_criteria_ids}
        problems += [f"acceptance criterion {ac} is not covered by any task" for ac in acceptance
                     if ac not in covered_ac]
        covered_modules = {mid for task in tasks for mid in task.module_ids}
        problems += [f"module {mid} is not covered by any task" for mid in modules if mid not in covered_modules]
        covered_nfrs = {nid for task in tasks for nid in task.nfr_ids}
        problems += [f"security NFR {nid} is not covered by any task" for nid in security if nid not in covered_nfrs]
        for nfr in ba_spec.get("non_functional_requirements", []):
            scope = set(nfr.get("feature_ids") or [])
            holders = [task for task in tasks if nfr["id"] in task.nfr_ids]
            if nfr.get("category") == "security" and scope and holders and not any(
                    scope & set(task.feature_ids) for task in holders):
                problems.append(f"security NFR {nfr['id']} belongs to features {sorted(scope)}: put it on a task "
                                "that covers one of them")
        covered_cases = {cid for task in tasks for cid in task.edge_case_ids}
        problems += [f"edge case {cid} of high-priority feature {case['feature_id']} is not covered by any task"
                     for cid, case in edge_cases.items()
                     if features.get(case.get("feature_id"), {}).get("priority") == "high" and cid not in covered_cases]
        if (len(features) >= 3 and len(frs) >= 4 and len(tasks) < 2):
            problems.append("the project has 3+ features and 4+ functional requirements: split it into several tasks")
        return problems
    return check


# ---------------------------------------------------------------- hydrate bằng code

def execution_order(tasks: list[dict]) -> list[str]:
    """Thứ tự topo (Kahn) tái lập: luôn lấy task sẵn sàng có số thứ tự nhỏ nhất. Giả định DAG đã được kiểm."""
    pending = {task["id"]: set(task["depends_on"]) for task in tasks}
    dependents: dict[str, list[str]] = {tid: [] for tid in pending}
    for tid, deps in pending.items():
        for dep in deps:
            dependents[dep].append(tid)
    ready = [id_key(tid) for tid, deps in pending.items() if not deps]
    heapq.heapify(ready)
    order = []
    while ready:
        _, tid = heapq.heappop(ready)
        order.append(tid)
        for nxt in dependents[tid]:
            pending[nxt].discard(tid)
            if not pending[nxt]:
                heapq.heappush(ready, id_key(nxt))
    return order


def _pick(items, wanted) -> list[dict]:
    wanted = set(wanted)
    return [item for item in items or [] if item.get("id") in wanted]


def build_context_slice(task: dict, ba_spec: dict, sa_data: dict) -> dict:
    """Đúng các mục T_i tham chiếu (giữ nguyên văn và thứ tự gốc của BA/SA) + ràng buộc toàn cục dùng chung."""
    constraints = [item for item in sa_data.get("global_constraints", [])
                   if item["id"] in task["constraint_ids"] or item["category"] in SHARED_CONSTRAINT_CATEGORIES]
    architecture = sa_data.get("security_architecture") or {}
    security_nfrs = set(security_nfr_ids(ba_spec)) | set(architecture.get("nfr_ids", []))
    features = set(task["feature_ids"])
    scoped_security = {fid for nfr in ba_spec.get("non_functional_requirements", [])
                       if nfr.get("category") == "security" for fid in nfr.get("feature_ids") or []}
    touches_security = (bool(security_nfrs & set(task["nfr_ids"]))
                        or any(item["category"] == "security" for item in sa_data.get("global_constraints", [])
                               if item["id"] in task["constraint_ids"])
                        or bool(scoped_security & features))
    return {
        "features": _pick(ba_spec.get("features"), task["feature_ids"]),
        "functional_requirements": _pick(ba_spec.get("functional_requirements"), task["fr_ids"]),
        "business_rules": [rule for rule in ba_spec.get("business_rules", []) if rule.get("feature_id") in features],
        "edge_cases": _pick(ba_spec.get("edge_cases"), task["edge_case_ids"]),
        "non_functional_requirements": _pick(ba_spec.get("non_functional_requirements"), task["nfr_ids"]),
        "modules": _pick(sa_data.get("modules"), task["module_ids"]),
        "api_contracts": _pick(sa_data.get("api_contracts"), task["api_contract_ids"]),
        "data_models": _pick(sa_data.get("data_models"), task["data_model_ids"]),
        "global_constraints": constraints,
        "security_architecture": architecture if touches_security and architecture else None,
        "external_integrations": _scoped(ba_spec.get("external_integrations"), features),
        "relevant_assumptions": [*(_text(item) for item in _scoped(ba_spec.get("assumptions"), features)),
                                 *task["open_assumptions"]],
        "relevant_open_questions": [_text(item) for item in _scoped(ba_spec.get("open_questions"), features)],
    }


def hydrate(output: dict, ba_spec: dict, sa_data: dict, mode: str) -> dict:
    order = execution_order(output["tasks"])
    by_id = {task["id"]: task for task in output["tasks"]}
    return {
        "schema_version": SCHEMA_VERSION,
        "summary": output["summary"],
        "workspace_mode": mode,
        "execution_order": order,
        "global_tech_stack": sa_data["tech_stack"],
        "global_runtime_contract": sa_data["runtime_contract"],
        "milestones": output["milestones"],
        "tasks": [{**by_id[tid], "context_slice": build_context_slice(by_id[tid], ba_spec, sa_data)} for tid in order],
        "open_questions": output["open_questions"],
    }


def model_part(data: dict | None) -> dict | None:
    """Chỉ phần do model viết (schema ProjectManagerOutput/TaskSpec): bỏ context_slice, execution_order, global_*."""
    if not data:
        return None
    part = {key: data[key] for key in ProjectManagerOutput.model_fields if key in data}
    part["tasks"] = [{key: task[key] for key in TaskSpec.model_fields if key in task} for task in data.get("tasks", [])]
    return part


def _for_diff(data: dict | None) -> dict | None:
    if not data:
        return None
    return {**model_part(data), "execution_order": data.get("execution_order", [])}


async def run(artifacts: Artifacts, runtime, config, *, note: str | None = None) -> dict:
    gate.require_runnable(artifacts, FLOW)
    ba_spec = artifacts.read_ba_spec()
    sa_data = artifacts.read_json(SA_FILE)
    mode = workspace_mode(artifacts.workspace)
    if note:
        artifacts.add_feedback(FLOW, note)
    current = artifacts.read_json(OUTPUT_FILE)
    payload = {"request": artifacts.request(), "ba_spec": ba_spec, "solution_architecture": sa_data,
               "workspace_mode": mode, "user_feedback": artifacts.feedback_history(FLOW)}
    if note and current:
        payload["current_draft"] = model_part(current)  # không gửi lại phần do code tạo (tiết kiệm token, tránh copy)
    output = await run_role(runtime, FLOW, ROLE, ProjectManagerOutput, payload, check_against(ba_spec, sa_data, mode),
                            retries=config.max_flow_retries)
    data = hydrate(output.model_dump(), ba_spec, sa_data, mode)
    artifacts.write_json(OUTPUT_FILE, data)
    path = artifacts.write_text("tasks.md", render_tasks(data))
    gate.invalidate_from(artifacts, FLOW)
    changes = summarize_changes(_for_diff(current), _for_diff(data), FLOW)
    return {"status": "draft", "run_id": runtime.run_id, "path": str(path), "changes": changes,
            "tasks": len(data["tasks"]), "execution_order": data["execution_order"],
            "message": (f"project_manager: bản nháp {len(data['tasks'])} task, {len(data['milestones'])} milestone "
                        f"({mode}). Thứ tự: {' → '.join(data['execution_order'])}. Xem: {path}\n"
                        + (f"  Thay đổi so với bản trước ({len(changes)}):\n" + "\n".join(f"    {line}" for line in changes)
                           + "\n" if changes else "")
                        + "  Góp ý: revise project_manager --workspace ... --note \"...\" "
                          "| vừa ý: approve project_manager")}
