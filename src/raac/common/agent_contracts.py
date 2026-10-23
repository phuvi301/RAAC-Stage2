"""Shared role wire contracts and trusted adapter handoffs."""

import json
from dataclasses import dataclass, field
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    RootModel,
    StringConstraints,
    ValidationError,
    field_validator,
    model_validator,
)

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
            if any(
                dependency < 1 or dependency >= step.id
                for dependency in step.depends_on
            ):
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
        if self.decision == "approve" and any(
            comment.severity == "blocker" for comment in self.comments
        ):
            raise ValueError("An approve decision cannot contain blocker comments")
        return self


class OutputFormatError(ValueError):
    """A role's response violates its prompt's output contract."""


JSON_OUTPUT_MODELS = {
    "analyst": AnalystOutput,
    "planner": PlannerOutput,
    "tester": TesterOutput,
    "reviewer": ReviewerOutput,
    "environment": EnvironmentOutput,
}
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
        return (
            model.model_validate_json(output)
            if isinstance(output, str)
            else model.model_validate(output)
        )
    except (ValidationError, ValueError) as exc:
        if role.lower() != "coder" and isinstance(output, str):
            recovered = _recover_embedded(model, output)
            if recovered is not None:
                return recovered
        if isinstance(exc, ValidationError):
            details = "; ".join(
                f"{'.'.join(map(str, error['loc'])) or '<root>'}: {error['msg']}"
                for error in exc.errors(include_input=False, include_url=False)
            )
        else:
            details = "invalid unified diff"
        raise OutputFormatError(f"{role.lower()}: {details}") from exc


@dataclass(frozen=True)
class RoleHandoff:
    """Adapter-owned metadata is separate from untrusted model wire output."""

    output: dict[str, Any] | str
    runtime: dict[str, Any] = field(default_factory=dict)
