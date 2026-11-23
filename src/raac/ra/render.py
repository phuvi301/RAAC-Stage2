"""requirement_profile.json -> requirement_profile.md (bảng điểm 4 trục + bằng chứng từng task)."""

LABELS = {"complexity": "Complexity (C)", "testability": "Testability (T)", "security": "Security (S)",
          "architecture_impact": "Architecture Impact (A)"}


def _cell(text) -> str:
    return str(text).replace("|", r"\|").replace("\n", " ")


def render_profiles(profiles: dict) -> str:
    lines = ["# Requirement Profile", "", "| Task | C | T | S | A |", "|---|---|---|---|---|"]
    for task_id, entry in sorted(profiles.items()):
        c, t, s, a = entry["profile"]
        lines.append(f"| {task_id} | {c} | {t} | {s} | {a} |")
    for task_id, entry in sorted(profiles.items()):
        lines += ["", f"## {task_id}", ""]
        if entry.get("summary"):
            lines += [entry["summary"], ""]
        lines += ["| Trục | Điểm | Yếu tố | Bằng chứng |", "|---|---|---|---|"]
        for axis, label in LABELS.items():
            score = entry[axis]
            lines.append(f"| {label} | {score['score']} | {', '.join(score['factors'])} | {_cell(score['evidence'])} |")
        if entry.get("architecture_sections_read"):
            lines += ["", f"Đã đọc trong architecture.md: {', '.join(entry['architecture_sections_read'])}"]
        if entry.get("checks"):
            lines += ["", "Cần xem lại:", *[f"- {note}" for note in entry["checks"]]]
        if entry.get("human"):
            lines += ["", f"Điểm người sửa: {entry['human']}"]
        lines += ["", f"_rubric {entry.get('rubric_version')}, run {entry.get('run_id')}, {entry.get('analyzed_at')}_"]
    return "\n".join(lines) + "\n"
