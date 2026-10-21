"""Role output formats based on 39d39e7, plus revision-aware Chain artifacts.

Wire outputs follow role prompts. RoleHandoff carries framework observations
separately, so model output cannot author execution counts or revision stamps.
ChainState owns collaboration, not an agent's Layer 1 lifecycle.
"""

from dataclasses import dataclass, field
import json
import operator
import time
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, RootModel, StringConstraints, ValidationError, field_validator, model_validator
from typing_extensions import TypedDict

from raac.registry.output_formats import validate_unified_diff

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class StructuredOutput(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")


class AcceptanceCriterion(StructuredOutput):
    id: Text
    description: Text


class Requirement(StructuredOutput):
    id: Text
    description: Text
    priority: Literal["high", "medium", "low"] = "medium"


class AnalystOutput(StructuredOutput):
    summary: Text
    requirements: list[Requirement] = Field(min_length=1)
    constraints: list[Text] = Field(default_factory=list)
    acceptance_criteria: list[Text] = Field(min_length=1)
    open_questions: list[Text] = Field(default_factory=list)
    interface_contracts: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def unique_requirements(self):
        ids = [item.id for item in self.requirements]
        if len(set(ids)) != len(ids):
            raise ValueError("Requirement IDs must be unique")
        return self


class PlanStep(StructuredOutput):
    id: int = Field(ge=1)
    description: Text
    files: list[Text] = Field(default_factory=list)
    depends_on: list[int] = Field(default_factory=list)
    # L3 extension: coverage stays explicit rather than guessed from prose.
    ac_ids: list[Text] = Field(default_factory=list)


class PlannerOutput(StructuredOutput):
    steps: list[PlanStep] = Field(min_length=1)
    covered_ac_ids: list[Text] = Field(default_factory=list)
    interface_contracts: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def ordered_dependencies(self):
        if [step.id for step in self.steps] != list(range(1, len(self.steps) + 1)):
            raise ValueError("Plan step IDs must be consecutive integers starting at 1")
        for step in self.steps:
            if any(dependency < 1 or dependency >= step.id for dependency in step.depends_on):
                raise ValueError("Plan dependencies must reference earlier steps")
        return self


class EnvironmentOutput(StructuredOutput):
    status: Literal["ready", "blocked"]
    summary: Text
    blocking_issues: list[Text] = Field(default_factory=list)

    @model_validator(mode="after")
    def consistent_status(self):
        if self.status == "ready" and self.blocking_issues:
            raise ValueError("Ready environment cannot have blockers")
        if self.status == "blocked" and not self.blocking_issues:
            raise ValueError("Blocked environment must explain its blockers")
        return self


class CoderOutput(RootModel[str]):
    """Unified diff on the wire; empty text explicitly represents a no-op."""
    model_config = ConfigDict(strict=True)

    @field_validator("root")
    @classmethod
    def valid_patch(cls, value):
        return validate_unified_diff(value) if value.strip() else ""


class TestResult(StructuredOutput):
    __test__ = False
    test: Text
    status: Literal["passed", "failed", "error", "skipped"]
    details: str = ""


class TesterOutput(StructuredOutput):
    __test__ = False
    tests_to_run: list[Text] = Field(min_length=1)
    results: list[TestResult] = Field(min_length=1)
    verdict: Literal["pass", "fail"]
    covered_ac_ids: list[Text] = Field(default_factory=list)
    blocking_issues: list[Text] = Field(default_factory=list)

    @model_validator(mode="after")
    def consistent_verdict(self):
        failed = any(result.status in {"failed", "error"} for result in self.results)
        if (self.verdict == "fail") != failed:
            raise ValueError("Tester verdict must agree with its results")
        return self


class ReviewComment(StructuredOutput):
    file: Text | None = None
    line: int | None = Field(default=None, ge=1)
    severity: Literal["blocker", "major", "minor", "nit"]
    message: Text


class ReviewerOutput(StructuredOutput):
    decision: Literal["approve", "reject"]
    summary: Text
    comments: list[ReviewComment] = Field(default_factory=list)
    covered_ac_ids: list[Text] = Field(default_factory=list)

    @model_validator(mode="after")
    def consistent_decision(self):
        if self.decision == "reject" and not self.comments:
            raise ValueError("A reject decision requires at least one comment")
        if self.decision == "approve" and any(comment.severity == "blocker" for comment in self.comments):
            raise ValueError("An approve decision cannot contain blocker comments")
        return self


class OutputFormatError(ValueError):
    """A role's response violates its prompt's output contract."""


JSON_OUTPUT_MODELS = {"analyst": AnalystOutput, "planner": PlannerOutput,
                      "tester": TesterOutput, "reviewer": ReviewerOutput, "environment": EnvironmentOutput}
ROLE_OUTPUT_MODELS = {**JSON_OUTPUT_MODELS, "coder": CoderOutput}


def role_output_model(role: str):
    if not isinstance(role, str) or role.lower() not in ROLE_OUTPUT_MODELS:
        raise OutputFormatError("No Chain output contract registered for role")
    return ROLE_OUTPUT_MODELS[role.lower()]


def _embedded_json_objects(text: str) -> list[dict[str, Any]]:
    """Top-level JSON objects inside prose or fences, in order of appearance."""
    decoder, found, index = json.JSONDecoder(), [], 0
    while (start := text.find("{", index)) != -1:
        try:
            value, end = decoder.raw_decode(text, start)
        except ValueError:
            index = start + 1
            continue
        if isinstance(value, dict):
            found.append(value)
        index = end
    return found


def _recover_embedded(model, text: str):
    """Models sometimes narrate before the report. Accept the last JSON object that fully
    satisfies the role schema; prose alone, or an object that breaks the schema, stays an error."""
    for candidate in reversed(_embedded_json_objects(text)):
        try:
            return model.model_validate(candidate)
        except ValidationError:
            continue
    return None


def parse_role_output(role: str, output: str | dict[str, Any]):
    model = role_output_model(role)
    try:
        if role.lower() == "coder":
            return model.model_validate(output)
        return model.model_validate_json(output) if isinstance(output, str) else model.model_validate(output)
    except (ValidationError, ValueError) as exc:
        if role.lower() != "coder" and isinstance(output, str):
            recovered = _recover_embedded(model, output)
            if recovered is not None:
                return recovered
        if isinstance(exc, ValidationError):
            details = "; ".join(f"{'.'.join(map(str, error['loc'])) or '<root>'}: {error['msg']}"
                                for error in exc.errors(include_input=False, include_url=False))
        else:
            details = "invalid unified diff"
        raise OutputFormatError(f"{role.lower()}: {details}") from exc


@dataclass(frozen=True)
class RoleHandoff:
    """Adapter-owned metadata is separate from untrusted model wire output."""
    output: dict[str, Any] | str
    runtime: dict[str, Any] = field(default_factory=dict)


class RepositoryChange(StructuredOutput):
    path: Text
    deleted: bool


class CodeOutput(StructuredOutput):
    """Framework artifact, not the Coder's wire format."""
    patch: str = ""
    summary: Text
    execution_evidence: list[dict[str, Any]] = Field(default_factory=list, max_length=8)
    repository_changes: list[RepositoryChange] = Field(default_factory=list)


class TestReport(TesterOutput):
    """Role report plus observed execution/revision used by Chain control nodes."""
    observation_ref: str | None = None
    patch_hash: Text
    status: Literal["passed", "failed", "error"]
    tests_run: int = Field(ge=0)
    failed: int = Field(ge=0)
    skipped: int = Field(default=0, ge=0)
    expected_failures: int = Field(default=0, ge=0)
    test_cases: list[Text] = Field(default_factory=list)
    summary: Text

    @model_validator(mode="after")
    def consistent_counts(self):
        if self.failed > self.tests_run or (self.status == "passed" and self.failed):
            raise ValueError("Observed test counts are inconsistent")
        if not 0 <= self.expected_failures <= self.skipped <= self.tests_run - self.failed:
            raise ValueError("Observed skipped/expected failure counts are inconsistent")
        return self


class ReviewReport(ReviewerOutput):
    patch_hash: Text
    verdict: Literal["PASS", "REJECT"]
    blocking_issues: list[Text] = Field(default_factory=list)


WorkSpec = AnalystOutput
TechnicalPlan = PlannerOutput


def execution_report(output: dict, runtime: dict, patch_hash: str) -> TestReport:
    """Use adapter observations when present, otherwise an injected runner's report."""
    results = output["results"]
    failed = sum(item["status"] in {"failed", "error"} for item in results)
    status = "error" if any(item["status"] == "error" for item in results) else (
        "passed" if output["verdict"] == "pass" and all(item["status"] == "passed" for item in results) else "failed")
    observed = {"patch_hash": patch_hash, "status": status, "tests_run": len(results),
                "failed": failed, "test_cases": [item["test"] for item in results],
                "skipped": sum(item["status"] == "skipped" for item in results), "expected_failures": 0,
                "summary": "\n".join(f"{item['test']}: {item['status']} {item['details']}" for item in results)}
    for key in observed:
        if key in runtime:
            observed[key] = runtime[key]
    if "skipped" not in runtime:
        # A zero-test adapter can carry a placeholder skipped result. It is
        # not an executed skip and must not replace the actual suite count.
        observed["skipped"] = min(observed["skipped"], max(0, observed["tests_run"] - observed["failed"]))
    return TestReport.model_validate({**output, **observed, "observation_ref": runtime.get("observation_ref")})


class WorkItem(StructuredOutput):
    run_id: Text
    task_id: Text
    work_id: Text
    scope: Literal["task", "subtask"]
    requirement: Text
    baseline_ref: Text
    acceptance_criteria: list[AcceptanceCriterion] = Field(default_factory=list)
    constraints: list[Text] = Field(default_factory=list)
    parent_context: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def unique_criteria(self):
        ids = [criterion.id for criterion in self.acceptance_criteria]
        if len(set(ids)) != len(ids):
            raise ValueError("Acceptance criterion IDs must be unique")
        return self


class ChainLimits(StructuredOutput):
    max_rounds: int = Field(ge=1)
    hard_max_rounds: int = Field(ge=1)
    timeout_seconds: float = Field(gt=0, allow_inf_nan=False)
    agent_timeout_seconds: float = Field(gt=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def within_hard_limit(self):
        if self.max_rounds > self.hard_max_rounds:
            raise ValueError("max_rounds exceeds hard_max_rounds")
        return self


class ChainState(TypedDict):
    work: dict[str, Any]
    limits: dict[str, Any]
    environment_report: dict[str, Any] | None
    deadline_at: float
    round_number: int
    rounds_completed: int
    spec: dict[str, Any] | None
    ac_catalog: dict[str, str]
    plan: dict[str, Any] | None
    patch: str | None
    patch_hash: str | None
    coder_evidence: list[dict[str, Any]]
    repository_changes: list[dict[str, Any]]
    coder_summary: str | None
    test_report: dict[str, Any] | None
    review_report: dict[str, Any] | None
    tests_passed: bool
    verdict: str | None
    stop_reason: str | None
    error: dict[str, str] | None
    next_node: str
    events: Annotated[list[dict[str, Any]], operator.add]
    result: dict[str, Any] | None


def initial_chain_state(work: WorkItem, limits: ChainLimits) -> ChainState:
    """Start fresh; resume must use this contract version's graph checkpoint."""
    return {
        "work": work.model_dump(), "limits": limits.model_dump(),
        "deadline_at": time.time() + limits.timeout_seconds,
        "round_number": 0, "rounds_completed": 0, "ac_catalog": {},
        "spec": None, "plan": None, "patch": None, "patch_hash": None,
        "coder_evidence": [], "coder_summary": None, "repository_changes": [],
        "test_report": None, "review_report": None, "tests_passed": False,
        "verdict": None, "stop_reason": None, "error": None,
        "environment_report": None,
        "next_node": "analyst", "events": [], "result": None,
    }
