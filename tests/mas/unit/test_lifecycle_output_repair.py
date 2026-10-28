import json
import time

import pytest
from test_lifecycle_loop import FINAL, make_runner, request, tool
from test_lifecycle_resume import approve

from raac.common.errors import AgentExecutionError, RunGuardError
from raac.harness.layer1_lifecycle.approval import AgentPaused


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", ["not json", '<｜｜DSML｜｜ invoke name="replace_text">'])
async def test_repair_once_without_executing_text(tmp_path, invalid):
    runner, client, seen = make_runner(tmp_path, [
        {"content": invalid}, {"content": json.dumps(FINAL)},
    ])
    try:
        assert (await runner("Analyst", request()))["summary"] == FINAL["summary"]
        assert len(seen) == 2
        assert "Correct the response format once" in seen[1]["messages"][-1]["content"]
        state = runner.checkpoints.latest_run("run-one")
        assert state["output_recoveries"] == 1
        assert runner.checkpoints.load(state["invocation_id"])["output_recoveries"] == 1
        assert (runner.tools.workspace.root / "value.py").read_text() == "VALUE = 1\n"
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_second_invalid_output_fails_with_code(tmp_path):
    runner, client, seen = make_runner(tmp_path, [{"content": "bad"}, {"content": "bad again"}])
    try:
        with pytest.raises(AgentExecutionError, match="invalid_output"):
            await runner("Analyst", request())
        assert len(seen) == 2
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_dsml_documented_inside_valid_json_is_not_a_tool_call(tmp_path):
    output = {**FINAL, "summary": '<DSML invoke name="example"> is documentation'}
    runner, client, seen = make_runner(tmp_path, [{"content": json.dumps(output)}])
    try:
        assert (await runner("Analyst", request()))["summary"] == output["summary"]
        assert len(seen) == 1
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_resume_does_not_reset_repair_quota(tmp_path):
    runner, client, _ = make_runner(tmp_path, [
        {"content": "bad"}, {"tool_calls": [tool()]},
    ], auto_approve=False)
    try:
        with pytest.raises(AgentPaused) as paused:
            await runner("Analyst", request())
        resumed, next_client, seen = make_runner(tmp_path, [{"content": "bad again"}], auto_approve=False)
        try:
            with pytest.raises(AgentExecutionError, match="invalid_output"):
                await resumed.resume(paused.value.invocation_id, approval=approve(resumed, paused.value))
            assert len(seen) == 1
            assert next_client.usage.snapshot(run_id="run-one")["calls"] == 3
        finally:
            await next_client.aclose()
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_finalize_adapter_failure_does_not_trigger_model_retry(tmp_path, monkeypatch):
    from raac.common.agent_contracts import OutputFormatError

    runner, client, seen = make_runner(tmp_path, [{"content": json.dumps(FINAL)}])
    calls = []
    async def finalize(state):
        calls.append(True)
        raise OutputFormatError("adapter result invalid after execution")
    monkeypatch.setattr(runner, "_finalize_output", finalize)
    try:
        with pytest.raises(OutputFormatError):
            await runner("Analyst", request())
        assert len(seen) == len(calls) == 1
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_final_call_cannot_get_an_extra_repair(tmp_path):
    runner, client, seen = make_runner(tmp_path, [{"content": "bad"}])
    role = runner.registry.get("Analyst")
    object.__setattr__(role.spec, "max_llm_calls", 1)
    try:
        with pytest.raises(AgentExecutionError, match="invalid_output"):
            await runner("Analyst", request())
        assert len(seen) == 1
    finally:
        await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("guard", ["deadline", "usage"])
async def test_repair_does_not_bypass_guards(tmp_path, guard):
    runner, client, _ = make_runner(tmp_path, [])
    state = {"request": request(), "deadline_at": time.time() - 1 if guard == "deadline" else None}
    if guard == "usage":
        client.usage.add({"run_id": "run-one", "agent_id": "analyst", "level": "L3"}, None)
    try:
        with pytest.raises(TimeoutError if guard == "deadline" else RunGuardError):
            runner._recover_output(state, "invalid_output", "bad")
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_repaired_tool_call_still_enforces_allowlist(tmp_path):
    runner, client, seen = make_runner(tmp_path, [
        {"content": '<DSML invoke name="replace_text">'},
        {"tool_calls": [tool("delete_file", {"path": "value.py"})]},
        {"content": json.dumps(FINAL)},
    ])
    try:
        await runner("Analyst", request())
        assert json.loads(seen[2]["messages"][-1]["content"])["error_kind"] == "ToolNotAllowed"
        assert (runner.tools.workspace.root / "value.py").exists()
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_coder_progress_is_bounded_and_tracks_stale_evidence(tmp_path):
    runner, client, _ = make_runner(tmp_path, [])
    context = {"agent_call_id": "fixture"}
    runner.tools._evidence["fixture"] = [
        {"tool_call_id": str(i), "status": "completed", "exit_code": 0, "code_edit_count": 0, "patch_hash": "fixture"}
        for i in range(8)
    ]
    try:
        assert len(runner.tools.coder_progress(context)) == 3
        assert all(e["matches_current_revision"] for e in runner.tools.coder_progress(context))
        runner.tools.workspace.code_edit_count += 1
        assert not any(e["matches_current_revision"] for e in runner.tools.coder_progress(context))
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_near_deadline_coder_receives_handoff_hint_without_losing_tools(tmp_path):
    runner, client, seen = make_runner(tmp_path, [{"content": " "}])
    req = {**request(), "deadline_at": time.time() + 45}
    try:
        output = await runner("Coder", req)
        assert output.output == ""
        system = seen[0]["messages"][0]["content"]
        assert "Deadline is near" in system
        assert '"calls_left_including_this": 20' in system
        assert seen[0]["tools"]
        progress = system.split("Runtime progress: ", 1)[1]
        assert len(progress) < 1500
    finally:
        await client.aclose()
