"""Role output formats based on 39d39e7, plus revision-aware Chain artifacts.

Wire outputs follow role prompts. RoleHandoff carries framework observations
separately, so model output cannot author execution counts or revision stamps.
ChainState owns collaboration, not an agent's Layer 1 lifecycle.
"""

import operator
import time
from typing import Annotated, Any, Literal

from pydantic import Field, model_validator
from typing_extensions import TypedDict

# Preserve existing collaboration imports after extracting shared wire contracts.
from raac.common.agent_contracts import (  # noqa: F401
    JSON_OUTPUT_MODELS,
    ROLE_OUTPUT_MODELS,
    AcceptanceCriterion,
    AnalystOutput,
    CoderOutput,
    EnvironmentOutput,
    OutputFormatError,
    PlannerOutput,
    PlanStep,
    Requirement,
    ReviewComment,
    ReviewerOutput,
    RoleHandoff,
    StructuredOutput,
    TesterOutput,
    TestResult,
    Text,
    _embedded_json_objects,
    _recover_embedded,
    parse_role_output,
    role_output_model,
)


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
