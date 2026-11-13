"""One folder per project: the machine-readable artifacts (*.json) and a Markdown view of each for people.

runs/<project>/
  request.md                    the user's description
  clarify.json, answers.md      questions and the editable answers
  spec.json, spec.md            functional + non-functional requirements
  architecture.json, architecture.md
  tasks.json, tasks.md          the task graph
  state.json                    phase, approvals, task status
  llm.jsonl                     trace of every model call
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from .models import Answer, Architecture, Clarification, FlowState, Spec, TaskGraph
from .task_graph import problems, topological_order


class Store:
    def __init__(self, folder: Path):
        self.folder = Path(folder)

    # ---------------------------------------------------------------- basics
    def path(self, name: str) -> Path:
        return self.folder / name

    def exists(self, name: str) -> bool:
        return self.path(name).is_file()

    def write_text(self, name: str, text: str) -> Path:
        self.folder.mkdir(parents=True, exist_ok=True)
        target = self.path(name)
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_text(text, encoding="utf-8")
        temporary.replace(target)
        return target

    def read_text(self, name: str) -> str:
        return self.path(name).read_text(encoding="utf-8")

    def save_model(self, name: str, model) -> Path:
        return self.write_text(name, model.model_dump_json(indent=2) + "\n")

    def load_model(self, name: str, kind):
        return kind.model_validate_json(self.read_text(name))

    # ---------------------------------------------------------------- state
    def state(self) -> FlowState:
        if self.exists("state.json"):
            return self.load_model("state.json", FlowState)
        return FlowState(project=self.folder.name)

    def save_state(self, state: FlowState) -> None:
        self.save_model("state.json", state)

    def request(self) -> str:
        return self.read_text("request.md")

    # ---------------------------------------------------------------- clarify
    def save_clarification(self, clarification: Clarification) -> None:
        self.save_model("clarify.json", clarification)
        lines = ["# Câu hỏi làm rõ yêu cầu", "",
                 "Sửa phần sau `A:` của từng câu (đã điền sẵn câu trả lời mặc định), rồi chạy lại bước tiếp theo.",
                 "Giữ nguyên các dòng `## Q...`. Câu nào để nguyên mặc định thì được ghi là giả định.", "",
                 f"Hiểu yêu cầu: {clarification.understanding}", ""]
        for question in clarification.questions:
            flag = " (quan trọng)" if question.blocking else ""
            lines += [f"## {question.id}{flag}: {question.question}", f"Vì sao hỏi: {question.why}",
                      f"A: {question.default_answer}", ""]
        self.write_text("answers.md", "\n".join(lines))

    def answers(self) -> list[Answer]:
        """Answers from answers.md; an unchanged default counts as an assumption, not a user decision."""
        if not self.exists("clarify.json"):
            return []
        clarification = self.load_model("clarify.json", Clarification)
        defaults = {q.id: q.default_answer.strip() for q in clarification.questions}
        text = self.read_text("answers.md") if self.exists("answers.md") else ""
        given = {}
        for block in re.split(r"(?m)^## ", text)[1:]:
            match = re.match(r"(Q\d+)", block)
            answer = re.search(r"(?ms)^A:\s*(.*?)\s*\Z", block)
            if match and answer:
                given[match.group(1)] = answer.group(1).strip()
        result = []
        for question in clarification.questions:
            value = given.get(question.id, "").strip() or defaults[question.id]
            source = "default" if value == defaults[question.id] else "user"
            result.append(Answer(question_id=question.id, answer=value, source=source))
        return result

    # ---------------------------------------------------------------- spec / architecture / tasks
    def save_spec(self, spec: Spec) -> None:
        self.save_model("spec.json", spec)
        lines = ["# Đặc tả yêu cầu", "", spec.summary, ""]
        if spec.scope_in:
            lines += ["**Trong phạm vi:** " + "; ".join(spec.scope_in), ""]
        if spec.scope_out:
            lines += ["**Ngoài phạm vi:** " + "; ".join(spec.scope_out), ""]
        for title, kind in (("Yêu cầu chức năng", "functional"), ("Yêu cầu phi chức năng", "non_functional")):
            items = [req for req in spec.requirements if req.kind == kind]
            if not items:
                continue
            lines += [f"## {title}", ""]
            for req in items:
                lines.append(f"### {req.id} ({req.priority}): {req.title}")
                lines.append(req.statement)
                if req.metric or req.target:
                    lines.append(f"Đo: {req.metric or '-'}; mục tiêu: {req.target or '-'}")
                for number, criterion in enumerate(req.acceptance_criteria, 1):
                    lines.append(f"- `{req.id}.AC{number}` {criterion}")
                lines.append("")
        if spec.assumptions:
            lines += ["## Giả định", ""] + [f"- {item}" for item in spec.assumptions] + [""]
        self.write_text("spec.md", "\n".join(lines))

    def save_architecture(self, architecture: Architecture) -> None:
        self.save_model("architecture.json", architecture)
        lines = ["# Kiến trúc và công nghệ", "", f"Bối cảnh: {architecture.context}", "", architecture.overview, ""]
        if architecture.components:
            lines += ["## Thành phần", ""] + [f"- {item}" for item in architecture.components] + [""]
        lines += ["## Quyết định", ""]
        for decision in architecture.decisions:
            lines += [f"### {decision.id}: {decision.topic} → {decision.choice}", decision.rationale]
            if decision.requirement_ids:
                lines.append("Theo yêu cầu: " + ", ".join(decision.requirement_ids))
            for option in decision.alternatives:
                lines.append(f"- Đã cân nhắc **{option.name}**: + {'; '.join(option.pros) or '-'} / "
                             f"− {'; '.join(option.cons) or '-'}")
            lines.append("")
        self.write_text("architecture.md", "\n".join(lines))

    def save_environment(self, plan) -> None:
        self.save_model("environment.json", plan)
        lines = ["# Môi trường phát triển", "", "| Runtime | Phiên bản | Nguồn | Đường dẫn / lệnh cài | Lý do |",
                 "|---|---|---|---|---|"]
        for runtime in plan.runtimes:
            where = runtime.path if runtime.source == "installed" else f"`{runtime.install_command}`"
            source = "đã có trên máy" if runtime.source == "installed" else "cần cài"
            lines.append(f"| {runtime.name} | {runtime.version} | {source} | {where or '-'} | {runtime.reason} |")
        lines += ["", f"- Quản lý gói: {plan.package_manager}", f"- Cài thư viện: `{plan.dependency_install}`",
                  f"- Chạy test: `{plan.test_command}`"]
        if plan.run_command:
            lines.append(f"- Chạy ứng dụng: `{plan.run_command}`")
        lines.append("- Kiểm tra nhanh: " + ", ".join(f"`{c}`" for c in plan.check_commands))
        if plan.notes:
            lines += ["", "## Ghi chú", ""] + [f"- {note}" for note in plan.notes]
        self.write_text("environment.md", "\n".join(lines) + "\n")

    def save_tasks(self, graph: TaskGraph, spec: Spec | None = None) -> list[str]:
        self.save_model("tasks.json", graph)
        issues = problems(graph, spec)
        lines = ["# Kế hoạch task", ""]
        if issues:
            lines += ["**Kế hoạch chưa hợp lệ:**", ""] + [f"- {item}" for item in issues] + [""]
            order = [task.id for task in graph.tasks]
        else:
            order = topological_order(graph)
            lines += ["Thứ tự chạy: " + " → ".join(order), ""]
        by_id = {task.id: task for task in graph.tasks}
        lines += ["| Task | Ưu tiên | Cỡ | Phụ thuộc | AC |", "|---|---|---|---|---|"]
        for task_id in order:
            task = by_id[task_id]
            lines.append(f"| {task.id}: {task.title} | {task.priority} | {task.size} | "
                         f"{', '.join(task.depends_on) or '-'} | {', '.join(task.acceptance_criteria)} |")
        lines.append("")
        for task_id in order:
            task = by_id[task_id]
            lines += [f"## {task.id}: {task.title}", task.description]
            if task.preconditions:
                lines.append("Điều kiện trước: " + "; ".join(task.preconditions))
            if task.files:
                lines.append("File dự kiến: " + ", ".join(task.files))
            lines.append("")
        if graph.integration_notes:
            lines += ["## Ghi chú tích hợp", graph.integration_notes, ""]
        self.write_text("tasks.md", "\n".join(lines))
        return issues


def dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)
