import asyncio
import io
import tarfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from raac.benchmark.dataset import load_tasks
from raac.benchmark.run_record import RunRecord
from raac.benchmark.runner import run_task

ROOT = Path(__file__).resolve().parents[2]


def test_preparation_passes_archive_file_path(tmp_path, monkeypatch):
    import raac.benchmark.runner as module

    task = next(iter(load_tasks(ROOT / "data/tasks/benchmark105-v1.json").values()))
    client = SimpleNamespace(close=lambda: None)
    sandbox = SimpleNamespace(root=tmp_path, config={"repo_cache": "repos"}, client=client,
        run=lambda *args, **kwargs: {"status": "prepared", "image_id": "fixture"})
    session = SimpleNamespace(client=client, __enter__=lambda: None, __exit__=lambda *args: None,
        container=SimpleNamespace(exec_run=lambda *args, **kwargs: SimpleNamespace(exit_code=0)))
    monkeypatch.setattr(module, "DockerSandbox", lambda **kwargs: sandbox)
    monkeypatch.setattr(module, "DockerRunWorkspace", lambda *args, **kwargs: session)
    monkeypatch.setattr(module, "DockerWorkspaceTools", lambda *args: SimpleNamespace(
        max_file_bytes=4194304, baseline_files={"value.py": b"1"}))

    def archive(task, cache, destination, **kwargs):
        assert destination.name == "source.tar"
        with tarfile.open(destination, "w") as stream:
            info = tarfile.TarInfo("value.py")
            info.size = 1
            stream.addfile(info, io.BytesIO(b"1"))

    monkeypatch.setattr(module, "archive_repo", archive)
    module.prepare_workspace(task, ROOT / "configs/benchmark.yaml", tmp_path / "run",
        module.DockerExecutionPolicy(), threading.Event(), time.monotonic() + 60)


@pytest.mark.asyncio
@pytest.mark.parametrize("agent_status,evaluation_status", [
    ("rejected", "resolved"), ("aborted", "unresolved"),
    ("completed", "protected_test_error"), ("completed", "timeout"),
])
async def test_runner_grades_candidate_even_if_agent_rejected(tmp_path, monkeypatch, agent_status, evaluation_status):
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
        return {"status": agent_status, "verdict": "REJECT", "stop_reason": "agent_error" if agent_status == "aborted" else "max_rounds_reached", "usage": {
            "prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12, "calls": 1,
            "usage_complete": True, "cached_tokens": None, "cache_usage_complete": False}, "events": []}

    def evaluate(*args, **kwargs):
        evaluated.append(kwargs)
        return {"status": evaluation_status, "candidate_validation": {"valid": evaluation_status == "resolved"}}

    monkeypatch.setattr(module, "run_chain", agent)
    monkeypatch.setattr(module, "evaluate_candidate", evaluate)
    output = tmp_path / "benchmark-fixture"
    result = await run_task(task, level="L3", repetition=1, output_dir=output, config_path=ROOT / "configs/benchmark.yaml")
    expected_error = agent_status == "aborted" or evaluation_status not in {"resolved", "unresolved"}
    assert result.status == ("error" if expected_error else "completed") and result.agent_status == agent_status
    assert result.evaluation_status == evaluation_status and result.usage.total_tokens == 12
    if expected_error:
        assert result.errors and all(error.get("retryable") == "false" for error in result.errors)
    assert removed == [True] and len(evaluated) == 1
    assert RunRecord.model_validate_json((output / "record.json").read_text()).evaluation_status == evaluation_status


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


@pytest.mark.asyncio
async def test_cancellation_after_preparation_completes_still_cleans_container(tmp_path, monkeypatch):
    import raac.benchmark.runner as module

    task = next(iter(load_tasks(ROOT / "data/tasks/benchmark105-v1.json").values()))
    removed = []
    session = SimpleNamespace(__exit__=lambda *args: removed.append(True))
    monkeypatch.setattr(module, "prepare_workspace", lambda *args: (session, object(), {}))

    async def cancel_after_done(worker):
        await worker
        raise asyncio.CancelledError

    monkeypatch.setattr(module.asyncio, "shield", cancel_after_done)
    output = tmp_path / "benchmark-cancel"
    with pytest.raises(asyncio.CancelledError):
        await run_task(task, level="L3", repetition=1, output_dir=output, config_path=ROOT / "configs/benchmark.yaml")
    assert removed == [True]
    assert RunRecord.model_validate_json((output / "record.json").read_text()).status == "interrupted"
