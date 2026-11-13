"""Artifacts of the flow. Each phase produces one of them; later phases read the earlier ones."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Text = str


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------- 1. clarify
class Question(Model):
    id: str = Field(pattern=r"^Q\d+$")
    question: Text = Field(min_length=1)
    why: Text = Field(min_length=1, description="What depends on the answer")
    default_answer: Text = Field(min_length=1, description="Used when the user does not answer")
    blocking: bool = Field(description="True if the spec cannot be written responsibly without an answer")


class Clarification(Model):
    understanding: Text = Field(min_length=1, description="The request restated in one paragraph")
    questions: list[Question] = Field(default_factory=list, max_length=12)


class Answer(Model):
    question_id: str
    answer: Text
    source: Literal["user", "default"]


# ---------------------------------------------------------------- 2. specify
class Requirement(Model):
    id: str = Field(pattern=r"^(FR|NFR)-\d+$")
    kind: Literal["functional", "non_functional"]
    title: Text = Field(min_length=1)
    statement: Text = Field(min_length=1)
    priority: Literal["must", "should", "could"]
    acceptance_criteria: list[Text] = Field(min_length=1)
    # Only for non-functional requirements: what is measured and the target value.
    metric: Text | None = None
    target: Text | None = None

    @field_validator("kind")
    @classmethod
    def kind_matches_id(cls, value, info):
        identifier = info.data.get("id", "")
        expected = "functional" if identifier.startswith("FR-") else "non_functional"
        if identifier and value != expected:
            raise ValueError(f"{identifier} must have kind {expected}")
        return value


class Spec(Model):
    summary: Text = Field(min_length=1)
    scope_in: list[Text] = Field(default_factory=list)
    scope_out: list[Text] = Field(default_factory=list)
    requirements: list[Requirement] = Field(min_length=1)
    assumptions: list[Text] = Field(default_factory=list)

    def ac_ids(self) -> list[str]:
        """Stable IDs for acceptance criteria: FR-1.AC1, FR-1.AC2, ..."""
        return [f"{req.id}.AC{n}" for req in self.requirements for n, _ in enumerate(req.acceptance_criteria, 1)]


# ---------------------------------------------------------------- 3. architect
class Option(Model):
    name: Text
    pros: list[Text] = Field(default_factory=list)
    cons: list[Text] = Field(default_factory=list)


class Decision(Model):
    id: str = Field(pattern=r"^ADR-\d+$")
    topic: Text = Field(min_length=1, description="e.g. language, web framework, database, test framework")
    choice: Text = Field(min_length=1)
    rationale: Text = Field(min_length=1)
    alternatives: list[Option] = Field(default_factory=list)
    requirement_ids: list[str] = Field(default_factory=list, description="Requirements that drive this choice")


class Architecture(Model):
    context: Literal["new_project", "existing_codebase"]
    overview: Text = Field(min_length=1)
    components: list[Text] = Field(default_factory=list)
    decisions: list[Decision] = Field(min_length=1)


# ---------------------------------------------------------------- 3b. environment
class Runtime(Model):
    name: Text = Field(min_length=1, description="e.g. node, python, npm, postgresql")
    version: Text = Field(min_length=1, description="The exact version to use, e.g. 24.21.0 or 3.12")
    source: Literal["installed", "install"] = Field(description="Already on this machine, or must be installed")
    path: Text | None = Field(default=None, description="Executable path when already installed")
    install_command: Text | None = Field(default=None, description="Command to install it (only when source=install)")
    reason: Text = Field(min_length=1)


class EnvironmentPlan(Model):
    runtimes: list[Runtime] = Field(min_length=1)
    package_manager: Text = Field(min_length=1, description="e.g. npm, pip, uv, poetry")
    dependency_install: Text = Field(min_length=1, description="How dependencies are installed once a manifest exists")
    test_command: Text = Field(min_length=1, description="How the test suite is run from the project root")
    run_command: Text | None = Field(default=None, description="How the application is started locally")
    check_commands: list[Text] = Field(min_length=1, description="Quick commands proving the runtimes work")
    notes: list[Text] = Field(default_factory=list, description="Risks and compatibility remarks (not blockers)")


# ---------------------------------------------------------------- 4. plan
class TaskNode(Model):
    id: str = Field(pattern=r"^T\d+$")
    title: Text = Field(min_length=1)
    description: Text = Field(min_length=1)
    depends_on: list[str] = Field(default_factory=list)
    preconditions: list[Text] = Field(default_factory=list, description="Facts that must hold before starting")
    requirement_ids: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(min_length=1, description="AC IDs such as FR-1.AC2")
    files: list[Text] = Field(default_factory=list, description="Files or modules expected to change")
    priority: int = Field(ge=1, le=5, description="1 = highest")
    size: Literal["S", "M", "L"]


class TaskGraph(Model):
    tasks: list[TaskNode] = Field(min_length=1)
    integration_notes: Text = ""


# ---------------------------------------------------------------- run state
PHASES = ("clarify", "specify", "architect", "environment", "plan")
TaskStatus = Literal["pending", "running", "passed", "failed", "blocked"]


class FlowState(Model):
    project: str
    repo: str | None = None  # an existing codebase to describe to the model (read-only)
    phase: Literal["new", "clarify", "specify", "architect", "environment", "plan", "done"] = "new"
    approved: dict[str, bool] = Field(default_factory=dict)
    clarify_rounds: int = 0
    task_status: dict[str, TaskStatus] = Field(default_factory=dict)
