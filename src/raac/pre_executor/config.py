"""Cấu hình pre_executor, đọc từ configs/pre_executor.yaml."""

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field


class PreExecutorConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str = "pre-executor-v1"
    max_flow_retries: int = Field(default=2, ge=0)  # số lần cho role chạy lại khi đầu ra vi phạm luật
    max_clarify_rounds: int = Field(default=3, ge=1)
    models: dict[str, str] = Field(default_factory=dict)  # flow -> model; "" = model mặc định của llm.yaml

    def model_for(self, flow: str, override: str | None = None) -> str | None:
        return override or self.models.get(flow) or None


def load_config(path: Path) -> PreExecutorConfig:
    path = Path(path)
    if not path.is_file():
        return PreExecutorConfig()
    return PreExecutorConfig.model_validate(yaml.safe_load(path.read_text(encoding="utf-8-sig")) or {})
