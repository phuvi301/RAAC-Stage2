"""Chấm Requirement Profile (C, T, S, A) cho một task người dùng chọn chạy vào MAS.

Vào:  tasks.json (task + tên các task phụ thuộc), phần liên quan của requirement_spec.json (gom bằng code), mục lục
      architecture.md (agent tự đọc đúng mục bằng read_file/read_files của harness), rubric 4 trục.
Ra:   một mục trong <workspace>/.raac/ra/requirement_profile.json (+ requirement_profile.md).
Luật cứng (sai thì gửi lại agent kèm lỗi): task_id đúng; factors của mỗi trục chỉ lấy từ bảng yếu tố của trục đó;
      có architecture.md thì phải ghi các mục đã đọc và các mục đó có thật trong mục lục.
Tín hiệu mềm (không chặn, ghi vào `checks` để người xem lại): weak phrase / thiếu metric mà T thấp; từ khóa bảo mật
      mà S = 1; dấu hiệu đổi API/schema/hạ tầng mà A = 1.
"""

import hashlib
import json
import re
from datetime import datetime, timezone

from raac.pre_executor.flows.base import run_role

from .context import architecture_outline, rubric_text, select_task, spec_context
from .contracts import AXES, AXIS_FACTORS, RUBRIC_VERSION, RequirementProfileOutput
from .render import render_profiles

FLOW = "analyze"
ROLE = "RequirementAnalyzer"
ARCHITECTURE = "architecture.md"

# Wilson et al. (1997) và rubric mục 4.2: cụm từ mơ hồ không đo được.
WEAK_PHRASES = re.compile(
    r"\b(adequate|appropriate|user[- ]friendly|effective|efficient|easy|fast|flexible|robust|as needed|"
    r"if possible|thân thiện|phù hợp|hiệu quả|dễ dùng|nhanh chóng|linh hoạt|ổn định|khi cần)\b", re.IGNORECASE)
SECURITY_WORDS = re.compile(
    r"\b(auth\w*|login|đăng nhập|mật khẩu|password|token|session|phiên|quyền|role|permission|otp|payment|"
    r"thanh toán|pii|cá nhân|encrypt\w*|mã hóa|admin|quản trị)\b", re.IGNORECASE)
ARCHITECTURE_WORDS = re.compile(
    r"\b(api|endpoint|schema|migration|queue|cache|service|webhook|integration|tích hợp|deploy\w*|"
    r"bảng dữ liệu|cơ sở dữ liệu|database)\b", re.IGNORECASE)


def _sha(data) -> str:
    return hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:16]


def check_against(task_id: str, outline: list[dict]):
    titles = {item["title"] for item in outline}

    def check(output: RequirementProfileOutput) -> list[str]:
        problems = [] if output.task_id == task_id else [f"task_id must be {task_id}, got {output.task_id}"]
        for axis in AXES:
            wrong = [f for f in getattr(output, axis).factors if f not in AXIS_FACTORS[axis]]
            if wrong:
                problems.append(f"{axis}.factors {wrong} are not {axis} factors; use only {list(AXIS_FACTORS[axis])}")
        if outline:
            if not output.architecture_sections_read:
                problems.append("read the relevant sections of architecture.md with read_file/read_files and list "
                                "their headings in architecture_sections_read")
            unknown = [s for s in output.architecture_sections_read if s not in titles]
            if unknown:
                problems.append(f"architecture_sections_read {unknown} are not headings of architecture.md; "
                                "copy headings exactly from architecture_outline")
        return problems
    return check


