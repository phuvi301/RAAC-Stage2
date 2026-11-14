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


_ENVELOPE_FILES = ("*** Add File: ", "*** Delete File: ", "*** Update File: ")


def _find_block(lines, block, start):
    """Locate ``block`` at or after ``start``: exact first, then ignoring trailing whitespace."""
    for normalize in (lambda text: text, str.rstrip):
        wanted = [normalize(text) for text in block]
        for position in range(start, len(lines) - len(block) + 1):
            if [normalize(text) for text in lines[position:position + len(block)]] == wanted:
                return position
    return None


def apply_envelope_patch(baseline: dict[str, str], patch: str) -> dict[str, str | None]:
    """The ``*** Begin Patch`` format some models emit by default; same result shape as unified diffs."""
    lines = patch.splitlines()
    if not lines or lines[0].strip() != "*** Begin Patch":
        raise ValueError("Expected file headers")
    if not any(line.startswith("*** End Patch") for line in lines[1:]):
        raise ValueError("Missing patch terminator")
    changes, index = {}, 1
    while index < len(lines) and not lines[index].startswith("*** End Patch"):
        marker = next((item for item in _ENVELOPE_FILES if lines[index].startswith(item)), None)
        if marker is None:
            raise ValueError("Expected file headers")
        target = repo_path(lines[index][len(marker):].strip())
        if target in changes:
            raise ValueError("Invalid, renamed or duplicate patch target")
        index += 1
        body = []
        while index < len(lines) and not lines[index].startswith(("*** End Patch", *_ENVELOPE_FILES)):
            if lines[index].startswith("*** Move to"):
                raise ValueError("Invalid, renamed or duplicate patch target")
            if not lines[index].startswith("*** End of File"):
                body.append(lines[index])
            index += 1
        if marker == _ENVELOPE_FILES[1]:
            if target not in baseline:
                raise ValueError("Patch source is not in baseline")
            changes[target] = None
        elif marker == _ENVELOPE_FILES[0]:
            if target in baseline:
                raise ValueError("New file already exists in baseline")
            if any(line and not line.startswith("+") for line in body):
                raise ValueError("Unsupported patch content")
            changes[target] = "".join(line[1:] + "\n" for line in body)
        else:
            if target not in baseline:
                raise ValueError("Patch source is not in baseline")
            current, cursor, hunks = baseline[target].splitlines(), 0, [[None]]
            for line in body:
                if line.startswith("@@"):
                    hunks.append([line[2:].strip() or None])
                elif line and line[0] not in " +-":
                    raise ValueError("Unsupported patch content")
                else:
                    hunks[-1].append(line or " ")
            if not any(len(hunk) > 1 for hunk in hunks):
                raise ValueError("Missing hunk")
            for anchor, *hunk in hunks:
                if not hunk:
                    continue
                old = [line[1:] for line in hunk if line[0] in " -"]
                new = [line[1:] for line in hunk if line[0] in " +"]
                if old:
                    position = _find_block(current, old, cursor)
                elif anchor is not None:
                    # Pure insertion: placed right after the line named in the hunk header.
                    position = _find_block(current, [anchor], cursor)
                    position = None if position is None else position + 1
                else:
                    position = len(current)
                if position is None:
                    raise ValueError("Patch does not match original baseline")
                current[position:position + len(old)] = new
                cursor = position + len(new)
            changes[target] = "".join(line + "\n" for line in current)
    if not changes:
        raise ValueError("Empty patch")
    return changes


def recount_hunks(patch: str) -> str:
    """Rewrite hunk line counts from the hunk bodies; models routinely miscount them."""
    lines = patch.rstrip("\r\n").splitlines(keepends=True)
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    output, index = [], 0

    def continues(position):
        if position >= len(lines) or lines[position][:1] not in {" ", "+", "-", "\\"}:
            return False
        return not (lines[position].startswith("--- ") and position + 1 < len(lines)
                    and lines[position + 1].startswith("+++ "))

    while index < len(lines):
        match = re.fullmatch(r"@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@([^\r\n]*[\r\n]*)", lines[index])
        if not match:
            output.append(lines[index])
            index += 1
            continue
        body, index, old, new = [], index + 1, 0, 0
        while index < len(lines):
            line = lines[index]
            if not line.strip("\r\n"):
                # An empty context line whose leading space was dropped.
                ahead = index
                while ahead < len(lines) and not lines[ahead].strip("\r\n"):
                    ahead += 1
                if not continues(ahead):
                    break
                line = " " + line
            elif not continues(index):
                break
            if line[0] != "\\":
                old += line[0] in " -"
                new += line[0] in " +"
            body.append(line)
            index += 1
        output.append(f"@@ -{match[1]},{old} +{match[2]},{new} @@{match[3]}")
        output.extend(body)
    return "".join(output)


def apply_tool_patch(baseline: dict[str, str], patch: str) -> dict[str, str | None]:
    """Entry point for the apply_patch tool call. Role output validation keeps the strict parser."""
    if patch.lstrip().startswith("*** Begin Patch"):
        return apply_envelope_patch(baseline, patch.lstrip())
    try:
        return apply_repo_patch(baseline, patch)
    except ValueError:
        # Content still has to match the files exactly; only the declared counts are repaired.
        return apply_repo_patch(baseline, recount_hunks(patch))
