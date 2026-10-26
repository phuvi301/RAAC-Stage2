import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from raac.benchmark.dataset import Task
from raac.benchmark.l1_runner import run_l1
from raac.llm.config import ModelProfile, load_llm_config
from raac.registry import load_registry

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_l1_adapter_preserves_usage_and_writes_partial_on_cancel(tmp_path, monkeypatch, cancel):
    import raac.benchmark.l1_runner as module

    registry = load_registry(ROOT / "registry")
    config = load_llm_config(ROOT / "configs/llm.yaml")
    config.profiles = {r.model.ref: ModelProfile(model="mock") for r in registry.roles.values()}
    workspace = SimpleNamespace(root="/workspace", baseline_ref="baseline", requirement="Fix value", test_spec=None,
        python_candidates=lambda: ["/usr/local/bin/python"],
        executor=SimpleNamespace(trace=None, policy=SimpleNamespace(timeout_seconds=200.0)))
    client = None
    original = module.LLMClient

    def make_client(*args, **kwargs):
        nonlocal client
        client = original(*args, **kwargs)
        return client

    class FakeEngine:
        events = ({"event": "agent_started", "agent_id": "analyst"},)

        async def run(self, *, run_id, task, level, config):
            assert level.name == "L1" and config["version"] == "l1-fixed-v1"
            assert task["requirement"] == "Fix value"
            if cancel:
                client.usage.record_attempt({"run_id": run_id, "agent_id": "coder", "level": "L1"})
                client.usage.add({"run_id": run_id, "agent_id": "coder", "level": "L1"}, None, count_call=False)
                raise asyncio.CancelledError
            return SimpleNamespace(state={"status": "rejected", "result": {
                "status": "rejected", "verdict": "REJECT", "stop_reason": "reviewer_rejected", "rounds_completed": 1}})

    monkeypatch.setattr(module, "LLMClient", make_client)
    monkeypatch.setattr(module.Engine, "from_runtime", classmethod(lambda cls, **kwargs: FakeEngine()))
    kwargs = {"run_id": "benchmark-fixture", "workspace": workspace, "registry": registry, "llm_config": config,
              "harness": yaml.safe_load((ROOT / "configs/harness.yaml").read_text()),
              "level_config": yaml.safe_load((ROOT / "configs/levels/l1_fixed.yaml").read_text()),
              "output_dir": tmp_path}
    task = Task("fixture__repo-1", "fixture/repo", "https://github.com/fixture/repo", "Fix value", "Fix value", "a" * 40)
    if cancel:
        with pytest.raises(asyncio.CancelledError):
            await run_l1(task, **kwargs)
        result = json.loads((tmp_path / "agent-result.json").read_text())
        assert result["status"] == "interrupted" and not result["usage"]["usage_complete"]
        assert result["usage"]["calls"] == 1
    else:
        result = await run_l1(task, **kwargs)
        assert result["status"] == "rejected" and result["rounds_completed"] == 1
        assert result["provenance"]["registry_version"] == registry.version
        assert "configs/levels/l1_fixed.yaml" in result["provenance"]["configs"]
        assert result["events"] == list(FakeEngine.events)
    assert workspace.test_spec["command"] == ["/usr/local/bin/python", "-m", "pytest"]


@pytest.mark.asyncio
async def test_l1_adapter_maps_engine_failure_to_aborted(tmp_path, monkeypatch):
    import raac.benchmark.l1_runner as module

    registry = load_registry(ROOT / "registry")
    config = load_llm_config(ROOT / "configs/llm.yaml")
    config.profiles = {r.model.ref: ModelProfile(model="mock") for r in registry.roles.values()}
    workspace = SimpleNamespace(root="/workspace", baseline_ref="baseline", requirement="Fix value",
        test_spec={"command": ["python", "-m", "pytest", "tests"], "cwd": ".", "env": {}},
        python_candidates=lambda: ["/usr/bin/python"],
        executor=SimpleNamespace(trace=None, policy=SimpleNamespace(timeout_seconds=200.0)))

    class FakeEngine:
        events = ()

        async def run(self, **kwargs):
            return SimpleNamespace(state={"status": "failed", "error": {"agent_id": "coder", "kind": "TimeoutError"},
                "result": {"status": "aborted", "verdict": None, "stop_reason": "TimeoutError", "rounds_completed": 1}})

    monkeypatch.setattr(module.Engine, "from_runtime", classmethod(lambda cls, **kwargs: FakeEngine()))
    task = Task("fixture__repo-1", "fixture/repo", "https://github.com/fixture/repo", "Fix value", "Fix value", "a" * 40)
    result = await run_l1(task, run_id="benchmark-fixture", workspace=workspace, registry=registry, llm_config=config,
        harness=yaml.safe_load((ROOT / "configs/harness.yaml").read_text()),
        level_config=yaml.safe_load((ROOT / "configs/levels/l1_fixed.yaml").read_text()), output_dir=tmp_path)
    assert result["status"] == "aborted" and result["stop_reason"] == "TimeoutError"
    # A profile-selected test command is preserved rather than replaced.
    assert workspace.test_spec["command"] == ["python", "-m", "pytest", "tests"]
