"""One Registry schema for core/extended roles, tools and the MAS runtime."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
from pathlib import PurePosixPath
from typing import Annotated, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

ID_PATTERN = r"^[a-z][a-z0-9_]*$"
REQUIRED_CORE_ROLES = ("analyst", "planner", "coder", "tester", "reviewer")
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class RoleGroup(str, Enum):
    CORE = "core"
    EXTENDED = "extended"


class OutputFormat(str, Enum):
    JSON = "json"
    UNIFIED_DIFF = "unified_diff"


class ToolSpec(_Strict):
    name: str = Field(pattern=ID_PATTERN)
    description: Text
    read_only: bool = True


class ModelSpec(_Strict):
    """Reference to default settings or a named profile in configs/llm.yaml."""
    ref: str = Field(default="default", pattern=ID_PATTERN)


class RoleSpec(_Strict):
    id: str = Field(pattern=ID_PATTERN)
    role: Text
    group: RoleGroup = Field(default=RoleGroup.CORE, validation_alias=AliasChoices("group", "Group"))
    prompt: Text
    prompt_version: Text
    tools: list[str]
    model: ModelSpec = Field(default_factory=ModelSpec)
    cost_weight: float = Field(default=1.0, gt=0, allow_inf_nan=False)
    max_llm_calls: int = Field(ge=1, le=20)
    # Missing inherits the Chain limit; explicit null removes this role deadline.
    timeout_seconds: float | None | Literal["inherit"] = "inherit"

    @field_validator("timeout_seconds")
    @classmethod
    def valid_timeout(cls, value):
        if value is not None and value != "inherit":
            import math
            if isinstance(value, bool) or not math.isfinite(value) or value <= 0:
                raise ValueError("timeout_seconds must be positive, null or inherit")
        return value

    @field_validator("group", mode="before")
    @classmethod
    def parse_group(cls, value):
        return RoleGroup(value)

    @field_validator("tools")
    @classmethod
    def unique_tools(cls, value):
        if len(set(value)) != len(value):
            raise ValueError("tools contains duplicates")
        return value

    @property
    def name(self):
        return self.role

    @property
    def prompt_filename(self):
        return PurePosixPath(self.prompt).name

    @property
    def output_format(self):
        """Derived runtime format; YAML has no second format declaration."""
        return OutputFormat.UNIFIED_DIFF if self.id == "coder" else OutputFormat.JSON


class ToolCatalog(_Strict):
    tools: list[ToolSpec]

    @model_validator(mode="after")
    def unique_names(self):
        names = [tool.name for tool in self.tools]
        if len(set(names)) != len(names):
            raise ValueError("duplicate tool names")
        return self


@dataclass(frozen=True)
class LoadedRole:
    spec: RoleSpec
    system_prompt: str
    prompt_hash: str


class Registry(_Strict):
    version: str
    tools: dict[str, ToolSpec]
    roles: dict[str, RoleSpec]
    prompts: dict[str, str]

    def get_role(self, role_id: str) -> RoleSpec:
        try:
            return self.roles[role_id]
        except KeyError:
            raise KeyError(f"unknown role '{role_id}' (known: {', '.join(sorted(self.roles))})") from None

    def get(self, name: str) -> LoadedRole:
        """Resolve an ID or display name for the MAS adapter."""
        spec = self.roles.get(name)
        if spec is None:
            spec = next((item for item in self.roles.values() if item.role == name), None)
        if spec is None:
            raise ValueError(f"Unknown Registry role: {name!r}")
        prompt = self.prompts[spec.id]
        return LoadedRole(spec, prompt, hashlib.sha256(prompt.encode("utf-8")).hexdigest())

    def roles_in_group(self, group: RoleGroup):
        return [role for role in self.roles.values() if role.group == group]

    def snapshot(self):
        return {"version": self.version, "roles": {
            role_id: {**self.roles[role_id].model_dump(mode="json"),
                      "prompt_hash": self.get(role_id).prompt_hash}
            for role_id in sorted(self.roles)
        }}
