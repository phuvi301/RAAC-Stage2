"""Gom đầu vào cho Requirement Analyzer bằng code (không qua model), chỉ phần liên quan tới task để tiết kiệm token.

- select_task: lấy task theo id trong tasks.json (+ tên các task nó phụ thuộc).
- spec_context: từ id AC (và FR/feature/EC/NFR) của task tra ngược trong ba_spec.json sang FR, chức năng, quy tắc
  nghiệp vụ, trường hợp biên, user flow; kèm NFR, tích hợp ngoài, giả định, câu hỏi mở của các chức năng đó (hoặc toàn
  cục), thực thể chính và ràng buộc. Id không có trong spec thì báo lỗi rõ.
- architecture_outline: mục lục của solution_architecture.md (tiêu đề + số dòng). Agent dựa vào đó để tự đọc đúng
  mục bằng tool read_file/read_files, không gửi nguyên file.
- rubric_text: phần chấm điểm của rubric (mục 2-7), bỏ phần mở đầu và tài liệu tham khảo.
- prompt_with_rubric / prompt_in_sync: ghép rubric vào cuối prompt của role (system prompt) và kiểm còn khớp.
"""

import re
from pathlib import Path

from pydantic import ValidationError

from .contracts import RUBRIC_MARKER, TaskInput

HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")


class ContextError(ValueError):
    pass


def select_task(tasks_data: dict | list, task_id: str) -> tuple[TaskInput, list[dict]]:
    items = tasks_data.get("tasks", []) if isinstance(tasks_data, dict) else tasks_data
    by_id = {str(item.get("id")): item for item in items if isinstance(item, dict)}
    if task_id not in by_id:
        known = ", ".join(sorted(by_id)) or "(trống)"
        raise ContextError(f"Không có task {task_id} trong tasks.json. Các task có: {known}")
    try:
        task = TaskInput.model_validate(by_id[task_id])
    except ValidationError as exc:
        fields = ", ".join(".".join(map(str, error["loc"])) for error in exc.errors())
        raise ContextError(f"Task {task_id} trong tasks.json thiếu hoặc sai trường: {fields}") from None
    dependencies = [{"id": dep, "title": by_id.get(dep, {}).get("title", "(không có trong tasks.json)")}
                    for dep in task.depends_on]
    return task, dependencies


def _scoped(items, features: set[str]) -> list:
    """Mục toàn cục (feature_ids rỗng hoặc chuỗi) hoặc gắn với một chức năng của task."""
    return [item for item in items or []
            if not isinstance(item, dict) or not item.get("feature_ids") or features & set(item["feature_ids"])]


def entity_ids(architecture: dict | None, task: TaskInput) -> set[str] | None:
    """ENT-n mà task chạm: theo data model của task (entity_ids), không ra gì thì theo module (owns_entities).
    None khi không có kiến trúc dạng json: khi đó gửi mọi thực thể."""
    if not architecture:
        return None
    # data model chỉ đúng hơn module (kiểu MVC: một module model sở hữu mọi thực thể); data model không gắn thực thể
    # nào thì mới dựa vào module.
    found = {eid for model in architecture.get("data_models", []) if model.get("id") in task.data_model_ids
             for eid in model.get("entity_ids", [])}
    return found or {eid for module in architecture.get("modules", []) if module.get("id") in task.module_ids
                     for eid in module.get("owns_entities", [])}


