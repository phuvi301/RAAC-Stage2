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


class Assumption(StructuredOutput):
    """A reading the Analyst chose because the request leaves a behaviour undecided; nobody is asked during a run."""
    question: Text
    assumption: Text
    affects_behavior: bool = True


## Output contracts for each role, with validation and recovery of embedded JSON objects in prose.
from pydantic import Field, model_validator

class UserFlow(StructuredOutput):
    id: Text = Field(description="Unique identifier for the flow, e.g., UF1, UF2")
    title: Text = Field(description="Short, descriptive name of the user journey or system workflow")
    actor: Text = Field(description="Primary user role, external system, or trigger initiating this flow")
    steps: list[Text] = Field(
        min_length=1,
        description="Ordered sequence of interaction steps and state transitions from trigger to completion"
    )

class AnalystOutput(StructuredOutput):
    # 1. Product Overview & Context
    product_goal: Text = Field(
        description="High-level purpose, core value proposition, and problem the software solves"
    )
    target_users: list[Text] = Field(
        min_length=1,
        description="Distinct user roles, personas, or external client systems interacting with the software"
    )

    # 2. Flows & Requirements
    user_flows: list[UserFlow] = Field(
        min_length=1,
        description="End-to-end user journeys or operational workflows through the system"
    )
    functional_requirements: list[Requirement] = Field(
        min_length=1,
        description="Distinct functional capabilities the system must provide (IDs: FR1, FR2, ...)"
    )
    non_functional_requirements: list[Text] = Field(
        default_factory=list,
        description="Technical stack mandates, performance, security, scalability, runtime constraints, and backward-compatibility rules that guide architectural decisions"
    )
    interface_contracts: dict[str, str] = Field(
        default_factory=dict,
        description="Externally mandated public signatures, REST API endpoints, CLI arguments, data schemas, or I/O formats stated in the request or existing repo"
    )

    # 3. Verification & Scope Boundaries
    acceptance_criteria: list[Text] = Field(
        min_length=1,
        description="Testable, concrete pass/fail rules covering business logic, edge cases, bounds, and error outputs"
    )
    out_of_scope: list[Text] = Field(
        default_factory=list,
        description="Features, architectural complexity, integrations, or behaviors explicitly excluded to prevent over-engineering"
    )

    # 4. Ambiguity & Risk Management
    assumptions: list[Assumption] = Field(
        default_factory=list,
        description="Documented interpretations chosen to resolve ambiguity so the Architect is not blocked"
    )
    open_questions: list[Text] = Field(
        default_factory=list,
        description="Unresolved requirement gaps or contradictions that cannot be safely assumed"
    )

    @model_validator(mode="after")
    def unique_ids(self):
        fr_ids = [item.id for item in self.functional_requirements]
        if len(set(fr_ids)) != len(fr_ids):
            raise ValueError("Functional Requirement IDs (FR1, FR2, ...) must be unique")

        uf_ids = [flow.id for flow in self.user_flows]
        if len(set(uf_ids)) != len(uf_ids):
            raise ValueError("User Flow IDs (UF1, UF2, ...) must be unique")

        return self


class ComponentSpec(StructuredOutput):
    id: Text = Field(description="Unique component identifier, e.g., COMP1, COMP2")
    name: Text = Field(description="Logical name of the module, service, or layer")
    path: Text = Field(description="Repository-relative file or directory path")
    action: Literal["create", "modify", "delete"] = Field(
        description="Whether this component is newly created (greenfield) or modified/deleted (brownfield)"
    )
    responsibilities: Text = Field(
        description="Core logic, reponsibilities, and exact changes or capabilities this component implements"
    )
    satisfies_requirements: list[Text] = Field(
        min_length=1,
        description="Requirement IDs (FR1, FR2...) or User Flow IDs (UF1...) from AnalystOutput fulfilled here"
    )
    dependencies: list[Text] = Field(
        default_factory=list,
        description="Other Component IDs (COMP1...) or external packages this component depends on"
    )

class DataModelSpec(StructuredOutput):
    name: Text = Field(description="Entity, database table, or domain state model name")
    fields: dict[str, str] = Field(
        description="Field/column names mapped to their data types, constraints, and defaults"
    )
    relationships_and_indexes: list[Text] = Field(
        default_factory=list,
        description="Foreign keys, relations, uniqueness constraints, or state invariants"
    )

