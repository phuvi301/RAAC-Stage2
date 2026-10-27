import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from raac.benchmark.chain_runner import run_chain
from raac.benchmark.dataset import Task
from raac.llm.config import ModelProfile, load_llm_config
from raac.registry import load_registry

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_chain_adapter_preserves_usage_and_writes_partial_on_cancel(tmp_path, monkeypatch, cancel):
    import raac.benchmark.chain_runner as module

    registry = load_registry(ROOT / "registry")
    config = load_llm_config(ROOT / "configs/llm.yaml")
    config.profiles = {r.model.ref: ModelProfile(model="mock") for r in registry.roles.values()}
    workspace = SimpleNamespace(root="/workspace", baseline_ref="baseline", executor=SimpleNamespace(trace=None, policy=SimpleNamespace(timeout_seconds=200.0)),
        requirement="Fix value",
        execution_mode="docker", environment=SimpleNamespace(configuration=None))

    class Graph:
        async def ainvoke(self, state, options):
            if cancel:
                client.usage.record_attempt({"run_id": "benchmark-fixture", "agent_id": "coder", "level": "L3"})
                client.usage.add({"run_id": "benchmark-fixture", "agent_id": "coder", "level": "L3"}, None, count_call=False)
                raise asyncio.CancelledError
            return {**state, "result": {"status": "rejected", "verdict": "REJECT", "stop_reason": "max_rounds_reached"}}

    original = module.LLMClient

    def make_client(*args, **kwargs):
        nonlocal client
        client = original(*args, **kwargs)
        return client

    client = None
    monkeypatch.setattr(module, "LLMClient", make_client)
    monkeypatch.setattr(module, "build_collaboration_graph", lambda *args, **kwargs: Graph())
    kwargs = {"run_id": "benchmark-fixture", "workspace": workspace, "registry": registry,
              "llm_config": config, "harness": yaml.safe_load((ROOT / "configs/harness.yaml").read_text()),
              "level_config": yaml.safe_load((ROOT / "configs/levels/l3_collaboration.yaml").read_text()),
              "output_dir": tmp_path}
    task = Task("fixture__repo-1", "fixture/repo", "https://github.com/fixture/repo", "Fix value", "Fix value", "a" * 40)
    if cancel:
        with pytest.raises(asyncio.CancelledError):
            await run_chain(task, **kwargs)
        result = json.loads((tmp_path / "agent-result.json").read_text())
        assert result["status"] == "interrupted" and not result["usage"]["usage_complete"]
        assert result["usage"]["calls"] == 1
    else:
        result = await run_chain(task, **kwargs)
        assert result["status"] == "rejected"
        assert result["provenance"]["registry_version"] == registry.version
