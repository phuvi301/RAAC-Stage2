"""Resolve and validate benchmark harness selection before runtime setup."""

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

from raac.harness.layer2_core.context_manager import RoleContextPolicy
from raac.harness.layer2_core.memory.short_term import ShortTermMemoryPolicy
from raac.harness.layer3_execution.local_executor import LocalExecutionPolicy


class HarnessConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    version: str
    trace_content: bool
    token_limit: int | None = Field(gt=0)
    timeout_seconds: float = Field(gt=0)
    agent_timeout_seconds: float = Field(gt=0)
    context: RoleContextPolicy = Field(default_factory=RoleContextPolicy)
    memory: ShortTermMemoryPolicy = Field(default_factory=ShortTermMemoryPolicy)
    execution: LocalExecutionPolicy


def load_harness(root, config):
    selected = config.get("harness_config")
    if selected is None or selected == "":
        selected = "configs/harness.yaml"
    if not isinstance(selected, str):
        raise TypeError("harness_config must be a file path")
    path = Path(selected)
    path = (root / path).resolve() if not path.is_absolute() else path.resolve()
    harness = yaml.safe_load(path.read_text(encoding="utf-8-sig"))
    HarnessConfig.model_validate(harness)
    return path, harness
