"""Adapter: the flow's JsonModel port implemented with the RAAC LLM client.

Reuses, without changing them: RAAC's ``.env`` (model key/base URL), ``configs/llm.yaml`` (models, retries,
timeouts) and ``raac.llm.client.LLMClient`` (9router calls, usage tracking, secret redaction, JSONL trace).
"""

from __future__ import annotations

import uuid
from pathlib import Path

from swflow import __version__

RAAC_ROOT = Path(__file__).resolve().parents[2]  # software-flow/ lives inside the RAAC repository


class RaacModel:
    def __init__(self, *, log_path: Path, model: str | None = None, base_url: str | None = None,
                 raac_root: Path = RAAC_ROOT, trace_content: bool = True):
        try:
            from raac.llm.client import LLMClient
            from raac.llm.config import load_dotenv, load_llm_config
        except ImportError as error:  # pragma: no cover - depends on the installation
            raise RuntimeError("raac is not importable; run `python -m pip install -e .` in the RAAC root") from error
        load_dotenv(raac_root / ".env")
        config = load_llm_config(raac_root / "configs" / "llm.yaml", model=model, base_url=base_url)
        self.client = LLMClient(config, log_path=log_path, trace_content=trace_content)
        self.run_id = "flow-" + uuid.uuid4().hex

    async def complete_json(self, *, phase: str, system: str, user: str) -> str:
        context = {
            "run_id": self.run_id, "task_id": self.run_id, "level": "flow", "agent_id": f"flow-{phase}",
            "registry_version": f"software-flow-{__version__}", "prompt_version": f"{phase}-v1",
            "provenance": {"tool": "software-flow", "phase": phase},
        }
        completion = await self.client.complete(
            [{"role": "system", "content": system}, {"role": "user", "content": user}], context=context)
        return completion.message.get("content") or ""

    def usage(self) -> dict:
        return self.client.usage.snapshot(run_id=self.run_id)

    async def aclose(self) -> None:
        await self.client.aclose()
