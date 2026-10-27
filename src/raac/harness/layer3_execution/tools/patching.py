"""Strict textual unified diffs; no shell commands and no path escapes."""

import re

from .paths import repo_path


def apply_solution_patch(baseline: str, patch: str, *, path="solution.py") -> str:
    """Compatibility single-file entrypoint; callers may select any safe path."""
    path = repo_path(path)
    if len(patch.encode("utf-8")) > 4194304:
        raise ValueError("Patch exceeds size limit")
    lines = patch.splitlines(keepends=True)
    if lines and lines[0].startswith("diff --git "):
        if lines.pop(0).strip() != f"diff --git a/{path} b/{path}":
            raise ValueError("Patch path does not match target")
        if lines and lines[0].startswith("index "):
            lines.pop(0)
    if len(lines) < 3 or [line.rstrip("\r\n") for line in lines[:2]] != [
        f"--- a/{path}", f"+++ b/{path}",
    ]:
        raise ValueError("Expected a unified diff for the selected target path")
    body = []
    for line in lines[2:]:
        if line.rstrip("\r\n") == "\\ No newline at end of file":
            if not body or body[-1][0] not in " +-":
                raise ValueError("Invalid no-newline marker")
            body[-1] = body[-1].rstrip("\r\n")
        else:
            body.append(line)
    original = baseline.splitlines(keepends=True)
    output, cursor, index = [], 0, 0
    while index < len(body):
        match = re.fullmatch(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@[^\r\n]*[\r\n]*", body[index])
        if not match:
            raise ValueError("Invalid unified diff hunk header")
        old_start, old_count, new_start, new_count = match.groups()
        old_count, new_count = int(old_count or 1), int(new_count or 1)
        start = int(old_start) if old_count == 0 else int(old_start) - 1
        target = int(new_start) if new_count == 0 else int(new_start) - 1
        if start < cursor or start > len(original):
            raise ValueError("Overlapping or out-of-range hunk")
        output.extend(original[cursor:start])
        cursor = start
        if target != len(output):
            raise ValueError("Inconsistent new-file hunk position")
        index += 1
        consumed = produced = 0
        while index < len(body) and not body[index].startswith("@@ "):
            line = body[index]
            if not line or line[0] not in " +-":
                raise ValueError("Unsupported patch content")
            kind, text = line[0], line[1:]
            if kind in " -":
                if cursor >= len(original) or original[cursor] != text:
                    raise ValueError("Patch does not match original baseline")
                cursor += 1
                consumed += 1
            if kind in " +":
                output.append(text)
                produced += 1
            index += 1
        if (consumed, produced) != (old_count, new_count):
            raise ValueError("Hunk line counts do not match content")
    output.extend(original[cursor:])
    return "".join(output)


def _reverse_hunks(lines):
    result = []
    for line in lines:
        if line.startswith("@@ "):
            match = re.fullmatch(r"@@ -(\d+(?:,\d+)?) \+(\d+(?:,\d+)?) @@([^\r\n]*)([\r\n]*)", line)
            if not match:
                raise ValueError("Invalid reverse hunk")
            old, new, suffix, ending = match.groups()
            line = f"@@ -{new} +{old} @@{suffix}{ending}"
        elif line.startswith("+"):
            line = "-" + line[1:]
        elif line.startswith("-"):
            line = "+" + line[1:]
        result.append(line)
    return result


def apply_repo_patch(baseline: dict[str, str], patch: str, *, reverse=False) -> dict[str, str | None]:
    """Validate every file before returning changes for an atomic preflight."""
    if len(patch.encode("utf-8")) > 4194304:
        raise ValueError("Patch exceeds size limit")
    lines = patch.splitlines(keepends=True)
    changes = {}
    index = 0
    while index < len(lines):
        if lines[index].startswith("diff --git "):
            index += 1
            if index < len(lines) and lines[index].startswith("index "):
                index += 1
        if index + 1 >= len(lines) or not lines[index].startswith("--- ") or not lines[index + 1].startswith("+++ "):
            raise ValueError("Expected file headers")
        old = lines[index][4:].rstrip("\r\n").split("\t")[0]
        new = lines[index + 1][4:].rstrip("\r\n").split("\t")[0]
        old = None if old == "/dev/null" else repo_path(old[2:]) if old.startswith("a/") else repo_path(old)
        new = None if new == "/dev/null" else repo_path(new[2:]) if new.startswith("b/") else repo_path(new)
        if reverse:
            old, new = new, old
        target = new or old
        if not target or (old and new and old != new) or target in changes:
            raise ValueError("Invalid, renamed or duplicate patch target")
        if old is not None and old not in baseline:
            raise ValueError("Patch source is not in baseline")
        if old is None and target in baseline:
            raise ValueError("New file already exists in baseline")
        index += 2
        body_start = index
        while index < len(lines) and lines[index].startswith("@@ "):
            match = re.fullmatch(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@[^\r\n]*[\r\n]*", lines[index])
            if not match:
                raise ValueError("Invalid hunk")
            _, old_count, _, new_count = match.groups()
            remaining_old, remaining_new = int(old_count or 1), int(new_count or 1)
            index += 1
            while remaining_old or remaining_new:
                if index >= len(lines) or lines[index][:1] not in {" ", "+", "-"}:
                    raise ValueError("Truncated hunk")
                marker = lines[index][0]
                remaining_old -= marker in " -"
                remaining_new -= marker in " +"
                if min(remaining_old, remaining_new) < 0:
                    raise ValueError("Invalid hunk counts")
                index += 1
                if index < len(lines) and lines[index].rstrip("\r\n") == "\\ No newline at end of file":
                    index += 1
        if index == body_start:
            raise ValueError("Missing hunk")
        body = _reverse_hunks(lines[body_start:index]) if reverse else lines[body_start:index]
        normalized = f"--- a/{target}\n+++ b/{target}\n" + "".join(body)
        content = apply_solution_patch(baseline.get(target, ""), normalized, path=target)
        if new is None and content:
            raise ValueError("Deletion did not remove all content")
        changes[target] = None if new is None else content
    if not changes:
        raise ValueError("Empty patch")
    return changes


def rebase_repo_patch(patch: str, prefix: str) -> str:
    """Prefix file headers at parsed file boundaries; never rewrite hunk text."""
    prefix = repo_path(prefix)
    lines = patch.splitlines(keepends=True)
    index, output = 0, []
    while index < len(lines):
        if lines[index].startswith("diff --git "):
            index += 1
            if index < len(lines) and lines[index].startswith("index "):
                index += 1
        if index + 1 >= len(lines) or not lines[index].startswith("--- ") or not lines[index + 1].startswith("+++ "):
            raise ValueError("Expected patch file headers")
        for marker, side in (("--- ", "a/"), ("+++ ", "b/")):
            path = lines[index][4:].rstrip("\r\n").split("\t")[0]
            if path != "/dev/null":
                path = repo_path(path[2:] if path.startswith(side) else path)
                path = side + repo_path(prefix + "/" + path)
            output.append(marker + path + "\n")
            index += 1
        body_start = index
        while index < len(lines) and lines[index].startswith("@@ "):
            match = re.fullmatch(r"@@ -\d+(?:,(\d+))? \+\d+(?:,(\d+))? @@[^\r\n]*[\r\n]*", lines[index])
            if not match:
                raise ValueError("Invalid hunk")
            old, new = (int(value or 1) for value in match.groups())
            index += 1
            while old or new:
                if index >= len(lines) or lines[index][:1] not in {" ", "+", "-"}:
                    raise ValueError("Truncated hunk")
                marker = lines[index][0]
                old -= marker in " -"
                new -= marker in " +"
                if min(old, new) < 0:
                    raise ValueError("Invalid hunk counts")
                index += 1
                if index < len(lines) and lines[index].rstrip("\r\n") == "\\ No newline at end of file":
                    index += 1
        if index == body_start:
            raise ValueError("Missing hunk")
        output.extend(lines[body_start:index])
    return "".join(output)
