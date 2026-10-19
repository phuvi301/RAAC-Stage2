"""Pydantic schema for the Agent Registry (roles, tools, models, groups).

The registry only *describes* agents. Running them is the job of ``raac.harness``.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ID_PATTERN = r"^[a-z][a-z0-9_]*$"

# Roles every level of the ladder relies on (proposal section 1.2 pipeline).
REQUIRED_CORE_ROLES: tuple[str, ...] = ("analyst", "planner", "coder", "tester", "reviewer")


class _Strict(BaseModel):
    """Base class: unknown YAML keys are errors, models are immutable."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class RoleGroup(str, Enum):
    """Core roles are available at every level; extended roles are only used by L4."""

    CORE = "core"
    EXTENDED = "extended"


class OutputFormat(str, Enum):
    JSON = "json"
    UNIFIED_DIFF = "unified_diff"


class ToolSpec(_Strict):
    """A tool that roles may be allowed to use. Implemented in the harness."""

    name: str = Field(pattern=ID_PATTERN)
    description: str = Field(min_length=1)
    read_only: bool = True


class ModelSpec(_Strict):
    """Which LLM profile a role uses.

    ``ref`` is only a name. The concrete model, temperature and seed are looked up in
    ``configs/llm.yaml`` so that they can be frozen in one place before the pilot.
    """

    ref: str = Field(default="default", min_length=1)


class RoleSpec(_Strict):
    """One agent role in the registry."""

    id: str = Field(pattern=ID_PATTERN)
    role: str = Field(min_length=1, description="Display name, e.g. 'Coder'.")
    group: RoleGroup
    description: str = Field(min_length=1)
    prompt_version: str = Field(pattern=r"^v\d+$", description="Selects registry/prompts/<id>.<version>.md")
    tools: tuple[str, ...] = Field(default=())
    model: ModelSpec = Field(default_factory=ModelSpec)
    # TODO: calibrate with pilot data. 1.0 means "no extra cost weighting".
    cost_weight: float = Field(default=1.0, gt=0)
    output_format: OutputFormat

    @field_validator("tools")
    @classmethod
    def _no_duplicate_tools(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("tools contains duplicates")
        return value

    @property
    def prompt_filename(self) -> str:
        return f"{self.id}.{self.prompt_version}.md"


class ToolCatalog(_Strict):
    """Contents of ``registry/tools.yaml``."""

    tools: tuple[ToolSpec, ...]

    @model_validator(mode="after")
    def _unique_names(self) -> "ToolCatalog":
        names = [t.name for t in self.tools]
        dupes = sorted({n for n in names if names.count(n) > 1})
        if dupes:
            raise ValueError(f"duplicate tool names: {', '.join(dupes)}")
        return self


class Registry(_Strict):
    """A fully loaded and validated registry."""

    version: str
    tools: dict[str, ToolSpec]
    roles: dict[str, RoleSpec]
    prompts: dict[str, str] = Field(description="role id -> raw prompt template text")

    def get_role(self, role_id: str) -> RoleSpec:
        try:
            return self.roles[role_id]
        except KeyError:
            known = ", ".join(sorted(self.roles)) or "<none>"
            raise KeyError(f"unknown role '{role_id}' (known: {known})") from None

    def roles_in_group(self, group: RoleGroup) -> list[RoleSpec]:
        return [r for r in self.roles.values() if r.group == group]
