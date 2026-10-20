"""Shared unified-diff validation and the Registry API for current Chain outputs."""

from __future__ import annotations

import json
import re
from typing import Any

from .models import RoleSpec


class OutputFormatError(ValueError):
    """An agent's raw output does not match its role's output contract."""


# ------------------------------------------------------------------ helpers
_FENCE_RE = re.compile(r"^```[A-Za-z0-9_-]*\s*\n(.*?)\n?```\s*$", re.DOTALL)


def _strip_single_fence(text: str) -> str:
    """Tolerate one wrapping markdown fence; the prompts forbid it but models often add it."""
    # Only newlines are trimmed: a trailing " " is a blank context line of a diff and must survive.
    stripped = text.strip("\r\n")
    match = _FENCE_RE.match(stripped.strip())
    return match.group(1) if match else stripped


# ------------------------------------------------------------------ unified diff
_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_EXTENDED_HEADERS = (
    "diff --git ", "index ", "old mode ", "new mode ", "deleted file mode ", "new file mode ",
    "similarity index ", "rename from ", "rename to ", "copy from ", "copy to ",
)


def validate_unified_diff(text: str) -> str:
    """Check that ``text`` is a well-formed unified diff and return it (fence removed).

    Verifies file headers, hunk headers, and that every hunk body has exactly the number of
    old/new lines its header declares. It does NOT check that the patch applies to a
    repository; that is the sandbox's job.
    """
    cleaned = _strip_single_fence(text)
    if not cleaned.strip():
        raise OutputFormatError("empty diff")
    lines = cleaned.splitlines()
    n = len(lines)

    if not (lines[0].startswith("--- ") or lines[0].startswith("diff --git ")):
        raise OutputFormatError("line 1: diff must start with 'diff --git' or '--- ' (no prose before the patch)")

    i = 0
    file_count = 0
    hunks_in_file = 0
    in_file = False

    def close_file() -> None:
        if in_file and hunks_in_file == 0:
            raise OutputFormatError("a file section has headers but no hunk")

    while i < n:
        line = lines[i]
        if line.startswith(_EXTENDED_HEADERS):
            i += 1
        elif line.startswith("--- "):
            close_file()
            if i + 1 >= n or not lines[i + 1].startswith("+++ "):
                raise OutputFormatError(f"line {i + 1}: '--- ' header must be followed by '+++ '")
            file_count += 1
            in_file = True
            hunks_in_file = 0
            i += 2
        elif line.startswith("@@"):
            if not in_file:
                raise OutputFormatError(f"line {i + 1}: hunk before any '--- / +++' file header")
            match = _HUNK_RE.match(line)
            if not match:
                raise OutputFormatError(f"line {i + 1}: malformed hunk header: {line!r}")
            old_left = int(match.group(2)) if match.group(2) is not None else 1
            new_left = int(match.group(4)) if match.group(4) is not None else 1
            i += 1
            while old_left > 0 or new_left > 0:
                if i >= n:
                    raise OutputFormatError("diff is truncated: hunk has fewer lines than its header declares")
                body = lines[i]
                if body.startswith("\\"):  # "\ No newline at end of file"
                    i += 1
                    continue
                kind = body[:1]
                if kind == " " or body == "":
                    if old_left == 0 or new_left == 0:
                        raise OutputFormatError(f"line {i + 1}: too many context lines for hunk header")
                    old_left -= 1
                    new_left -= 1
                elif kind == "-":
                    if old_left == 0:
                        raise OutputFormatError(f"line {i + 1}: more '-' lines than hunk header declares")
                    old_left -= 1
                elif kind == "+":
                    if new_left == 0:
                        raise OutputFormatError(f"line {i + 1}: more '+' lines than hunk header declares")
                    new_left -= 1
                else:
                    raise OutputFormatError(f"line {i + 1}: unexpected line inside hunk: {body!r}")
                i += 1
            while i < n and lines[i].startswith("\\"):
                i += 1
            hunks_in_file += 1
        else:
            raise OutputFormatError(f"line {i + 1}: unexpected text outside a hunk: {line!r}")

    close_file()
    if file_count == 0:
        raise OutputFormatError("diff has no '--- / +++' file header")
    return cleaned.strip("\n") + "\n"


# ------------------------------------------------------------------ entry point
def parse_role_output(role: RoleSpec | str, text: str) -> Any:
    """Public Registry API using the same current output contracts as Chain."""
    from raac.collaboration.levels.l3_collaboration.chain_state import (
        OutputFormatError as ChainOutputError,
        parse_role_output as parse_chain_output,
    )
    role_id = role.id if isinstance(role, RoleSpec) else role
    role_id = role_id.lower() if isinstance(role_id, str) else role_id
    if role_id not in {"analyst", "planner", "coder", "tester", "reviewer"}:
        raise OutputFormatError(f"no output contract registered for role '{role_id}'")
    payload = _strip_single_fence(text)
    if role_id != "coder":
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise OutputFormatError(f"{role_id}: output is not valid JSON") from exc
        if not isinstance(payload, dict):
            raise OutputFormatError(f"{role_id}: output must be a JSON object")
    try:
        parsed = parse_chain_output(role_id, payload)
    except ChainOutputError as exc:
        raise OutputFormatError(str(exc)) from exc
    return parsed.root if role_id == "coder" else parsed
