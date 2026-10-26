"""Regression checks for redundant persistence and cached checkpoint metadata."""

import asyncio
import json
from copy import deepcopy
from itertools import pairwise
from unittest.mock import Mock

import pytest
from test_lifecycle_loop import FINAL, make_runner, request, tool


@pytest.mark.asyncio
async def test_reason_outcome_is_not_checkpointed_twice(tmp_path, monkeypatch):
    runner, client, _ = make_runner(tmp_path, [{"tool_calls": [tool()]}, {"content": json.dumps(FINAL)}])
    saved = []
    original = runner._save

    def record(state):
        original(state)
        value = deepcopy(state)
        value.pop("revision")
        saved.append(value)

    monkeypatch.setattr(runner, "_save", record)
    try:
        await runner("Analyst", request())
        assert all(before != after for before, after in pairwise(saved))
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_checkpoint_workspace_cache_is_opt_in_and_refreshes_on_restore(tmp_path):
    runner, client, _ = make_runner(tmp_path, [])
    workspace = runner.tools.workspace
    workspace.execution_mode = "docker"
    snapshot = Mock(wraps=workspace._snapshot)
    workspace._snapshot = snapshot
    first = workspace.snapshot_runtime(refresh=False)
    assert workspace.snapshot_runtime(refresh=False) == first
    assert snapshot.call_count == 1
    (workspace.root / "value.py").write_text("VALUE = 9\n")
    with pytest.raises(ValueError, match="workspace_mismatch"):
        workspace.restore_runtime(first)
    assert snapshot.call_count == 2
    workspace.invalidate_runtime_snapshot()
    assert workspace.snapshot_runtime(refresh=False)["fingerprint"] != first["fingerprint"]
    assert snapshot.call_count == 3
    await client.aclose()


@pytest.mark.asyncio
async def test_baseline_redaction_cache_tracks_secrets_and_changed_baseline(tmp_path, monkeypatch):
    runner, client, _ = make_runner(tmp_path, [{"content": json.dumps(FINAL)}])
    try:
        await runner("Analyst", request())
        state = runner.checkpoints.latest_run("run-one")
        decoded = []
        import base64

        original = base64.b64decode

        def decode(*args, **kwargs):
            decoded.append(args[0])
            return original(*args, **kwargs)

        monkeypatch.setattr(base64, "b64decode", decode)
        runner._save(state)
        runner._save(state)
        assert decoded == []
        client.trace.secrets = ("VALUE = 1",)
        runner._save(state)
        assert decoded
        persisted = runner.checkpoints.load(state["invocation_id"])
        assert persisted["resumable"] is False
        assert persisted["runtime"]["workspace"]["baseline_files"] == {}
        client.trace.secrets = ()
        runner.tools.workspace.baseline_files["extra.py"] = b"sk-fixture-private-key\n"
        runner._save(state)
        assert runner.checkpoints.load(state["invocation_id"])["runtime"]["workspace"]["baseline_files"] == {}
    finally:
        await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["edit_file", "run_command", "run_tests", "configure_environment", "provision_environment"])
@pytest.mark.parametrize("failed", [None, RuntimeError, asyncio.CancelledError])
async def test_mutating_tool_invalidates_cache_even_when_it_fails(tmp_path, monkeypatch, name, failed):
    runner, client, _ = make_runner(tmp_path, [])
    workspace = runner.tools.workspace
    workspace.execution_mode = "docker"
    first = workspace.snapshot_runtime(refresh=False)

    async def execute(*args, **kwargs):
        (workspace.root / "value.py").write_text("VALUE = 8\n")
        if failed:
            raise failed("fixture failure")
        return {"ok": True, "result": {}}

    monkeypatch.setattr(runner.tools, "_execute", execute)
    try:
        if failed:
            with pytest.raises(failed, match="fixture failure"):
                await runner.tools.execute(runner.registry.get("Coder"), name, {})
        else:
            await runner.tools.execute(runner.registry.get("Coder"), name, {})
        assert workspace.snapshot_runtime(refresh=False)["fingerprint"] != first["fingerprint"]
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_read_only_tool_keeps_checkpoint_cache_and_baseline_cannot_be_poisoned(tmp_path):
    runner, client, _ = make_runner(tmp_path, [])
    workspace = runner.tools.workspace
    workspace.execution_mode = "docker"
    snapshot = Mock(wraps=workspace._snapshot)
    workspace._snapshot = snapshot
    try:
        first = workspace.snapshot_runtime(refresh=False)
        first["baseline_files"]["value.py"] = "poisoned"
        observed = await runner.tools.execute(runner.registry.get("Analyst"), "read_file", {"path": "value.py"})
        assert observed["ok"]
        assert workspace.snapshot_runtime(refresh=False)["baseline_files"]["value.py"] != "poisoned"
        assert snapshot.call_count == 1
        workspace.snapshot_runtime()
        assert snapshot.call_count == 2
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_new_run_rechecks_baseline_even_with_same_workspace_and_secret(tmp_path, monkeypatch):
    import base64

    runner, client, _ = make_runner(tmp_path, [{"content": json.dumps(FINAL)}, {"content": json.dumps(FINAL)}])
    decode = Mock(wraps=base64.b64decode)
    monkeypatch.setattr(base64, "b64decode", decode)
    try:
        await runner("Analyst", request())
        first = decode.call_count
        await runner("Analyst", request() | {"run_id": "run-two"})
        assert decode.call_count > first
        assert client.usage.snapshot(run_id="run-two")["calls"] == 1
    finally:
        await client.aclose()
