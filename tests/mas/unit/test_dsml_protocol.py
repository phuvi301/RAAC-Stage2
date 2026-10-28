import json

import httpx
import pytest
from test_lifecycle_loop import FINAL, make_runner, request
from test_lifecycle_resume import approve
from test_model_profiles import call_context

from raac.harness.layer1_lifecycle.approval import AgentPaused
from raac.llm.client import CompletionFormatError, LLMClient
from raac.llm.config import LLMConfig
from raac.llm.protocol import normalize_message


def dsml(name="read_file", params=None):
    params = params if params is not None else '<DSML parameter name="path" string="true">value.py</DSML parameter>'
    return f'<DSML calls><DSML invoke name="{name}">{params}</DSML invoke></DSML calls>'


TOOLS = [{"type": "function", "function": {"name": "read_file"}}]


@pytest.mark.parametrize("bars", ["", "|", "||", "\uff5c", "\uff5c\uff5c"])
def test_delimiters_values_and_stable_identity(bars):
    content = dsml(params='<DSML parameter name="path" string="true">a\n\u4e2d.py</DSML parameter><DSML parameter name="options" string="false">{"count":2,"ok":true}</DSML parameter>')
    if bars:
        content = content.replace("DSML ", f"{bars}DSML{bars} ")
    result, protocol = normalize_message({"content": content}, TOOLS, "call")
    assert protocol == "dsml"
    call = result["tool_calls"][0]
    assert call["id"] == "call_0"
    assert json.loads(call["function"]["arguments"]) == {"path": "a\n\u4e2d.py", "options": {"count": 2, "ok": True}}


@pytest.mark.parametrize("content,tools", [
    ("example " + dsml(), TOOLS), ("```\n" + dsml() + "\n```", TOOLS),
    (dsml(), []), (dsml("bash"), TOOLS), (dsml()[:-5], TOOLS),
    (dsml(params='<DSML parameter name="x" string="false">NaN</DSML parameter>'), TOOLS),
    (dsml(params='<DSML parameter name="x" string="true">a</DSML parameter>' * 2), TOOLS),
])
def test_rejects_ambiguous_or_unauthorized_content(content, tools):
    with pytest.raises(ValueError):
        normalize_message({"content": content}, tools, "call")


@pytest.mark.asyncio
async def test_dsml_resume_executes_once_and_sends_native_history(tmp_path):
    runner, client, _ = make_runner(tmp_path, [{"content": dsml()}], auto_approve=False)
    try:
        with pytest.raises(AgentPaused) as paused:
            await runner("Analyst", request())
        decision = approve(runner, paused.value)
        resumed, other, seen = make_runner(tmp_path, [{"content": json.dumps(FINAL)}], auto_approve=False)
        try:
            result = await resumed.resume(paused.value.invocation_id, approval=decision)
            assert await resumed.resume(paused.value.invocation_id) == result
            history = seen[0]["messages"]
            assert len([m for m in history if m["role"] == "tool"]) == 1
            assert any(m.get("tool_calls") for m in history)
        finally:
            await other.aclose()
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_invalid_protocol_preserves_usage_and_error_log(tmp_path):
    client = LLMClient(LLMConfig(model="fixture"), log_path=tmp_path / "llm.jsonl",
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={
            "choices": [{"message": {"content": dsml("bash")}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 2}})))
    try:
        with pytest.raises(CompletionFormatError):
            await client.complete([], context=call_context(), tools=TOOLS)
        assert client.usage.snapshot(run_id="profiles-run")["total_tokens"] == 12
        rows = [json.loads(s) for s in client.log_path.read_text().splitlines()]
        assert any(x.get("response_error") == "invalid_tool_call_format" for x in rows)
    finally:
        await client.aclose()


@pytest.mark.parametrize("calls", [False, {}, "tool", [{"id": "x"}],
    [{"id": "x", "type": "function", "function": {"name": "read_file", "arguments": {}}}]])
def test_native_envelope_rejected(calls):
    with pytest.raises(ValueError):
        normalize_message({"tool_calls": calls}, TOOLS, "call")


def test_multiple_calls_and_mixed_protocol_rejection():
    invoke = dsml().removeprefix('<DSML calls>').removesuffix('</DSML calls>')
    content = '<DSML calls>' + invoke * 4 + '</DSML calls>'
    message, _ = normalize_message({"content": content}, TOOLS, "call")
    assert len({c["id"] for c in message["tool_calls"]}) == 4
    with pytest.raises(ValueError):
        normalize_message({"content": '<DSML calls>' + invoke * 5 + '</DSML calls>'}, TOOLS, "call")
    with pytest.raises(ValueError):
        normalize_message({**message, "content": dsml()}, TOOLS, "call")
    with pytest.raises(ValueError):
        normalize_message({"tool_calls": [message["tool_calls"][0]] * 2}, TOOLS, "call")


@pytest.mark.asyncio
async def test_rate_limit_still_retries_and_timeout_retains_safe_cause(tmp_path):
    from raac.common.errors import RunGuardError
    statuses = [429, 200, 524]
    def handler(_):
        status = statuses.pop(0)
        return httpx.Response(status, json={"choices": [{"message": {"content": "{}"}}],
                                           "usage": {"prompt_tokens": 10, "completion_tokens": 2}})
    client = LLMClient(LLMConfig(model="fixture", backoff_seconds=0.0),
                       log_path=tmp_path / "llm.jsonl", transport=httpx.MockTransport(handler))
    try:
        await client.complete([], context=call_context())
        with pytest.raises(RunGuardError):
            await client.complete([], context=call_context())
        error = client.upstream_errors["profiles-run"]
        assert error["status"] == 524
        assert error["retry_blocked_reason"] == "usage_unavailable"
        assert not error["retry_scheduled"]
        assert not statuses
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_unauthorized_dsml_repairs_without_tool_side_effect(tmp_path):
    runner, client, seen = make_runner(tmp_path, [{"content": dsml("bash")}, {"content": json.dumps(FINAL)}])
    try:
        assert (await runner("Analyst", request()))["summary"] == FINAL["summary"]
        assert len(seen) == 2
        rows = [json.loads(s) for s in client.log_path.read_text().splitlines()]
        assert not any(x["event"] == "tool_call_started" for x in rows)
        assert sum(x["event"] == "output_recovery_started" for x in rows) == 1
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_truncated_dsml_never_converted(tmp_path):
    client = LLMClient(LLMConfig(model="fixture"), log_path=tmp_path / "llm.jsonl",
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={
            "choices": [{"finish_reason": "length", "message": {"content": dsml()}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 2}})))
    try:
        with pytest.raises(CompletionFormatError) as error:
            await client.complete([], context=call_context(), tools=TOOLS)
        assert error.value.code == "truncated_completion"
        assert "tool_protocol_normalized" not in client.log_path.read_text()
    finally:
        await client.aclose()


def test_summary_displays_upstream_cause():
    from raac.common.trace_viewer import TraceViewer
    from raac.common.trace_web import present_event
    event = {"event": "run_completed", "status": "aborted", "stop_reason": "timeout",
             "upstream_error": {"status": 524, "role": "Tester", "turn": 9}}
    assert "HTTP 524 / Tester / turn 9" in TraceViewer().render(event)[0]
    assert "HTTP 524 / Tester / turn 9" in present_event(event, 0)["summary"]
