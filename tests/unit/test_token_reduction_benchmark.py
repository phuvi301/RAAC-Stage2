"""Selected harness reaches every level and participates in batch resume identity."""

import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from raac.benchmark.dataset import load_tasks
from raac.benchmark.run_record import RunRecord, write_record
from raac.benchmark.runner import run_benchmark_batch, run_task

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.asyncio
@pytest.mark.parametrize("level", ["L1", "L2", "L3"])
async def test_selected_harness_reaches_level_and_provenance(tmp_path, monkeypatch, level):
    import raac.benchmark.runner as module

    task = next(iter(load_tasks(ROOT / "data/tasks/benchmark105-v1.json").values()))
    harness_path = tmp_path / "harness.yaml"
    harness = yaml.safe_load((ROOT / "configs/experiments/token_reduction/concise.yaml").read_text())
    harness_path.write_text(yaml.safe_dump(harness))
    config = yaml.safe_load((ROOT / "configs/benchmark.yaml").read_text())
    config["harness_config"] = str(harness_path)
    config_path = tmp_path / "benchmark.yaml"
    config_path.write_text(yaml.safe_dump(config))
    class Workspace:
        def export_candidate(self, path):
            path.write_text("fixture")
            return {"path": str(path), "sha256": "a" * 64}
    session = SimpleNamespace(container=SimpleNamespace(id="fixture"), __exit__=lambda *args: None)
    monkeypatch.setattr(module, "prepare_workspace", lambda *args: (session, Workspace(), {"image_id": "fixture"}))
    async def agent(*args, **kwargs):
        assert kwargs["harness_path"] == harness_path
        assert kwargs["harness"] == harness
        return {"status": "completed", "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
                "calls": 0, "usage_complete": True, "cached_tokens": None, "cache_usage_complete": False}}
    monkeypatch.setattr(module, {"L1": "run_l1", "L2": "run_l2", "L3": "run_chain"}[level], agent)
    monkeypatch.setattr(module, "evaluate_candidate", lambda *args, **kwargs: {"status": "resolved"})
    result = await run_task(task, level=level, repetition=1, output_dir=tmp_path / "run", config_path=config_path)
    assert result.status == "completed"
    configs = result.provenance["configs"]
    assert "configs/harness.yaml" not in configs
    assert configs[harness_path.as_posix()]["sha256"] == hashlib.sha256(harness_path.read_bytes()).hexdigest()
    assert result.provenance["effective_config"]["harness"] == harness
    harness["context"]["concise_output"] = "yes"
    harness_path.write_text(yaml.safe_dump(harness))
    with pytest.raises(ValueError):
        await run_task(task, level=level, repetition=1, output_dir=tmp_path / "invalid", config_path=config_path)
    assert not (tmp_path / "invalid").exists()


@pytest.mark.asyncio
async def test_external_harness_change_rejects_batch_resume(tmp_path, monkeypatch):
    import raac.benchmark.runner as module

    config = yaml.safe_load((ROOT / "configs/benchmark.yaml").read_text())
    config["results"] = str(tmp_path / "results")
    harness_path = tmp_path / "external.yaml"
    harness = yaml.safe_load((ROOT / "configs/harness.yaml").read_text())
    harness_path.write_text(yaml.safe_dump(harness))
    config["harness_config"] = str(harness_path)
    config_path = tmp_path / "benchmark.yaml"
    config_path.write_text(yaml.safe_dump(config))
    task_id = next(iter(load_tasks(ROOT / config["dataset"])))
    calls = []
    async def run(task, *, output_dir, level, repetition, **kwargs):
        calls.append(task.id)
        output_dir.mkdir(parents=True)
        record = RunRecord(run_id=output_dir.name, task_id=task.id, level=level, base_commit=task.base_commit,
                           repetition=repetition, status="completed", evaluation_status="resolved")
        write_record(output_dir / "record.json", record)
        return record
    monkeypatch.setattr(module, "run_task", run)
    kwargs = {"config_path": config_path, "task_ids": [task_id], "levels": ["L1", "L2", "L3"], "repetitions": 1, "batch_id": "policies"}
    await run_benchmark_batch(**kwargs)
    await run_benchmark_batch(**kwargs, resume=True)
    assert len(calls) == 3
    harness["context"]["concise_output"] = True
    harness_path.write_text(yaml.safe_dump(harness))
    with pytest.raises(ValueError, match="fingerprint"):
        await run_benchmark_batch(**kwargs, resume=True)
    assert len(calls) == 3
