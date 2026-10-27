from pathlib import Path

import pytest
import yaml

from raac.benchmark.dataset import load_tasks
from raac.benchmark.run_record import RunRecord, write_record
from raac.benchmark.runner import run_benchmark_batch

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.asyncio
async def test_batch_runs_matrix_once_and_resume_skips_completed(tmp_path, monkeypatch):
    import raac.benchmark.runner as module

    config = yaml.safe_load((ROOT / "configs/benchmark.yaml").read_text())
    config["results"] = str(tmp_path / "results")
    config["dataset"] = str(ROOT / config["dataset"])
    config_path = tmp_path / "benchmark.yaml"
    config_path.write_text(yaml.safe_dump(config))
    ids = list(load_tasks(Path(config["dataset"])))[:2]
    calls = []

    async def run(task, *, output_dir, level, repetition, **kwargs):
        calls.append((task.id, repetition))
        output_dir.mkdir(parents=True)
        record = RunRecord(run_id=output_dir.name, task_id=task.id, level=level,
            base_commit=task.base_commit, repetition=repetition, status="completed", agent_status="rejected",
            evaluation_status="resolved")
        write_record(output_dir / "record.json", record)
        return record

    monkeypatch.setattr(module, "run_task", run)
    kwargs = {"config_path": config_path, "task_ids": ids, "levels": ["L3"], "repetitions": 2, "batch_id": "fixture"}
    result = await run_benchmark_batch(**kwargs)
    assert len(calls) == 4 and result["total"] == 4
    await run_benchmark_batch(**kwargs, resume=True)
    assert len(calls) == 4
    config["agent_run_timeout_seconds"] = 100.0
    config_path.write_text(yaml.safe_dump(config))
    with pytest.raises(ValueError, match="fingerprint"):
        await run_benchmark_batch(**kwargs, resume=True)


@pytest.mark.asyncio
async def test_retry_errors_requires_resume_before_side_effects(tmp_path):
    with pytest.raises(ValueError):
        await run_benchmark_batch(config_path=tmp_path / "missing", task_ids=[], levels=["L3"],
                                  repetitions=1, batch_id="fixture", retry_errors=True)
    assert not list(tmp_path.iterdir())


@pytest.mark.asyncio
async def test_two_batch_writers_cannot_start_the_same_run(tmp_path, monkeypatch):
    import asyncio

    import raac.benchmark.runner as module

    config = yaml.safe_load((ROOT / "configs/benchmark.yaml").read_text())
    config["results"] = str(tmp_path / "results")
    config["dataset"] = str(ROOT / config["dataset"])
    config_path = tmp_path / "benchmark.yaml"
    config_path.write_text(yaml.safe_dump(config))
    task_id = next(iter(load_tasks(Path(config["dataset"]))))
    entered, release = asyncio.Event(), asyncio.Event()

    async def run(task, *, output_dir, level, repetition, **kwargs):
        entered.set()
        await release.wait()
        value = RunRecord(run_id=output_dir.name, task_id=task.id, base_commit=task.base_commit,
            level=level, repetition=repetition, status="completed", evaluation_status="unresolved")
        write_record(output_dir / "record.json", value)
        return value

    monkeypatch.setattr(module, "run_task", run)
    kwargs = {"config_path": config_path, "task_ids": [task_id], "levels": ["L3"], "repetitions": 1, "batch_id": "concurrent"}
    first = asyncio.create_task(run_benchmark_batch(**kwargs))
    try:
        await asyncio.wait_for(entered.wait(), 10)
        with pytest.raises(TimeoutError):
            await run_benchmark_batch(**kwargs, resume=True)
    finally:
        release.set()
        await first


@pytest.mark.asyncio
async def test_missing_second_attempt_record_recovers_its_attempt_number(tmp_path, monkeypatch):
    import json

    import raac.benchmark.runner as module

    config = yaml.safe_load((ROOT / "configs/benchmark.yaml").read_text())
    config["results"] = str(tmp_path / "results")
    config["dataset"] = str(ROOT / config["dataset"])
    config_path = tmp_path / "benchmark.yaml"
    config_path.write_text(yaml.safe_dump(config))
    task_id = next(iter(load_tasks(Path(config["dataset"]))))
    calls = []

    async def run(task, *, output_dir, level, repetition, attempt, **kwargs):
        calls.append(attempt)
        if attempt == 2:
            raise RuntimeError("crash before record")
        value = RunRecord(run_id=output_dir.name, task_id=task.id, base_commit=task.base_commit,
            repetition=repetition, attempt=attempt, level=level, status="error" if attempt == 1 else "completed")
        value.errors.append({"retryable": "true"})
        write_record(output_dir / "record.json", value)
        return value

    monkeypatch.setattr(module, "run_task", run)
    kwargs = {"config_path": config_path, "task_ids": [task_id], "levels": ["L3"], "repetitions": 1, "batch_id": "fixture"}
    await run_benchmark_batch(**kwargs)
    with pytest.raises(RuntimeError, match="crash before record"):
        await run_benchmark_batch(**kwargs, resume=True, retry_errors=True)
    await run_benchmark_batch(**kwargs, resume=True)
    manifest = json.loads((tmp_path / "results/benchmark-batches/fixture/manifest.json").read_text())
    records = [RunRecord.model_validate_json((tmp_path / "results" / run_id / "record.json").read_text())
               for run_id in manifest["entries"][0]["attempts"]]
    assert [record.attempt for record in records] == [1, 2, 3]
    assert calls == [1, 2, 3]


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["error", "model-error", "unresolved", "crash-after-record"])
async def test_retry_and_crash_recovery_do_not_rerun_completed_results(tmp_path, monkeypatch, outcome):
    import raac.benchmark.runner as module

    config = yaml.safe_load((ROOT / "configs/benchmark.yaml").read_text())
    config["results"] = str(tmp_path / "results")
    config["dataset"] = str(ROOT / config["dataset"])
    config_path = tmp_path / "benchmark.yaml"
    config_path.write_text(yaml.safe_dump(config))
    task_id = next(iter(load_tasks(Path(config["dataset"]))))
    calls = []

    async def run(task, *, output_dir, level, repetition, attempt, **kwargs):
        calls.append(attempt)
        output_dir.mkdir(parents=True)
        value = RunRecord(run_id=output_dir.name, task_id=task.id, base_commit=task.base_commit, level=level,
            repetition=repetition, attempt=attempt, status="error" if outcome in {"error", "model-error"} and attempt == 1 else "completed",
            evaluation_status="unresolved" if outcome == "unresolved" else "resolved")
        if outcome == "error":
            value.errors.append({"phase": "setup", "kind": "DockerException", "retryable": "true"})
        write_record(output_dir / "record.json", value)
        if outcome == "crash-after-record" and attempt == 1:
            raise RuntimeError("fixture crash after atomic record")
        return value

    monkeypatch.setattr(module, "run_task", run)
    kwargs = {"config_path": config_path, "task_ids": [task_id], "levels": ["L3"], "repetitions": 1, "batch_id": "fixture"}
    if outcome == "crash-after-record":
        with pytest.raises(RuntimeError):
            await run_benchmark_batch(**kwargs)
    else:
        await run_benchmark_batch(**kwargs)
    await run_benchmark_batch(**kwargs, resume=True)
    assert calls == [1]
    result = await run_benchmark_batch(**kwargs, resume=True, retry_errors=True)
    assert calls == ([1, 2] if outcome == "error" else [1])
    assert result["attempts"] == len(calls)
