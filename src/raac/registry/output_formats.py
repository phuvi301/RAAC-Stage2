"""Output contracts for the core roles, and validation of raw LLM output against them.

Each role's system prompt (``registry/prompts/<id>.<version>.md``) tells the model to
produce one of these shapes. ``parse_role_output`` is the single place that checks it, so
unit tests, the harness validators and the evaluator can share the same definition.
"""

from __future__ import annotations

import json
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .models import OutputFormat, RoleSpec


class OutputFormatError(ValueError):
    """An agent's raw output does not match its role's output contract."""


class _Out(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ------------------------------------------------------------------ Analyst
class Requirement(_Out):
    id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    priority: Literal["high", "medium", "low"] = "medium"


class AnalystOutput(_Out):
    summary: str = Field(min_length=1)
    requirements: list[Requirement] = Field(min_length=1)
    constraints: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(min_length=1)
    open_questions: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique_requirement_ids(self) -> "AnalystOutput":
        ids = [r.id for r in self.requirements]
        if len(set(ids)) != len(ids):
            raise ValueError("requirement ids must be unique")
        return self


# ------------------------------------------------------------------ Planner
class PlanStep(_Out):
    id: int = Field(ge=1)
    description: str = Field(min_length=1)
    files: list[str] = Field(default_factory=list)
    depends_on: list[int] = Field(default_factory=list)


class PlannerOutput(_Out):
    steps: list[PlanStep] = Field(min_length=1)

    @model_validator(mode="after")
    def _ids_and_dependencies(self) -> "PlannerOutput":
        expected = list(range(1, len(self.steps) + 1))
        if [s.id for s in self.steps] != expected:
            raise ValueError("step ids must be consecutive integers starting at 1")
        for step in self.steps:
            bad = [d for d in step.depends_on if d >= step.id or d < 1]
            if bad:
                raise ValueError(f"step {step.id} depends_on must reference earlier steps, got {bad}")
        return self


# ------------------------------------------------------------------ Tester
class TestResult(_Out):
    __test__ = False  # not a pytest class

    test: str = Field(min_length=1)
    status: Literal["passed", "failed", "error", "skipped"]
    details: str = ""


class TesterOutput(_Out):
    __test__ = False  # not a pytest class

    tests_to_run: list[str] = Field(min_length=1)
    results: list[TestResult] = Field(min_length=1)
    verdict: Literal["pass", "fail"]

    @model_validator(mode="after")
    def _verdict_matches_results(self) -> "TesterOutput":
        has_failure = any(r.status in ("failed", "error") for r in self.results)
        if has_failure and self.verdict == "pass":
            raise ValueError("verdict is 'pass' but some results are failed/error")
        if not has_failure and self.verdict == "fail":
            raise ValueError("verdict is 'fail' but no result is failed/error")
        return self


# ------------------------------------------------------------------ Reviewer
class ReviewComment(_Out):
    file: str | None = None
    line: int | None = Field(default=None, ge=1)
    severity: Literal["blocker", "major", "minor", "nit"]
    message: str = Field(min_length=1)


class ReviewerOutput(_Out):
    decision: Literal["approve", "reject"]
    summary: str = Field(min_length=1)
    comments: list[ReviewComment] = Field(default_factory=list)

    @model_validator(mode="after")
    def _decision_matches_comments(self) -> "ReviewerOutput":
        if self.decision == "reject" and not self.comments:
            raise ValueError("a 'reject' decision needs at least one comment")
        if self.decision == "approve" and any(c.severity == "blocker" for c in self.comments):
            raise ValueError("an 'approve' decision cannot carry blocker comments")
        return self


JSON_OUTPUT_MODELS: dict[str, type[BaseModel]] = {
    "analyst": AnalystOutput,
    "planner": PlannerOutput,
    "tester": TesterOutput,
    "reviewer": ReviewerOutput,
}

# ------------------------------------------------------------------ helpers
_FENCE_RE = re.compile(r"^```[A-Za-z0-9_-]*\s*\n(.*?)\n?```\s*$", re.DOTALL)


def _strip_single_fence(text: str) -> str:
    """Tolerate one wrapping markdown fence; the prompts forbid it but models often add it."""
    # Only newlines are trimmed: a trailing " " is a blank context line of a diff and must survive.
    stripped = text.strip("\r\n")
    match = _FENCE_RE.match(stripped.strip())
    return match.group(1) if match else stripped


def _format_pydantic_error(exc: ValidationError) -> str:
    parts = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err["loc"]) or "<root>"
        parts.append(f"{loc}: {err['msg']}")
    return "; ".join(parts)


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
    """Validate a role's raw output.

    Returns the validated pydantic model for JSON roles, or the cleaned diff string for the
    Coder. Raises :class:`OutputFormatError` with a message that can be fed back to the agent.
    """
    role_id = role.id if isinstance(role, RoleSpec) else role
    fmt = role.output_format if isinstance(role, RoleSpec) else None

    if role_id == "coder" or fmt is OutputFormat.UNIFIED_DIFF:
        return validate_unified_diff(text)

    model = JSON_OUTPUT_MODELS.get(role_id)
    if model is None:
        raise OutputFormatError(f"no output contract registered for role '{role_id}'")

    payload = _strip_single_fence(text)
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise OutputFormatError(f"{role_id}: output is not valid JSON ({exc.msg} at line {exc.lineno})") from exc
    if not isinstance(data, dict):
        raise OutputFormatError(f"{role_id}: output must be a JSON object")
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        raise OutputFormatError(f"{role_id}: {_format_pydantic_error(exc)}") from exc
