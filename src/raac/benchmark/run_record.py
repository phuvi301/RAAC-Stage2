"""Versioned records for one benchmark attempt, independent of the agent verdict."""

from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from raac.common.tracing import JSONLTrace
from raac.harness.layer3_execution.sandbox.batch import atomic_json
from raac.harness.layer3_execution.sandbox.docker_sandbox import safe_snapshot

Count = Annotated[int, Field(ge=0)]
Seconds = Annotated[float, Field(ge=0, allow_inf_nan=False)]


class RecordModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, validate_assignment=True)


class RunUsage(RecordModel):
    prompt_tokens: Count | None = None
    completion_tokens: Count | None = None
    total_tokens: Count | None = None
    cached_tokens: Count | None = None
    calls: Count = 0
    usage_complete: bool = False
    cache_usage_complete: bool = False

    @model_validator(mode="after")
    def consistent_totals(self):
        counts = (self.prompt_tokens, self.completion_tokens, self.total_tokens)
        if self.usage_complete and any(value is None for value in counts):
            raise ValueError("Complete usage requires input, output and total tokens")
        if all(value is not None for value in counts) and counts[0] + counts[1] != counts[2]:
            raise ValueError("Total tokens must equal input plus output; cache is part of input")
        if self.cached_tokens is not None and (
            self.prompt_tokens is None or self.cached_tokens > self.prompt_tokens
        ):
            raise ValueError("Cached tokens must be a subset of input tokens")
        if self.cache_usage_complete and (not self.usage_complete or self.cached_tokens is None):
            raise ValueError("Complete cache usage requires complete token usage")
        return self


class RunTiming(RecordModel):
    setup_seconds: Seconds = 0.0
    agent_seconds: Seconds = 0.0
    evaluation_seconds: Seconds = 0.0
    total_seconds: Seconds = 0.0


class RunRecord(RecordModel):
    schema_version: Literal[1] = 1
    run_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
    task_id: str = Field(min_length=1)
    level: Literal["L1", "L2", "L3"]
    repetition: int = Field(default=1, ge=1)
    attempt: int = Field(default=1, ge=1)
    base_commit: str = Field(pattern=r"^[0-9a-fA-F]{40}$")
    phase: Literal["pending", "setup", "agent", "export", "evaluation", "cleanup", "finished"] = "pending"
    status: Literal["pending", "running", "completed", "error", "interrupted", "timeout"] = "pending"
    agent_status: str = "pending"
    evaluation_status: str = "pending"
    verdict: str | None = None
    stop_reason: str | None = None
    patch_path: str | None = None
    patch_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    evaluation: dict[str, Any] = Field(default_factory=dict)
    usage: RunUsage = Field(default_factory=RunUsage)
    timing: RunTiming = Field(default_factory=RunTiming)
    agent_activations: dict[str, Count] = Field(default_factory=dict)
    messages: Count = 0
    rounds: Count = 0
    errors: list[dict[str, str]] = Field(default_factory=list)
    provenance: dict[str, Any] = Field(default_factory=dict)
    trace: dict[str, Any] = Field(default_factory=dict)


def write_record(path: Path, record: RunRecord) -> None:
    validated = RunRecord.model_validate(record.model_dump())
    data = JSONLTrace(path).redact(safe_snapshot(validated.model_dump(mode="json")))
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_json(path, data)
