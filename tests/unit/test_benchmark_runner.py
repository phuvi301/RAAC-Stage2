import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from raac.benchmark.dataset import load_tasks
from raac.benchmark.run_record import RunRecord
from raac.benchmark.runner import run_task

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.asyncio
async def test_runner_grades_candidate_even_if_agent_rejected(tmp_path, monkeypatch):
    import raac.benchmark.runner as module

    task = next(iter(load_tasks(ROOT / "data/tasks/benchmark105-v1.json").values()))
    removed, evaluated = [], []

    class Session:
        container = SimpleNamespace(id="fixture")

        def __exit__(self, *args):
            removed.append(True)

    class Workspace:
        def export_candidate(self, path):
            path.write_text("candidate fixture")
            return {"path": str(path), "sha256": "b" * 64}

    monkeypatch.setattr(module, "prepare_workspace", lambda *args, **kwargs: (Session(), Workspace(), {"image_id": "fixture"}))

    async def agent(*args, **kwargs):
        return {"status": "rejected", "verdict": "REJECT", "stop_reason": "max_rounds_reached", "usage": {
            "prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12, "calls": 1,
            "usage_complete": True, "cached_tokens": None, "cache_usage_complete": False}, "events": []}

    def evaluate(*args, **kwargs):
        evaluated.append(kwargs)
        return {"status": "resolved", "candidate_validation": {"valid": True}}

    monkeypatch.setattr(module, "run_chain", agent)
    monkeypatch.setattr(module, "evaluate_candidate", evaluate)
    output = tmp_path / "benchmark-fixture"
    result = await run_task(task, level="L3", repetition=1, output_dir=output, config_path=ROOT / "configs/benchmark.yaml")
    assert result.status == "completed" and result.agent_status == "rejected"
    assert result.evaluation_status == "resolved" and result.usage.total_tokens == 12
    assert removed == [True] and len(evaluated) == 1
    assert RunRecord.model_validate_json((output / "record.json").read_text()).evaluation_status == "resolved"


@pytest.mark.asyncio
async def test_setup_failure_produces_record_without_fake_zero_usage(tmp_path, monkeypatch):
    import raac.benchmark.runner as module

    task = next(iter(load_tasks(ROOT / "data/tasks/benchmark105-v1.json").values()))

    def fail(*args, **kwargs):
        raise RuntimeError("private setup detail")

    monkeypatch.setattr(module, "prepare_workspace", fail)
    output = tmp_path / "benchmark-error"
    result = await run_task(task, level="L3", repetition=1, output_dir=output, config_path=ROOT / "configs/benchmark.yaml")
    assert result.status == "error" and result.usage.total_tokens is None
    assert result.errors[0]["phase"] == "setup"
    assert "private setup detail" not in (output / "record.json").read_text()


@pytest.mark.asyncio
async def test_unsupported_level_fails_before_creating_output(tmp_path):
    task = next(iter(load_tasks(ROOT / "data/tasks/benchmark105-v1.json").values()))
    with pytest.raises(ValueError):
        await run_task(task, level="L4", repetition=1, output_dir=tmp_path / "run", config_path=ROOT / "configs/benchmark.yaml")
    assert not (tmp_path / "run").exists()