def spec_context(spec: dict, task: TaskInput, entities: set[str] | None = None) -> dict:
    """Phần của ba_spec.json liên quan tới task: chỉ FR/AC task phụ trách và chức năng của chúng.
    NFR: các NFR task trỏ tới và NFR gắn với chức năng của task (NFR toàn hệ thống đã được PM chọn vào nfr_ids
    nếu cần). Thực thể: chỉ các ENT task chạm qua data model/module (entities), hoặc tất cả khi không biết."""
    requirements = {fr["id"]: fr for fr in spec.get("functional_requirements", [])}
    owner = {ac["id"]: fr_id for fr_id, fr in requirements.items() for ac in fr.get("acceptance_criteria", [])}
    known = {"AC": set(owner), "FR": set(requirements),
             "feature": {f["id"] for f in spec.get("features", [])},
             "EC": {c["id"] for c in spec.get("edge_cases", [])},
             "NFR": {n["id"] for n in spec.get("non_functional_requirements", [])}}
    for label, ids in (("AC", task.acceptance_criteria_ids), ("FR", task.fr_ids), ("feature", task.feature_ids),
                       ("EC", task.edge_case_ids), ("NFR", task.nfr_ids)):
        unknown = [item for item in ids if item not in known[label]]
        if unknown:
            raise ContextError(f"Task {task.id} trỏ tới {label} không có trong ba_spec.json: {', '.join(unknown)}")

    wanted = set(task.acceptance_criteria_ids)
    fr_ids = list(dict.fromkeys([*(owner[ac] for ac in task.acceptance_criteria_ids), *task.fr_ids]))
    selected = []
    for fr_id in fr_ids:
        fr = requirements[fr_id]
        mine = [ac for ac in fr.get("acceptance_criteria", []) if ac["id"] in wanted]
        selected.append({**fr, "acceptance_criteria": mine or fr.get("acceptance_criteria", [])})
    features = {fr["feature_id"] for fr in selected} | set(task.feature_ids)
    edge_ids, nfr_ids = set(task.edge_case_ids), set(task.nfr_ids)
    return {
        "features": [f for f in spec.get("features", []) if f.get("id") in features],
        "functional_requirements": selected,
        "business_rules": [r for r in spec.get("business_rules", []) if r.get("feature_id") in features],
        "edge_cases": [c for c in spec.get("edge_cases", [])
                       if c.get("id") in edge_ids or c.get("feature_id") in features],
        "user_flows": [{"id": u["id"], "name": u.get("name", ""), "feature_ids": u.get("feature_ids", [])}
                       for u in spec.get("user_flows", []) if features & set(u.get("feature_ids", []))],
        "non_functional_requirements": [n for n in spec.get("non_functional_requirements", [])
                                        if n.get("id") in nfr_ids or features & set(n.get("feature_ids") or [])],
        "external_integrations": _scoped(spec.get("external_integrations"), features),
        "key_entities": [e for e in spec.get("key_entities", [])
                         if entities is None or e.get("id") in entities or e.get("name") in entities],
        "constraints": spec.get("constraints", []),
        "assumptions": _scoped(spec.get("assumptions"), features),
        "open_questions": _scoped(spec.get("open_questions"), features),
    }


def architecture_outline(text: str) -> list[dict]:
    """[{line, level, title}] của mọi tiêu đề Markdown (bỏ qua khối code), để agent đọc đúng khoảng dòng."""
    outline, fenced = [], False
    for number, line in enumerate(text.splitlines(), start=1):
        if line.lstrip().startswith("```"):
            fenced = not fenced
            continue
        match = None if fenced else HEADING.match(line)
        if match:
            outline.append({"line": number, "level": len(match.group(1)), "title": match.group(2)})
    total = len(text.splitlines())
    for index, item in enumerate(outline):  # mỗi mục kéo dài tới tiêu đề kế tiếp
        item["end_line"] = (outline[index + 1]["line"] - 1) if index + 1 < len(outline) else total
    return outline


def rubric_text(path: Path) -> str:
    """Mục 2-7 của rubric (ranh giới trục, định nghĩa, yếu tố, thang điểm, ví dụ)."""
    text = Path(path).read_text(encoding="utf-8-sig")
    start = re.search(r"^# 2\.", text, re.MULTILINE)
    end = re.search(r"^# 8\.", text, re.MULTILINE)
    if not start:
        raise ContextError(f"Rubric {path} không có mục '# 2.'")
    return text[start.start():end.start() if end else len(text)].strip()


def prompt_with_rubric(prompt: str, rubric: str) -> str:
    """Phần hướng dẫn của prompt (trước RUBRIC_MARKER) + marker + rubric mục 2-7."""
    instructions = prompt.split(RUBRIC_MARKER, 1)[0].rstrip()
    return f"{instructions}\n\n{RUBRIC_MARKER}\n\n# RAAC 4-axis rubric (sections 2-7)\n\n{rubric}\n"


def prompt_in_sync(prompt_path: Path, rubric_path: Path) -> bool:
    prompt = Path(prompt_path).read_text(encoding="utf-8-sig")
    return prompt.strip() == prompt_with_rubric(prompt, rubric_text(rubric_path)).strip()


def sync_prompt(prompt_path: Path, rubric_path: Path) -> bool:
    """Ghi lại phần rubric trong prompt của role theo file rubric. Trả True nếu có thay đổi."""
    prompt = Path(prompt_path).read_text(encoding="utf-8-sig")
    updated = prompt_with_rubric(prompt, rubric_text(rubric_path))
    if prompt.strip() == updated.strip():
        return False
    Path(prompt_path).write_text(updated, encoding="utf-8")
    return True
