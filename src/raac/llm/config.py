"""Central 9router settings; secrets come from the process environment only."""

import os
from pathlib import Path
from typing import Annotated
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, SecretStr, StringConstraints, model_validator
import yaml


ProfileRef = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*$")]
ModelID = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class ModelProfile(BaseModel):
    """Per-role request overrides; endpoint/auth/retry stay on the shared client."""
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    model: ModelID
    temperature: float | None = Field(default=None, ge=0, le=2, allow_inf_nan=False)
    max_tokens: int | None = Field(default=None, ge=1)


class ResolvedModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    model: ModelID
    temperature: float = Field(ge=0, le=2, allow_inf_nan=False)
    max_tokens: int = Field(ge=1)


class LLMConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    version: str = "llm-v3"
    base_url: str = "http://127.0.0.1:20128/v1"
    model: str = ""
    temperature: float = Field(default=0.0, ge=0, le=2)
    max_tokens: int = Field(default=4096, ge=1)
    timeout_seconds: float = Field(default=60.0, gt=0)
    max_retries: int = Field(default=2, ge=0, le=5)
    backoff_seconds: float = Field(default=0.5, ge=0, le=10)
    profiles: dict[ProfileRef, ModelProfile] = Field(default_factory=dict)
    api_key: SecretStr = Field(default=SecretStr(""), exclude=True, repr=False)

    @model_validator(mode="after")
    def valid_endpoint(self):
        if "default" in self.profiles:
            raise ValueError("default is reserved; configure it with top-level model/temperature/max_tokens")
        url = urlsplit(self.base_url)
        if url.scheme not in ("http", "https") or not url.hostname:
            raise ValueError("LLM_BASE_URL must be an HTTP(S) API base URL")
        if url.username or url.password or url.query or url.fragment:
            raise ValueError("Credentials/query parameters are not allowed in the API URL")
        self.base_url = self.base_url.rstrip("/")
        return self


    def resolve_model(self, ref: str = "default") -> ResolvedModel:
        data = {"model": self.model, "temperature": self.temperature, "max_tokens": self.max_tokens}
        if ref != "default":
            profile = self.profiles.get(ref)
            if profile is None:
                raise ValueError(f"Unknown model profile '{ref}' in configs/llm.yaml")
            data.update(profile.model_dump(exclude_none=True))
        if not data["model"].strip():
            raise ValueError("Fill LLM_MODEL in .env or pass --model for roles using model.ref: default")
        return ResolvedModel.model_validate(data)

    def resolve_role_models(self, refs: dict[str, str]) -> dict[str, ResolvedModel]:
        """Preflight every declared role before workspace access or model calls."""
        resolved = {}
        errors = []
        for role_id, ref in refs.items():
            try:
                resolved[role_id] = self.resolve_model(ref)
            except ValueError as exc:
                errors.append(f"{role_id}: {exc}")
        if errors:
            raise ValueError("Invalid role models: " + "; ".join(errors))
        return resolved


def load_dotenv(path: Path) -> None:
    """Load simple KEY=VALUE lines without expansion or overriding the environment."""
    if not path.exists():
        return
    allowed = {"LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL"}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() in allowed:
            os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def load_llm_config(path: Path, *, model=None, base_url=None) -> LLMConfig:
    data = yaml.safe_load(path.read_text(encoding="utf-8-sig")) or {}
    if "api_key" in data:
        raise ValueError("Set LLM_API_KEY in .env/environment, never in YAML")
    data["model"] = model or os.getenv("LLM_MODEL") or data.get("model", "")
    data["base_url"] = base_url or os.getenv("LLM_BASE_URL") or data.get(
        "base_url", "http://127.0.0.1:20128/v1",
    )
    data["api_key"] = SecretStr(os.getenv("LLM_API_KEY", ""))
    return LLMConfig.model_validate(data)
