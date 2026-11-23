"""Kết quả của từng flow, lưu trong repo đích tại <workspace>/.raac/pre_executor/.

    request.md              yêu cầu gốc (từ --prompt hoặc --prompt-file)
    clarify.json            các vòng làm rõ: câu hỏi của role Clarifier theo vòng, run_id
    answers.md              câu hỏi + câu trả lời; người dùng sửa phần sau "A:"
    clarification.json      Clarification đã duyệt (ghi lúc approve clarify) — đầu vào của flow requirements
    feedback/<flow>.json    góp ý của người dùng cho flow đó, theo thứ tự
    run.json                run_id chung của workspace: mọi flow ghi log vào cùng results/<run_id>/llm.jsonl
    usage.json              token đã dùng: từng lần chạy flow, cộng dồn theo flow và theo run
    approvals.json          {flow: {sha256, approved_at}}
    requirements.json/.md           flow requirements
    requirement_profile.json/.md    flow profile (hồ sơ đặc tả BA: F/FR/AC/UF/BR/EC/NFR), đầu vào của SA và PM
    solution_architecture.json/.md  flow solution_architect (tech stack, module, API, ràng buộc toàn cục, runtime)
    tasks.json/.md                  flow project_manager (DAG T1..Tn + execution_order + context_slice cho MAS)

Thư mục có sẵn .gitignore riêng ("*") nên không lẫn vào git của repo đích.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path

FOLDER = Path(".raac") / "pre_executor"
FLOWS = ("clarify", "requirements", "profile", "solution_architect", "project_manager")
# File mà người dùng duyệt cho từng flow (hash của file này được ghi lúc approve).
APPROVED_FILE = {
    "clarify": "answers.md",
    "requirements": "requirements.json",
    "profile": "requirement_profile.json",
    "solution_architect": "solution_architecture.json",
    "project_manager": "tasks.json",
}
# Đầu ra đã duyệt của tầng BA mà SA/PM đọc: ưu tiên ba_spec.json nếu phần Clarifier + BA sau này gộp/đổi tên,
# mặc định requirement_profile.json. Đổi tên chỉ cần sửa ở đây (và FLOWS/APPROVED_FILE).
BA_SPEC_CANDIDATES = ("ba_spec.json", "requirement_profile.json")


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

    def read_ba_spec(self) -> dict | None:
        """Đặc tả BA đã duyệt (ba_spec.json nếu có, mặc định requirement_profile.json)."""
        for filename in BA_SPEC_CANDIDATES:
            data = self.read_json(filename)
            if data is not None:
                return data
        return None

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

    # ---- run chung của workspace
    def run_id(self) -> str | None:
        return (self.read_json("run.json") or {}).get("run_id")

    def set_run_id(self, run_id: str, reason: str = "") -> None:
        data = self.read_json("run.json") or {"run_id": None, "history": []}
        if data.get("run_id") == run_id:
            return
        if data.get("run_id"):
            data["history"].append({"run_id": data["run_id"], "replaced_because": reason or "unknown",
                                    "at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
        data["run_id"] = run_id
        self.write_json("run.json", data)

    def usage_for_run(self, run_id: str | None) -> dict:
        """Token đã ghi cho run này (mọi lần chạy flow trước), để tách token của lần chạy hiện tại."""
        total = {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0}
        for run in (self.read_json("usage.json") or {}).get("runs", []):
            if run["run_id"] == run_id:
                for key in total:
                    total[key] += run[key]
        return total

    # ---- token đã dùng (số do provider báo, như MAS)
    def add_usage(self, flow: str, run_id: str, usage: dict) -> None:
        data = self.read_json("usage.json") or {"runs": [], "flows": {}}
        entry = {key: usage.get(key) or 0 for key in ("prompt_tokens", "completion_tokens", "calls")}
        data["runs"].append({"run_id": run_id, "flow": flow, **entry,
                             "at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
        total = data["flows"].setdefault(flow, {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0, "runs": 0})
        for key, value in entry.items():
            total[key] += value
        total["runs"] += 1
        self.write_json("usage.json", data)

    def usage_by_flow(self) -> dict:
        return (self.read_json("usage.json") or {}).get("flows", {})

    def usage_total(self) -> dict:
        total = {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0}
        for values in self.usage_by_flow().values():
            for key in total:
                total[key] += values[key]
        return total
