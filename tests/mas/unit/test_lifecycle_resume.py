import asyncio
import json
from copy import deepcopy
import pytest
from test_lifecycle_loop import make_runner, request, tool, FINAL
from raac.harness.layer1_lifecycle.approval import AgentPaused, ApprovalDecision
from raac.harness.layer1_lifecycle.checkpoint import LifecycleError


def approve(runner, pause, allow=True):
    state = runner.checkpoints.load(pause.invocation_id)
    op = state["pending_approval"]
    return ApprovalDecision(
        pause.invocation_id, pause.revision, op["id"], op["arguments_hash"], allow
    )


@pytest.mark.asyncio
async def test_manual_resume_with_new_runner_preserves_usage_and_reads_once(tmp_path):
    runner, client, _ = make_runner(
        tmp_path, [{"tool_calls": [tool()]}], auto_approve=False
    )
    try:
        with pytest.raises(AgentPaused) as paused:
            await runner("Analyst", request())
        decision = approve(runner, paused.value)
        assert hasattr(runner, "resume"), "Resume API missing"
        resumed, new_client, seen = make_runner(
            tmp_path, [{"content": json.dumps(FINAL)}], auto_approve=False
        )
        try:
            result = await resumed.resume(paused.value.invocation_id, approval=decision)
            assert result["summary"] == "Observed source"
            assert new_client.usage.snapshot(run_id="run-one")["total_tokens"] == 24
            assert len([m for m in seen[0]["messages"] if m["role"] == "tool"]) == 1
            assert await resumed.resume(paused.value.invocation_id) == result
            assert new_client.usage.snapshot(run_id="run-one")["calls"] == 2
            rows = [
                json.loads(line)
                for line in new_client.log_path.read_text().splitlines()
            ]
            assert [e["sequence"] for e in rows] == list(range(1, len(rows) + 1))
        finally:
            await new_client.aclose()
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_approval_mismatch_does_not_consume_and_rejection_stops(tmp_path):
    runner, client, _ = make_runner(
        tmp_path, [{"tool_calls": [tool()]}], auto_approve=False
    )
    try:
        with pytest.raises(AgentPaused) as paused:
            await runner("Analyst", request())
        good = approve(runner, paused.value)
        bad = ApprovalDecision(
            good.invocation_id,
            good.revision - 1,
            good.operation_id,
            good.arguments_hash,
            True,
        )
        assert hasattr(runner, "resume")
        with pytest.raises(LifecycleError, match="approval_mismatch"):
            await runner.resume(good.invocation_id, approval=bad)
        with pytest.raises(LifecycleError, match="approval_rejected"):
            await runner.resume(
                good.invocation_id, approval=approve(runner, paused.value, False)
            )
        assert runner.checkpoints.load(good.invocation_id)["status"] == "FAILED"
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_resume_rejects_workspace_change_and_unknown_inflight(tmp_path):
    runner, client, _ = make_runner(
        tmp_path, [{"tool_calls": [tool()]}], auto_approve=False
    )
    try:
        with pytest.raises(AgentPaused) as paused:
            await runner("Analyst", request())
        assert hasattr(runner, "resume")
        saved = runner.checkpoints.load(paused.value.invocation_id)
        (runner.tools.workspace.root / "value.py").write_text("CHANGED\n")
        with pytest.raises(ValueError, match="workspace_mismatch"):
            await runner.resume(paused.value.invocation_id)
        (runner.tools.workspace.root / "value.py").write_text("VALUE = 1\n", newline="")
        saved["inflight"] = {"kind": "tool"}
        runner.checkpoints.save(saved)
        with pytest.raises(LifecycleError, match="resume_unsafe_inflight"):
            await runner.resume(paused.value.invocation_id)
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_resume_after_committed_observation_does_not_repeat_tool(
    tmp_path, monkeypatch
):
    runner, client, _ = make_runner(tmp_path, [{"tool_calls": [tool()]}])
    original = runner._observe if hasattr(runner, "_observe") else None
    assert original is not None

    class Crash(BaseException):
        pass

    async def crash(state):
        await original(state)
        runner._save(state)
        raise Crash()

    monkeypatch.setattr(runner, "_observe", crash)
    try:
        with pytest.raises(Crash):
            await runner("Analyst", request())
        rows = [json.loads(line) for line in client.log_path.read_text().splitlines()]
        identifier = next(
            e["agent_call_id"] for e in rows if e["event"] == "agent_started"
        )
        assert hasattr(runner, "resume")
        new, other, seen = make_runner(tmp_path, [{"content": json.dumps(FINAL)}])
        try:
            result = await new.resume(identifier)
            assert result["summary"] == "Observed source"
            after = [
                json.loads(line) for line in other.log_path.read_text().splitlines()
            ]
            assert (
                sum(
                    e["event"] == "lifecycle_transition"
                    and e.get("to_state") == "EXECUTING_TOOL"
                    for e in after
                )
                == 1
            )
            assert other.usage.snapshot(run_id="run-one")["calls"] == 2
        finally:
            await other.aclose()
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_new_activation_does_not_reset_run_budget(tmp_path):
    first, client, _ = make_runner(tmp_path, [{"content": json.dumps(FINAL)}])
    try:
        await first("Analyst", request())
    finally:
        await client.aclose()
    second, new_client, _ = make_runner(tmp_path, [{"content": json.dumps(FINAL)}])
    try:
        await second("Analyst", request())
        assert new_client.usage.snapshot(run_id="run-one")["calls"] == 2
        assert new_client.usage.snapshot(run_id="run-one")["total_tokens"] == 24
    finally:
        await new_client.aclose()