class ArchitectOutput(StructuredOutput):
    # 1. High-level Overview & Structure
    summary: Text = Field(
        description="High-level architectural style, system topology, and how the design satisfies the specification"
    )
    project_structure: list[Text] = Field(
        min_length=1,
        description="Planned repository directory and file tree (relative paths)"
    )
    components: list[ComponentSpec] = Field(
        min_length=1,
        description="All created or affected architectural components and their requirement mappings"
    )

    # 2. Data & Interface Contracts
    data_models: list[DataModelSpec] = Field(
        default_factory=list,
        description="Domain models, persistence schemas, or core data structures"
    )
    api_contracts: dict[str, str] = Field(
        default_factory=dict,
        description="External/public contracts: REST endpoints, CLI signatures, request/response schemas, error formats"
    )
    interface_contracts: dict[str, str] = Field(
        default_factory=dict,
        description="Internal module-to-module function/class signatures and preserved specification contracts"
    )

    # 3. Runtime, Dependencies & Environment
    runtime_contract: dict[str, str] = Field(
        default_factory=dict,
        description="Language/runtime version, application entrypoints, execution model (sync/async), and build/run commands"
    )
    dependency_choices: dict[str, str] = Field(
        default_factory=dict,
        description="Selected third-party libraries/frameworks mapped to their purpose and version constraints"
    )
    configuration_contract: dict[str, str] = Field(
        default_factory=dict,
        description="Environment variables, config files, default values, and required secrets"
    )

    # 4. Decisions, Security, Deployment & Risks
    design_decisions: list[Text] = Field(
        default_factory=list,
        description="Key architectural patterns, trade-offs, state management, and error-handling strategies"
    )
    security_boundaries: list[Text] = Field(
        default_factory=list,
        description="Authentication, authorization, input validation boundaries, and trust zones"
    )
    deployment_constraints: list[Text] = Field(
        default_factory=list,
        description="Containerization, OS/environment constraints, statelessness, or resource limits"
    )
    constraints: list[Text] = Field(
        default_factory=list,
        description="Implementation rules and backward-compatibility constraints that Planner and Coder must not violate"
    )
    risks: list[Text] = Field(
        default_factory=list,
        description="Technical risks, spec-vs-code discrepancies, edge-case bottlenecks, and mitigation notes"
    )

    @model_validator(mode="after")
    def unique_component_ids(self):
        comp_ids = [c.id for c in self.components]
        if len(set(comp_ids)) != len(comp_ids):
            raise ValueError("Component IDs (COMP1, COMP2, ...) must be unique")
        return self


PlanPhase = Literal[
    "bootstrap",
    "data_model",
    "core_service",
    "interface",
    "integration",
    "configuration",
    "testing",
    "documentation",
    "packaging",
]

class PlanStep(StructuredOutput):
    id: int = Field(ge=1, description="Consecutive step integer starting at 1")
    phase: PlanPhase = Field(
        default="core_service",
        description="SDLC delivery phase this step belongs to"
    )
    description: Text = Field(
        description="Concrete, actionable implementation instructions for the Coder"
    )
    files: list[Text] = Field(
        default_factory=list,
        description="Ordered list of repository-relative file paths to create or modify in this step"
    )
    component_ids: list[Text] = Field(
        default_factory=list,
        description="Component IDs (COMP1, ...) from ArchitectOutput implemented or touched in this step"
    )
    depends_on: list[int] = Field(
        default_factory=list,
        description="Earlier step IDs that must be completed before this step so imports and configs exist"
    )
    # L3 extension: coverage stays explicit rather than guessed from prose.
    ac_ids: list[Text] = Field(
        default_factory=list,
        description="Acceptance Criteria IDs from artifacts.ac_catalog satisfied or verified by this step"
    )
    verification: Text = Field(
        default="",
        description="Concrete check or command output confirming this step succeeded"
    )


class TestStrategy(StructuredOutput):
    test_framework: Text = Field(
        description="Selected test runner/framework appropriate for the project stack (never assume pytest if not Python/configured)"
    )
    test_command: Text = Field(
        description="Exact command to execute the automated test suite"
    )
    working_directory: Text = Field(
        default=".",
        description="Working directory (cwd) for running tests and build commands"
    )
    unit_and_integration_scope: Text = Field(
        default="",
        description="How core services, data models, and component boundaries are unit/integration tested"
    )
    acceptance_test_plan: list[Text] = Field(
        default_factory=list,
        description="How end-to-end user flows (UF) and acceptance criteria (AC) are verified against the entrypoint"
    )


