"""Turn one planned task into the request a MAS run receives (requirement/user_chat.txt)."""

from __future__ import annotations

from .models import Architecture, EnvironmentPlan, Spec, TaskGraph, TaskNode


def criteria_text(spec: Spec) -> dict[str, str]:
    """AC ID (FR-1.AC2) -> its full text."""
    return {f"{req.id}.AC{n}": text for req in spec.requirements for n, text in enumerate(req.acceptance_criteria, 1)}


def task_request(*, request: str, spec: Spec, architecture: Architecture, graph: TaskGraph, task: TaskNode,
                 environment: EnvironmentPlan | None = None) -> str:
    by_id = {item.id: item for item in graph.tasks}
    criteria = criteria_text(spec)
    requirements = {req.id: req for req in spec.requirements}
    lines = [
        f"# {task.id}: {task.title}", "",
        "Đây là MỘT task trong kế hoạch của một dự án lớn hơn. Chỉ làm đúng phạm vi task này; "
        "các task khác sẽ được làm ở các lượt sau.", "",
        "## Sản phẩm", spec.summary, "",
        "Yêu cầu gốc của người dùng:", request.strip(), "",
        "## Công nghệ đã chốt (bắt buộc dùng)",
    ]
    lines += [f"- {d.topic}: {d.choice}" for d in architecture.decisions]
    if environment is not None:
        lines += environment_section(environment)
    lines += ["", "## Việc cần làm trong task này", task.description]
    if task.files:
        lines += ["", "File/module dự kiến: " + ", ".join(task.files)]
    if task.preconditions:
        lines += ["", "Điều kiện đã có trước task này: " + "; ".join(task.preconditions)]
    done = [by_id[dep] for dep in task.depends_on if dep in by_id]
    if done:
        lines += ["", "## Đã làm xong ở các task trước (code đã có trong repo)"]
        lines += [f"- {dep.id}: {dep.title}: {dep.description}" for dep in done]
    lines += ["", "## Tiêu chí nghiệm thu của task này (phải đạt hết)"]
    lines += [f"- `{ac}` {criteria.get(ac, '(không tìm thấy trong spec)')}" for ac in task.acceptance_criteria]
    # Requirements named on the task plus those its criteria belong to (FR-1.AC2 -> FR-1).
    related_ids = dict.fromkeys([*task.requirement_ids, *(ac.split(".")[0] for ac in task.acceptance_criteria)])
    related = [requirements[r] for r in related_ids if r in requirements]
    nonfunctional = [r for r in related if r.kind == "non_functional"]
    if nonfunctional:
        lines += ["", "## Yêu cầu phi chức năng liên quan"]
        lines += [f"- {r.id}: {r.statement} (đo: {r.metric}; mục tiêu: {r.target})" for r in nonfunctional]
    later = [item for item in graph.tasks if task.id in item.depends_on]
    if later:
        lines += ["", "## Không làm trong task này (để cho task sau)"]
        lines += [f"- {item.id}: {item.title}" for item in later]
    return "\n".join(lines) + "\n"


def environment_section(environment: EnvironmentPlan) -> list[str]:
    """What the MAS Environment role should use as is, instead of probing or scaffolding on its own."""
    lines = ["", "## Môi trường đã chốt (Environment dùng đúng như vậy)",
             "Môi trường này đã được chọn và kiểm tra trên máy trước khi chạy. Environment chỉ cần xác nhận các "
             "runtime dưới đây chạy được (bằng các lệnh kiểm tra), không dựng thử project ngoài repo, không tải thêm "
             "công cụ. Repo còn rỗng ở task đầu là bình thường: dựng khung project là việc của Coder."]
    for runtime in environment.runtimes:
        where = f" tại `{runtime.path}`" if runtime.path else ""
        lines.append(f"- {runtime.name} {runtime.version}{where}")
    lines += [f"- Quản lý gói: {environment.package_manager}; cài thư viện: `{environment.dependency_install}`",
              f"- Lệnh test (chạy ở thư mục gốc project): `{environment.test_command}`"]
    if environment.run_command:
        lines.append(f"- Chạy ứng dụng: `{environment.run_command}`")
    lines.append("- Lệnh kiểm tra: " + ", ".join(f"`{command}`" for command in environment.check_commands))
    if environment.notes:
        lines.append("- Ghi chú (rủi ro, không phải lỗi chặn): " + "; ".join(environment.notes))
    return lines