@pytest.mark.asyncio
async def test_resume_never_resets_budget_or_deadline(tmp_path):
    runner, client, _ = make_runner(
        tmp_path, [{"tool_calls": [tool()]}], auto_approve=False
    )
    try:
        with pytest.raises(AgentPaused) as paused:
            await runner("Analyst", request())
        saved = runner.checkpoints.load(paused.value.invocation_id)
        saved["deadline_at"] = 0
        runner.checkpoints.save(saved)
        with pytest.raises(TimeoutError):
            await runner.resume(paused.value.invocation_id)
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_exclusive_lock_blocks_concurrent_work(tmp_path):
    runner, client, _ = make_runner(tmp_path, [{"content": json.dumps(FINAL)}])
    try:
        with runner.checkpoints.lock("run-one"):
            with pytest.raises(LifecycleError, match="lifecycle_busy"):
                await runner("Analyst", request())
        assert client.usage.snapshot(run_id="run-one")["calls"] == 0
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_checkpoint_secret_redaction_blocks_resume(tmp_path):
    runner, client, _ = make_runner(
        tmp_path, [{"tool_calls": [tool()]}], auto_approve=False
    )
    client.trace.secrets = ("fixture-private-value",)
    req = request()
    req["private_note"] = "fixture-private-value"
    try:
        with pytest.raises(AgentPaused) as paused:
            await runner("Analyst", req)
        data = runner.checkpoints.load(paused.value.invocation_id)
        assert "fixture-private-value" not in json.dumps(data)
        assert data["resumable"] is False
        with pytest.raises(LifecycleError, match="resume_redacted_payload"):
            await runner.resume(paused.value.invocation_id)
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_manual_approval_applies_to_adapter_patch_and_restores_coder_memory(
    tmp_path,
):
    patch = "--- a/value.py\n+++ b/value.py\n@@ -1 +1 @@\n-VALUE = 1\n+VALUE = 2\n"
    runner, client, _ = make_runner(tmp_path, [{"content": patch}], auto_approve=False)
    try:
        with pytest.raises(AgentPaused) as paused:
            await runner("Coder", request())
        state = runner.checkpoints.load(paused.value.invocation_id)
        assert state["pending_approval"]["name"] == "apply_patch"
        assert (runner.tools.workspace.root / "value.py").read_text() == "VALUE = 1\n"
        next_runner, other, _ = make_runner(tmp_path, [], auto_approve=False)
        try:
            result = await next_runner.resume(
                paused.value.invocation_id, approval=approve(runner, paused.value)
            )
            assert "+VALUE = 2" in result.output
            assert (
                next_runner.tools.workspace.root / "value.py"
            ).read_text() == "VALUE = 2\n"
            assert next_runner.tools.workspace.coder_paths == {"value.py"}
            assert other.usage.snapshot(run_id="run-one")["calls"] == 1
        finally:
            await other.aclose()
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_partial_model_tool_batch_resumes_without_replaying_saved_edit(
    tmp_path, monkeypatch
):
    edit = tool("edit_file", {"path": "value.py", "content": "VALUE = 2\n"}, "edit")
    runner, client, _ = make_runner(
        tmp_path, [{"tool_calls": [edit, tool(identifier="read")]}]
    )

    class Crash(BaseException):
        pass

    perform = runner._perform_tool

    async def crashed(role, name, arguments, **kwargs):
        if kwargs.get("tool_call_id") == "read":
            raise Crash()
        return await perform(role, name, arguments, **kwargs)

    monkeypatch.setattr(runner, "_perform_tool", crashed)
    try:
        with pytest.raises(Crash):
            await runner("Coder", request())
        rows = [json.loads(line) for line in client.log_path.read_text().splitlines()]
        identifier = next(
            e["agent_call_id"] for e in rows if e["event"] == "agent_started"
        )
        saved = runner.checkpoints.load(identifier)
        assert saved["tool_index"] == 1
        assert list(saved["receipts"]) == ["turn:1:0:edit"]
        assert (runner.tools.workspace.root / "value.py").read_text() == "VALUE = 2\n"
        with pytest.raises(LifecycleError, match="resume_unsafe_inflight"):
            await runner.resume(identifier)
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_resume_after_first_observation_in_multi_tool_response(
    tmp_path, monkeypatch
):
    runner, client, _ = make_runner(
        tmp_path, [{"tool_calls": [tool(identifier="one"), tool(identifier="two")]}]
    )
    observe = runner._observe

    class Crash(BaseException):
        pass

    async def crashed(state):
        await observe(state)
        if state["tool_index"] == 1:
            runner._save(state)
            raise Crash()

    monkeypatch.setattr(runner, "_observe", crashed)
    try:
        with pytest.raises(Crash):
            await runner("Analyst", request())
        rows = [json.loads(line) for line in client.log_path.read_text().splitlines()]
        identifier = next(
            e["agent_call_id"] for e in rows if e["event"] == "agent_started"
        )
        next_runner, other, seen = make_runner(
            tmp_path, [{"content": json.dumps(FINAL)}]
        )
        try:
            await next_runner.resume(identifier)
            assert [
                m["tool_call_id"] for m in seen[0]["messages"] if m["role"] == "tool"
            ] == ["one", "two"]
            rows = [
                json.loads(line) for line in other.log_path.read_text().splitlines()
            ]
            assert (
                sum(
                    e["event"] == "lifecycle_transition"
                    and e.get("to_state") == "EXECUTING_TOOL"
                    for e in rows
                )
                == 2
            )
        finally:
            await other.aclose()
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_process_crash_after_side_effect_blocks_reexecution(
    tmp_path, monkeypatch
):
    edit = tool("edit_file", {"path": "value.py", "content": "VALUE = 9\n"}, "edit")
    runner, client, _ = make_runner(tmp_path, [{"tool_calls": [edit]}])
    perform = runner._perform_tool

    class Crash(BaseException):
        pass

    async def crashed(*args, **kwargs):
        await perform(*args, **kwargs)
        raise Crash()

    monkeypatch.setattr(runner, "_perform_tool", crashed)
    try:
        with pytest.raises(Crash):
            await runner("Coder", request())
        rows = [json.loads(line) for line in client.log_path.read_text().splitlines()]
        identifier = next(
            e["agent_call_id"] for e in rows if e["event"] == "agent_started"
        )
        assert (runner.tools.workspace.root / "value.py").read_text() == "VALUE = 9\n"
        with pytest.raises(LifecycleError, match="resume_unsafe_inflight"):
            await runner.resume(identifier)
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_resume_rejects_unsupported_checkpoint_phase(tmp_path):
    runner, client, _ = make_runner(
        tmp_path, [{"tool_calls": [tool()]}], auto_approve=False
    )
    try:
        with pytest.raises(AgentPaused) as paused:
            await runner("Analyst", request())
        saved = runner.checkpoints.load(paused.value.invocation_id)
        saved["phase"] = "unknown"
        runner.checkpoints.save(saved)
        with pytest.raises(LifecycleError, match="checkpoint_corrupt"):
            await runner.resume(paused.value.invocation_id)
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_tester_final_validation_receipt_prevents_test_rerun(
    tmp_path, monkeypatch
):
    report = {
        "tests_to_run": ["test_value"],
        "results": [{"test": "test_value", "status": "passed", "details": ""}],
        "verdict": "pass",
        "covered_ac_ids": [],
        "blocking_issues": [],
    }
    runner, client, _ = make_runner(tmp_path, [{"content": json.dumps(report)}])
    ws = runner.tools.workspace
    (ws.root / "test_value.py").write_text(
        "import unittest\nfrom pathlib import Path\nclass T(unittest.TestCase):\n def test_value(self):\n  p=Path('count.txt'); p.write_text(str(int(p.read_text())+1) if p.exists() else '1')\n"
    )
    original = runner._finalize_output

    class Crash(BaseException):
        pass

    async def crashed(state):
        await original(state)
        raise Crash()

    monkeypatch.setattr(runner, "_finalize_output", crashed)
    req = request()
    req["artifacts"] = {"patch_hash": "fixture-hash"}
    try:
        with pytest.raises(Crash):
            await runner("Tester", req)
        assert (ws.root / "count.txt").read_text() == "1"
        rows = [json.loads(line) for line in client.log_path.read_text().splitlines()]
        identifier = next(
            e["agent_call_id"] for e in rows if e["event"] == "agent_started"
        )
        next_runner, other, _ = make_runner(tmp_path, [])
        try:
            result = await next_runner.resume(identifier)
            assert result.runtime["tests_run"] == 1
            assert result.runtime["patch_hash"] == "fixture-hash"
            assert (ws.root / "count.txt").read_text() == "1"
            assert other.usage.snapshot(run_id="run-one")["calls"] == 1
        finally:
            await other.aclose()
    finally:
        await client.aclose()
