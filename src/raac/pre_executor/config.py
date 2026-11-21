"""Cấu hình pre_executor, đọc từ configs/pre_executor.yaml."""

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

# Flow -> id role trong registry/roles/pre_executor/ chạy flow đó.
FLOW_ROLES = {
    "clarify": "clarifier",
    "spec": "business_analyst",
    "tech_select": "tech_selector",
    "architecture": "system_architect",
    "environment": "environment_planner",
    "task_decomposer": "task_decomposer",
}


class PreExecutorConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str = "pre-executor-v1"
    max_flow_retries: int = Field(default=2, ge=0)  # số lần cho role chạy lại khi đầu ra vi phạm luật
    max_clarify_rounds: int = Field(default=3, ge=1)
    models: dict[str, str] = Field(default_factory=dict)  # id role -> model; "" = model mặc định của llm.yaml

    @field_validator("models")
    @classmethod
    def known_roles(cls, value: dict[str, str]) -> dict[str, str]:
        unknown = sorted(set(value) - set(FLOW_ROLES.values()))
        if unknown:
            raise ValueError(f"models: khóa phải là id role {sorted(FLOW_ROLES.values())}, không nhận {unknown}")
        return value

    def model_for(self, flow: str, override: str | None = None) -> str | None:
        """Model cho role chạy flow này: --model > models[id role] > model mặc định của llm.yaml (None)."""
        return override or self.models.get(FLOW_ROLES.get(flow, flow)) or None


def load_config(path: Path) -> PreExecutorConfig:
    path = Path(path)
    if not path.is_file():
        return PreExecutorConfig()
    return PreExecutorConfig.model_validate(yaml.safe_load(path.read_text(encoding="utf-8-sig")) or {})