class PlannerOutput(StructuredOutput):
    # 1. Delivery & Bootstrap Overview
    project_bootstrap: list[Text] = Field(
        default_factory=list,
        description="Initial project scaffolding, manifest files, directory creation, and dependency setup requirements"
    )
    entrypoints: dict[str, str] = Field(
        default_factory=dict,
        description="Application entrypoints (e.g., main CLI, HTTP server, library export, UI root) mapped to their file paths/commands"
    )
    build_and_run_commands: dict[str, str] = Field(
        default_factory=dict,
        description="Commands for install/setup, build/compile, start/run, and packaging (e.g., {'install': '...', 'build': '...', 'run': '...'})"
    )

    # 2. Ordered Implementation Steps (Authoritative L3 Sequence)
    steps: list[PlanStep] = Field(
        min_length=1,
        description="Ordered implementation steps from scaffolding to tests and packaging"
    )

    # 3. Testing, Coverage & Contracts
    test_strategy: TestStrategy = Field(
        description="Explicit test runner, commands, cwd, and acceptance verification strategy"
    )
    covered_ac_ids: list[Text] = Field(
        default_factory=list,
        description="Complete list of AC IDs from artifacts.ac_catalog covered across all steps"
    )
    interface_contracts: dict[str, str] = Field(
        default_factory=dict,
        description="Public/API/data contracts finalized for the Tester and Coder, plus test_command, cwd, env, and entrypoint contracts"
    )
    final_deliverables: list[Text] = Field(
        default_factory=list,
        description="Expected end-state artifacts (runnable binaries/scripts, APIs, config templates, test suites, documentation)"
    )

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


class StepExecutionRecord(StructuredOutput):
    step_id: int = Field(ge=1, description="Step ID from PlannerOutput")
    status: Literal["done", "partial", "blocked"] = Field(
        description="Execution status of this plan step"
    )
    summary: Text = Field(
        description="What was implemented/changed and why, or blocker reason if not done"
    )

class IssueResolutionRecord(StructuredOutput):
    issue_label: Text = Field(
        description="Short identifier or point from Tester/Reviewer feedback or runtime error"
    )
    status: Literal["fixed", "unfixed"] = Field(
        description="Whether the blocking issue was resolved"
    )
    resolution: Text = Field(
        description="How the issue was fixed, or why it remains unfixed"
    )

class DeveloperOutput(StructuredOutput):
    # 1. High-Level Summary & Artifact Inventory
    summary: Text = Field(
        description="Concise summary of the implementation, architecture alignment, and verification performed"
    )
    created_files: list[Text] = Field(
        default_factory=list,
        description="Repository-relative paths of all newly created files"
    )
    modified_files: list[Text] = Field(
        default_factory=list,
        description="Repository-relative paths of all modified existing files"
    )
    deleted_files: list[Text] = Field(
        default_factory=list,
        description="Repository-relative paths of any removed obsolete files"
    )

    # 2. Execution & Operational Handoff (Crucial for Tester)
    entrypoint: Text = Field(
        default="",
        description="Primary application or module entrypoint path/target verified in the workspace"
    )
    run_command: list[Text] = Field(
        default_factory=list,
        description="Command argv to start or invoke the application (e.g., ['python', '-m', 'app.main'])"
    )
    test_command: list[Text] = Field(
        default_factory=list,
        description="Verified command argv for the Tester to run the test suite (e.g., ['pytest', '-q'])"
    )

    # 3. Feature & Plan Traceability
    implemented_features: list[Text] = Field(
        default_factory=list,
        description="Implemented capabilities mapped to Requirement IDs (FRx), Component IDs (COMPx), or AC IDs"
    )
    step_results: list[StepExecutionRecord] = Field(
        default_factory=list,
        description="Status report for each step in PlannerOutput.steps"
    )
    issue_resolutions: list[IssueResolutionRecord] = Field(
        default_factory=list,
        description="Resolution report for each blocking item on retry from test_report or review_report"
    )
    known_limitations: list[Text] = Field(
        default_factory=list,
        description="Any remaining risks, unimplemented edge cases, or environment blockers"
    )


class TestResult(StructuredOutput):
    __test__ = False
    test: Text = Field(description="Test identifier, node ID, or runtime check name")
    status: Literal["passed", "failed", "error", "skipped"]
    details: str = Field(
        default="",
        description="Key assertion failure, traceback line, or error reason if failed/error"
    )

