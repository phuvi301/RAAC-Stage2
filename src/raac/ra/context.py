"""Gom đầu vào cho Requirement Analyzer bằng code (không qua model), chỉ phần liên quan tới task để tiết kiệm token.

- select_task: lấy task theo id trong tasks.json (+ tên các task nó phụ thuộc).
- spec_context: từ id AC của task tra ngược sang FR, chức năng, quy tắc nghiệp vụ, trường hợp biên trong
  requirement_spec.json, kèm NFR, thực thể chính và ràng buộc. AC không có trong spec thì báo lỗi rõ.
- architecture_outline: mục lục của architecture.md (tiêu đề + số dòng). Agent dựa vào đó để tự đọc đúng mục bằng
  tool read_file/read_files, không gửi nguyên file.
- rubric_text: phần chấm điểm của rubric (mục 2-7), bỏ phần mở đầu và tài liệu tham khảo.
"""

import re
from pathlib import Path

from pydantic import ValidationError

from .contracts import TaskInput

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


def spec_context(spec: dict, task: TaskInput) -> dict:
    """Phần của Requirement Spec liên quan tới task: chỉ FR/AC task phụ trách và chức năng của chúng."""
    requirements = {fr["id"]: fr for fr in spec.get("functional_requirements", [])}
    owner = {ac["id"]: fr_id for fr_id, fr in requirements.items() for ac in fr.get("acceptance_criteria", [])}
    unknown = [ac for ac in task.acceptance_criteria if ac not in owner]
    if unknown:
        raise ContextError(f"Task {task.id} trỏ tới AC không có trong requirement_spec.json: {', '.join(unknown)}")

    wanted = set(task.acceptance_criteria)
    fr_ids = list(dict.fromkeys(owner[ac] for ac in task.acceptance_criteria))
    selected = []
    for fr_id in fr_ids:
        fr = requirements[fr_id]
        selected.append({**fr, "acceptance_criteria": [ac for ac in fr.get("acceptance_criteria", [])
                                                       if ac["id"] in wanted]})
    feature_ids = {fr["feature_id"] for fr in selected} | ({task.feature_id} if task.feature_id else set())
    return {
        "features": [f for f in spec.get("features", []) if f.get("id") in feature_ids],
        "functional_requirements": selected,
        "business_rules": [r for r in spec.get("business_rules", []) if r.get("feature_id") in feature_ids],
        "edge_cases": [c for c in spec.get("edge_cases", []) if c.get("feature_id") in feature_ids],
        "user_flows": [{"id": u["id"], "name": u.get("name", ""), "feature_ids": u.get("feature_ids", [])}
                       for u in spec.get("user_flows", []) if feature_ids & set(u.get("feature_ids", []))],
        "non_functional_requirements": spec.get("non_functional_requirements", []),
        "key_entities": spec.get("key_entities", []),
        "constraints": spec.get("constraints", []),
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
