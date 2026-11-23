"""Helper kiểm bằng code dùng chung cho solution_architect và project_manager.

- duplicates(label, values): mỗi giá trị trùng một thông điệp, cùng dạng với luật của profile.
- validate_relative_path(path): đường dẫn tương đối kiểu POSIX trong repo đích, hoặc thông điệp lỗi.
- find_cycle(graph): một chu trình (vd. ["T2", "T3", "T2"]) hoặc None; duyệt theo thứ tự id nên tái lập.
- workspace_mode(workspace): "brownfield" nếu repo đích đã có file ngoài .raac/ và .git/, ngược lại "greenfield".
"""

import re
from pathlib import Path

WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:")
IGNORED_IN_WORKSPACE = {".raac", ".git"}


def id_key(value: str) -> tuple[int, str]:
    """MOD-12 / T3 / CON-2 -> (12, ...): sắp theo số thứ tự, không theo chuỗi (T10 sau T9)."""
    digits = re.search(r"(\d+)$", value)
    return (int(digits.group(1)) if digits else 0, value)


def duplicates(label: str, values: list[str]) -> list[str]:
    return [f"duplicate {label} {value}" for value in sorted({value for value in values if values.count(value) > 1})]


def validate_relative_path(path: str) -> str | None:
    if not path or not path.strip():
        return "path must not be empty"
    if "\\" in path:
        return f"path {path!r} must use '/' separators"
    if path.startswith(("/", "~")) or WINDOWS_DRIVE.match(path):
        return f"path {path!r} must be relative to the repository root"
    if ".." in path.split("/"):
        return f"path {path!r} must not contain '..'"
    return None


def find_cycle(graph: dict[str, list[str]]) -> list[str] | None:
    """graph: node -> các node nó phụ thuộc. Bỏ qua cạnh trỏ node không có trong graph (luật khác báo)."""
    state: dict[str, int] = {}  # 1 = đang duyệt, 2 = xong
    stack: list[str] = []

    def visit(node: str) -> list[str] | None:
        state[node] = 1
        stack.append(node)
        for nxt in sorted(graph.get(node, []), key=id_key):
            if nxt not in graph:
                continue
            if state.get(nxt) == 1:
                return [*stack[stack.index(nxt):], nxt]
            if state.get(nxt) is None:
                found = visit(nxt)
                if found:
                    return found
        stack.pop()
        state[node] = 2
        return None

    for node in sorted(graph, key=id_key):
        if state.get(node) is None:
            found = visit(node)
            if found:
                return found
    return None


def workspace_mode(workspace: Path) -> str:
    workspace = Path(workspace)
    if workspace.is_dir() and any(entry.name not in IGNORED_IN_WORKSPACE for entry in workspace.iterdir()):
        return "brownfield"
    return "greenfield"