class AcceptanceResult(StructuredOutput):
    __test__ = False
    ac_id: Text = Field(description="Acceptance Criterion ID from artifacts.ac_catalog (e.g., AC1)")
    status: Literal["passed", "failed", "error", "untested"]
    verified_by: list[Text] = Field(
        default_factory=list,
        description="Test node IDs or runtime commands that verified this criterion"
    )
    details: str = Field(
        default="",
        description="Observed outcome or failure details against the expected criterion"
    )

class TesterOutput(StructuredOutput):
    __test__ = False
    # 1. Core Harness Fields (Preserved for L3 Adapter Compatibility)
    tests_to_run: list[Text] = Field(
        min_length=1,
        description="Complete list of test targets/identifiers executed in the final suite"
    )
    results: list[TestResult] = Field(
        min_length=1,
        description="Aggregated execution results for all tests run in the final suite"
    )
    verdict: Literal["pass", "fail"]

    # 2. Three-Tier Evidence Breakdown (SDLC / Greenfield Extensions)
    generated_tests: list[Text] = Field(
        default_factory=list,
        description="Repository-relative paths or node IDs of independent tests created or updated by the Tester"
    )
    runtime_test_results: list[TestResult] = Field(
        default_factory=list,
        description="Results of build, import, project structure, entrypoint, config, security, and clean-run checks"
    )
    acceptance_results: list[AcceptanceResult] = Field(
        default_factory=list,
        description="Explicit verification results mapped to each Acceptance Criterion in artifacts.ac_catalog"
    )

    # 3. Coverage & Escalation
    covered_ac_ids: list[Text] = Field(
        default_factory=list,
        description="AC IDs from artifacts.ac_catalog backed by executed test evidence"
    )
    blocking_issues: list[Text] = Field(
        default_factory=list,
        description="Actionable implementation bugs, contract violations, or missing requirements for the Developer"
    )

    @model_validator(mode="after")
    def consistent_verdict(self):
        failed_test = any(
            result.status in {"failed", "error"} for result in self.results
        )
        failed_runtime = any(
            result.status in {"failed", "error"} for result in self.runtime_test_results
        )
        failed_ac = any(
            ac.status in {"failed", "error", "untested"} for ac in self.acceptance_results
        )
        has_failure = failed_test or failed_runtime or failed_ac
        if (self.verdict == "fail") != has_failure:
            raise ValueError(
                "Tester verdict must agree with results, runtime_test_results, and acceptance_results"
            )
        return self


ReviewCategory = Literal[
    "requirement_coverage",
    "architecture_consistency",
    "implementation_completeness",
    "build_reproducibility",
    "runtime_entrypoint",
    "test_quality",
    "security_constraints",
    "configuration_completeness",
    "documentation_sufficiency",
    "assumption",
    "general",
]

class ReviewComment(StructuredOutput):
    file: Text | None = Field(
        default=None,
        description="Repository-relative file path, or None for project-wide issues"
    )
    line: int | None = Field(
        default=None,
        ge=1,
        description="1-based line number when pointing to a specific code location"
    )
    category: ReviewCategory = Field(
        default="general",
        description="Software artifact evaluation dimension this comment addresses"
    )
    severity: Literal["blocker", "major", "minor", "nit"] = Field(
        description="blocker: broken/unsafe/unmet AC; major: should fix before release; minor: small improvement or assumption check; nit: style"
    )
    message: Text = Field(
        description="Specific, actionable finding stating what is wrong and what must change"
    )


