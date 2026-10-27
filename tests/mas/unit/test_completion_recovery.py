import json

import httpx
import pytest
from test_lifecycle_loop import FINAL, make_runner, request, tool
from test_model_profiles import call_context

from raac.common.errors import AgentExecutionError, RunGuardError
from raac.common.trace_viewer import TraceViewer
from raac.common.trace_web import present_event
from raac.harness.layer1_lifecycle.approval import AgentPaused
from raac.llm.client import CompletionFormatError, LLMClient, LLMError
from raac.llm.config import LLMConfig


def response(content, finish="stop", usage=True, tool_calls=None):
    result = {"choices": [{"message": {"role": "assistant", "content": content,
                                      "reasoning_content": "private reasoning must not be logged",
                                      **({"tool_calls": tool_calls} if tool_calls else {})},
                           "finish_reason": finish}]}
    if usage:
        result["usage"] = {"prompt_tokens": 10, "completion_tokens": 5,
                           "prompt_tokens_details": {"cached_tokens": 3}, "private_extra": "do-not-log"}
    return result


@pytest.mark.asyncio
@pytest.mark.parametrize("content,finish,code", [
    (None, "stop", "empty_completion"), ('{"summary":', "length", "truncated_completion"),
    (None, "length", "truncated_completion"),
])
async def test_invalid_completion_retains_observed_usage_and_reason(tmp_path, content, finish, code):
    client = LLMClient(LLMConfig(model="fixture"), log_path=tmp_path / "llm.jsonl", trace_content=True,
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=response(content, finish))))
    try:
        with pytest.raises(CompletionFormatError) as error:
            await client.complete([], context=call_context())
        assert error.value.code == code
        assert error.value.content == (content or "")
        usage = client.usage.snapshot(run_id="profiles-run")
        assert usage["usage_complete"] and usage["total_tokens"] == 15 and usage["cached_tokens"] == 3
        rows = [row for line in client.log_path.read_text().splitlines()
                if (row := json.loads(line))["event"] in {"llm_call", "llm_response"}]
        assert all(row["finish_reason"] == finish and row["response_error"] == code for row in rows)
        assert rows[0]["usage"]["completion_tokens"] == 5
        assert "private reasoning" not in client.log_path.read_text()
        assert "do-not-log" not in client.log_path.read_text()
        assert "private reasoning" not in str(error.value)
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_malformed_envelope_preserves_usage_without_becoming_a_recoverable_completion(tmp_path):
    data = response("unused")
    data["choices"] = []
    client = LLMClient(LLMConfig(model="fixture"), log_path=tmp_path / "llm.jsonl",
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=data)))
    try:
        with pytest.raises(LLMError) as error:
            await client.complete([], context=call_context())
        assert not isinstance(error.value, CompletionFormatError)
        assert client.usage.snapshot(run_id="profiles-run")["total_tokens"] == 15
        assert json.loads(client.log_path.read_text())["response_error"] == "invalid_response"
    finally:
        await client.aclose()


async def install_responses(client, replies, seen):
    await client.aclose()
    def respond(req):
        seen.append(json.loads(req.content))
        return httpx.Response(200, json=replies.pop(0))
    client._http = httpx.AsyncClient(base_url="http://fixture/v1/", transport=httpx.MockTransport(respond))


@pytest.mark.asyncio
@pytest.mark.parametrize("content,finish", [(None, "stop"), ('{"summary":', "length"), (json.dumps(FINAL), "length")])
async def test_received_format_failure_can_repair_within_shared_limits(tmp_path, content, finish):
    runner, client, _ = make_runner(tmp_path, [])
    seen = []
    await install_responses(client, [response(content, finish), response(json.dumps(FINAL))], seen)
    try:
        assert (await runner("Analyst", request()))["summary"] == FINAL["summary"]
        assert len(seen) == 2
        assert "complete replacement" in seen[1]["messages"][-1]["content"]
        assert client.usage.snapshot(run_id="run-one")["total_tokens"] == 30
    finally:
        await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("block", ["missing_usage", "budget"])
async def test_recovery_does_not_bypass_usage_or_token_budget(tmp_path, block):
    runner, client, _ = make_runner(tmp_path, [], **({"token_limit": 15} if block == "budget" else {}))
    seen = []
    await install_responses(client, [response(None, usage=block != "missing_usage")], seen)
    try:
        with pytest.raises(RunGuardError, match="usage_unavailable" if block == "missing_usage" else "budget_exceeded"):
            await runner("Analyst", request())
        assert len(seen) == 1
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_penultimate_analyst_call_reserves_last_call_for_repair(tmp_path):
    runner, client, _ = make_runner(tmp_path, [])
    seen = []
    replies = [response(None, tool_calls=[tool(identifier=str(i))]) for i in range(4)]
    replies += [response(None), response(json.dumps(FINAL))]
    await install_responses(client, replies, seen)
    try:
        assert (await runner("Analyst", request()))["summary"] == FINAL["summary"]
        assert len(seen) == 6
        assert all(call.get("tools") for call in seen[:4])
        assert not seen[4].get("tools") and not seen[5].get("tools")
        assert client.usage.snapshot(run_id="run-one")["calls"] == 6
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_final_empty_completion_does_not_get_a_seventh_call(tmp_path):
    runner, client, _ = make_runner(tmp_path, [])
    seen = []
    replies = [response(None, tool_calls=[tool(identifier=str(i))]) for i in range(4)]
    replies += [response(None), response(None)]
    await install_responses(client, replies, seen)
    try:
        with pytest.raises(AgentExecutionError, match="empty_completion"):
            await runner("Analyst", request())
        assert len(seen) == 6
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_truncated_tool_call_is_never_executed(tmp_path):
    runner, client, _ = make_runner(tmp_path, [])
    seen = []
    replies = [response(None, "length", tool_calls=[tool("delete_file", {"path": "value.py"})]),
               response(json.dumps(FINAL))]
    await install_responses(client, replies, seen)
    try:
        await runner("Analyst", request())
        assert (runner.tools.workspace.root / "value.py").exists()
        assert not any(message["role"] == "tool" for message in seen[1]["messages"])
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_pending_completion_error_survives_checkpoint_resume(tmp_path, monkeypatch):
    runner, client, _ = make_runner(tmp_path, [])
    await install_responses(client, [response(None, "length")], [])
    decide = runner._decide
    async def pause(state):
        raise AgentPaused(state["invocation_id"], state["revision"], "fixture pause")
    monkeypatch.setattr(runner, "_decide", pause)
    try:
        with pytest.raises(AgentPaused) as paused:
            await runner("Analyst", request())
        saved = runner.checkpoints.load(paused.value.invocation_id)
        assert saved["completion_error"] == "truncated_completion" and not saved["inflight"]
        monkeypatch.setattr(runner, "_decide", decide)
        await install_responses(client, [response(json.dumps(FINAL))], [])
        assert (await runner.resume(paused.value.invocation_id))["summary"] == FINAL["summary"]
        assert client.usage.snapshot(run_id="run-one")["total_tokens"] == 30
    finally:
        await client.aclose()


def test_viewers_explain_response_failure_without_opening_raw_payload():
    event = {"event": "llm_call", "status": 200, "error": "InvalidResponse",
             "response_error": "empty_completion", "finish_reason": "stop"}
    assert "response rỗng" in present_event(event, 0)["summary"]
    assert "empty_completion" in TraceViewer().render(event)[0]