def soft_checks(output: RequirementProfileOutput, task, context: dict) -> list[str]:
    """Tín hiệu bằng code để người xem lại; không ép điểm vì rubric là phán đoán."""
    text = " ".join([task.title, task.description,
                     *(f"{ac['given']} {ac['when']} {ac['then']}" for fr in context["functional_requirements"]
                       for ac in fr.get("acceptance_criteria", []))])
    notes = []
    weak = sorted({m.group(0).lower() for m in WEAK_PHRASES.finditer(text)})
    if weak and output.testability.score <= 2:
        notes.append(f"testability {output.testability.score} nhưng có cụm mơ hồ: {', '.join(weak)}")
    if not task.acceptance_criteria and output.testability.score <= 2:
        notes.append(f"testability {output.testability.score} nhưng task không có acceptance criteria")
    security = sorted({m.group(0).lower() for m in SECURITY_WORDS.finditer(text)})
    if security and output.security.score == 1:
        notes.append(f"security 1 nhưng có từ khóa bảo mật: {', '.join(security)}")
    architecture = sorted({m.group(0).lower() for m in ARCHITECTURE_WORDS.finditer(" ".join([text, *task.files]))})
    if architecture and output.architecture_impact.score == 1:
        notes.append(f"architecture_impact 1 nhưng có dấu hiệu đổi kiến trúc: {', '.join(architecture)}")
    return notes


async def run(sources, store, runtime, config, task_id: str, *, rubric_path) -> dict:
    """sources: Artifacts của pre_executor (tasks.json, requirement_spec.json, architecture.md);
    store: Artifacts của ra (requirement_profile.json)."""
    tasks = sources.read_json("tasks.json")
    spec = sources.read_json("requirement_spec.json")
    if tasks is None:
        raise ValueError(f"Chưa có tasks.json trong {sources.folder}")
    if spec is None:
        raise ValueError(f"Chưa có requirement_spec.json trong {sources.folder}")
    task, dependencies = select_task(tasks, task_id)
    context = spec_context(spec, task)
    architecture = sources.read_text(ARCHITECTURE) or ""
    outline = architecture_outline(architecture)
    architecture_path = sources.path(ARCHITECTURE).relative_to(sources.workspace).as_posix() if architecture else None

    payload = {
        "task": task.model_dump(), "dependencies": dependencies, "spec_context": context,
        "architecture": ({"path": architecture_path, "total_lines": len(architecture.splitlines()),
                          "outline": outline} if architecture else None),
        "axis_factors": {axis: list(factors) for axis, factors in AXIS_FACTORS.items()},
        "rubric_version": RUBRIC_VERSION, "rubric": rubric_text(rubric_path),
    }
    output = await run_role(runtime, FLOW, ROLE, RequirementProfileOutput, payload, check_against(task_id, outline),
                            retries=config.max_flow_retries)
    entry = {
        **output.model_dump(),
        "profile": [getattr(output, axis).score for axis in AXES],
        "checks": soft_checks(output, task, context),
        "rubric_version": RUBRIC_VERSION, "run_id": runtime.run_id,
        "inputs": {"task": _sha(task.model_dump()), "spec_context": _sha(context), "architecture": _sha(architecture)},
        "analyzed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "human": None,  # điểm người sửa (nếu có) để đối chiếu với analyzer
    }
    data = store.read_json("requirement_profile.json") or {"profiles": {}}
    data["profiles"][task_id] = entry
    store.write_json("requirement_profile.json", data)
    path = store.write_text("requirement_profile.md", render_profiles(data["profiles"]))
    profile = entry["profile"]
    return {"status": "done", "run_id": runtime.run_id, "path": str(store.path("requirement_profile.json")),
            "profile": profile, "checks": entry["checks"],
            "message": (f"{task_id}: (C={profile[0]}, T={profile[1]}, S={profile[2]}, A={profile[3]})"
                        + (f" · đã đọc kiến trúc: {', '.join(output.architecture_sections_read)}"
                           if output.architecture_sections_read else "")
                        + (f"\n  Cần xem lại: " + "; ".join(entry["checks"]) if entry["checks"] else "")
                        + f"\n  Kết quả: {store.path('requirement_profile.json')} (đọc: {path})")}


def stale(sources, entry: dict, task_id: str) -> bool:
    """Đầu vào (task, phần spec liên quan, architecture.md) đã đổi kể từ lần chấm."""
    try:
        task, _ = select_task(sources.read_json("tasks.json") or {}, task_id)
        context = spec_context(sources.read_json("requirement_spec.json") or {}, task)
    except ValueError:
        return True
    current = {"task": _sha(task.model_dump()), "spec_context": _sha(context),
               "architecture": _sha(sources.read_text(ARCHITECTURE) or "")}
    return current != entry.get("inputs")