class ReviewerOutput(StructuredOutput):
    decision: Literal[
        "approve",
        "request_changes",
        "reject_final",
        "approve_with_known_failures",
    ] = Field(
        description=(
            "approve: all ACs & tests pass, no blockers; "
            "request_changes: actionable defects found, route back to Developer; "
            "reject_final: terminal failure or final iteration still failing required criteria; "
            "approve_with_known_failures: non-blocking limitations explicitly accepted"
        )
    )
    stop: bool = Field(
        description="False ONLY when decision=='request_changes' (continue loop); True for all terminal decisions"
    )
    completion_status: Literal[
        "completed",
        "needs_changes",
        "completed_with_failures",
        "failed",
    ] = Field(
        description="Distinguishes workflow termination from actual software quality outcome"
    )
    summary: Text = Field(
        description="Concise synthesis of software artifact readiness, test verification, and decision rationale"
    )
    blocking_failures: list[Text] = Field(
        default_factory=list,
        description="Explicit list of unmet acceptance criteria, failing tests, or blocker defects preventing clean approval"
    )
    comments: list[ReviewComment] = Field(
        default_factory=list,
        description="Specific, file/line-anchored review comments across the 9 artifact dimensions"
    )
    covered_ac_ids: list[Text] = Field(
        default_factory=list,
        description="Acceptance Criteria IDs from artifacts.ac_catalog verified by code inspection and executed tests"
    )

    @model_validator(mode="after")
    def consistent_decision(self):
        has_blocker_comment = any(c.severity == "blocker" for c in self.comments)

        if self.decision == "approve":
            if not self.stop:
                raise ValueError("An 'approve' decision must set stop=True")
            if self.completion_status != "completed":
                raise ValueError("An 'approve' decision must have completion_status='completed'")
            if has_blocker_comment or self.blocking_failures:
                raise ValueError("An 'approve' decision cannot have blocker comments or blocking_failures")

        elif self.decision == "request_changes":
            if self.stop:
                raise ValueError("A 'request_changes' decision must set stop=False so the Developer can fix the issues")
            if self.completion_status != "needs_changes":
                raise ValueError("A 'request_changes' decision must have completion_status='needs_changes'")
            if not self.comments and not self.blocking_failures:
                raise ValueError("A 'request_changes' decision requires at least one comment or blocking failure")

        elif self.decision == "reject_final":
            if not self.stop:
                raise ValueError("A 'reject_final' decision must set stop=True")
            if self.completion_status not in {"completed_with_failures", "failed"}:
                raise ValueError("A 'reject_final' decision must have completion_status in {'completed_with_failures', 'failed'}")
            if not self.comments and not self.blocking_failures:
                raise ValueError("A 'reject_final' decision requires at least one comment or blocking failure")

        elif self.decision == "approve_with_known_failures":
            if not self.stop:
                raise ValueError("An 'approve_with_known_failures' decision must set stop=True")
            if self.completion_status != "completed_with_failures":
                raise ValueError("An 'approve_with_known_failures' decision must have completion_status='completed_with_failures'")
            if not self.blocking_failures and not self.comments:
                raise ValueError("An 'approve_with_known_failures' decision must document the known failures")

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


class HubOutput(StructuredOutput):
    """Hub routing decision. The framework validates it against the allowed set before use."""
    next: Literal["analyst", "planner", "coder", "tester", "reviewer", "finish"]
    reason: Text
    note: str = ""


class SkippedRole(StructuredOutput):
    role: Literal["Analyst", "Planner", "Reviewer"]
    reason: Text


ROUTABLE_ROLES = ("Analyst", "Planner", "Coder", "Tester", "Reviewer")


class RouterOutput(StructuredOutput):
    selected_roles: list[Literal["Analyst", "Planner", "Coder", "Tester", "Reviewer"]] = Field(min_length=2)
    skipped: list[SkippedRole] = Field(default_factory=list)
    reason: Text

    @model_validator(mode="after")
    def valid_selection(self):
        ordered = [role for role in ROUTABLE_ROLES if role in self.selected_roles]
        if len(set(self.selected_roles)) != len(self.selected_roles) or self.selected_roles != ordered:
            raise ValueError("selected_roles must follow the fixed catalog order without duplicates")
        if not {"Coder", "Tester"}.issubset(self.selected_roles):
            raise ValueError("selected_roles must always include Coder and Tester")
        skipped_names = [item.role for item in self.skipped]
        expected_skipped = [role for role in ROUTABLE_ROLES if role not in self.selected_roles]
        if len(set(skipped_names)) != len(skipped_names) or set(skipped_names) != set(expected_skipped):
            raise ValueError("skipped must list every omitted role from the catalog exactly once with a reason")
        return self


class OutputFormatError(ValueError):
    """A role's response violates its prompt's output contract."""


JSON_OUTPUT_MODELS = {
    "analyst": AnalystOutput,
    "architect": ArchitectOutput,
    "planner": PlannerOutput,
    "tester": TesterOutput,
    "reviewer": ReviewerOutput,
    "environment": EnvironmentOutput,
    "hub": HubOutput,
    "router": RouterOutput,
}
ROLE_OUTPUT_MODELS = {**JSON_OUTPUT_MODELS, "coder": DeveloperOutput}


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
