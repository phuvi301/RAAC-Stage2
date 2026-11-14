"""Kết quả của từng flow, lưu trong repo đích tại <workspace>/.raac/pre_executor/.

    request.md              yêu cầu gốc (từ --prompt hoặc --prompt-file)
    clarify.json            các vòng làm rõ: câu hỏi của role Clarifier theo vòng, run_id
    answers.md              câu hỏi + câu trả lời; người dùng sửa phần sau "A:"
    clarification.json      Clarification đã duyệt (ghi lúc approve clarify) — đầu vào của flow requirements
    feedback/<flow>.json    góp ý của người dùng cho flow đó, theo thứ tự
    approvals.json          {flow: {sha256, approved_at}}
    (requirements.json, requirement_profile.json, tech_stack.json, ... do các flow sau ghi)

Thư mục có sẵn .gitignore riêng ("*") nên không lẫn vào git của repo đích.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path

FOLDER = Path(".raac") / "pre_executor"
FLOWS = ("clarify", "requirements", "profile", "tech_select", "architecture", "environment", "task_decomposer")
# File mà người dùng duyệt cho từng flow (hash của file này được ghi lúc approve).
APPROVED_FILE = {
    "clarify": "answers.md",
    "requirements": "requirements.json",
    "profile": "requirement_profile.json",
    "tech_select": "tech_stack.json",
    "architecture": "architecture.json",
    "environment": "environment.json",
    "task_decomposer": "tasks.json",
}


class Artifacts:
    def __init__(self, workspace: Path):
        self.workspace = Path(workspace).resolve()
        self.folder = self.workspace / FOLDER

    def path(self, name: str) -> Path:
        return self.folder / name

    def ensure(self) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        ignore = self.folder.parent / ".gitignore"
        if not ignore.exists():
            ignore.write_text("*\n", encoding="utf-8")

    # ---- đọc/ghi chung
    def write_text(self, name: str, text: str) -> Path:
        self.ensure()
        target = self.path(name)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + ".tmp")
        temporary.write_text(text, encoding="utf-8")
        os.replace(temporary, target)  # ghi nguyên tử
        return target

    def read_text(self, name: str) -> str | None:
        target = self.path(name)
        return target.read_text(encoding="utf-8") if target.is_file() else None

    def write_json(self, name: str, data) -> Path:
        return self.write_text(name, json.dumps(data, ensure_ascii=False, indent=2) + "\n")

    def read_json(self, name: str):
        text = self.read_text(name)
        return json.loads(text) if text is not None else None

    # ---- yêu cầu gốc
    def save_request(self, text: str) -> None:
        self.write_text("request.md", text.strip() + "\n")

    def request(self) -> str | None:
        text = self.read_text("request.md")
        return text.strip() if text is not None else None

    # ---- góp ý của người dùng
    def add_feedback(self, flow: str, text: str) -> None:
        history = self.read_json(f"feedback/{flow}.json") or []
        history.append({"text": text.strip(), "at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
        self.write_json(f"feedback/{flow}.json", history)

    def feedback_history(self, flow: str) -> list[str]:
        return [entry["text"] for entry in self.read_json(f"feedback/{flow}.json") or []]
