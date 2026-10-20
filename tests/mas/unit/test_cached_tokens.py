"""Cached input accounting without double charging or inventing missing usage."""
import json
from pathlib import Path
import tempfile
import unittest

import httpx
import pytest

from raac.llm.client import LLMClient
from raac.llm.config import LLMConfig
from raac.llm.usage import UsageTracker, cached_tokens, logged_usage
from raac.common.trace_viewer import TraceViewer
from raac.common.trace_web import present_event


def context(**updates):
    return {"run_id": "cache-run", "agent_id": "coder", "level": "L3", "task_id": "task",
            "registry_version": "test", "prompt_version": "test", "provenance": {}, **updates}


def observed(cache=6):
    return {"prompt_tokens": 10, "completion_tokens": 5,
            "prompt_tokens_details": {"cached_tokens": cache}}


@pytest.mark.parametrize("usage,expected", [
    (observed(),6), (observed(0),0), (observed(10),10),
    ({"prompt_tokens":10,"completion_tokens":5,"cached_tokens":3},3),
    (None,None), ({"prompt_tokens":10,"completion_tokens":5},None),
    (observed(-1),None), (observed(11),None), (observed(True),None),
    (observed(1.5),None), (observed("6"),None), (observed(None),None),
    ({"prompt_tokens":True,"cached_tokens":0},None),
    ({**observed(-1),"cached_tokens":3},None),
])
def test_extracts_only_observed_valid_cached_input(usage,expected):
    assert cached_tokens(usage) == expected


def test_log_whitelists_usage_and_normalizes_cache():
    assert logged_usage({**observed(),"private_extra":"do-not-log"}) == {
        "prompt_tokens":10,"completion_tokens":5,"cached_tokens":6}
    assert logged_usage({"prompt_tokens":10,"completion_tokens":5})["cached_tokens"] is None


def test_aggregation_isolated_by_run_role_level_and_cache_is_not_added_twice():
    tracker=UsageTracker()
    tracker.add(context(),observed())
    tracker.add(context(),observed(0))
    tracker.add(context(agent_id="tester"),observed(4))
    tracker.add(context(level="L2"),observed(2))
    tracker.add(context(run_id="other"),observed(9))
    assert tracker.snapshot(run_id="cache-run")["cached_tokens"] == 12
    assert tracker.snapshot(run_id="cache-run")["total_tokens"] == 60
    assert tracker.snapshot(run_id="cache-run",agent_id="coder",level="L3")["cached_tokens"] == 6
    assert tracker.snapshot(run_id="cache-run",agent_id="tester")["cached_tokens"] == 4
    assert tracker.snapshot(run_id="other")["cached_tokens"] == 9


@pytest.mark.parametrize("usage", [None, {"prompt_tokens":10,"completion_tokens":5}, observed(20)])
def test_missing_cache_never_becomes_a_complete_zero(usage):
    tracker=UsageTracker()
    tracker.add(context(),observed())
    tracker.add(context(),usage)
    result=tracker.snapshot(run_id="cache-run")
    assert result["cached_tokens"] is None
    assert not result["cache_usage_complete"]
    assert result["usage_complete"] == (usage is not None)


def test_failed_http_attempt_does_not_fabricate_zero_cache():
    tracker=UsageTracker()
    tracker.record_attempt(context())  # e.g. HTTP 429, no usage provided
    tracker.record_attempt(context())
    tracker.add(context(),observed(),count_call=False)
    result=tracker.snapshot(run_id="cache-run")
    assert result["calls"] == 2
    assert result["total_tokens"] == 15
    assert result["usage_complete"]
    assert result["cached_tokens"] is None
    assert not result["cache_usage_complete"]


def test_terminal_and_web_distinguish_observed_zero_from_missing_cache():
    for cache,expected in ((0,"cache 0"),(6,"cache 6"),(None,"cache chưa có số liệu")):
        usage=observed(cache)
        event={"event":"llm_call","agent_id":"coder","status":200,"usage":usage}
        assert expected in TraceViewer().render(event)[0]
        assert expected in present_event(event,0)["summary"]
    old={"event":"llm_call","usage":{"prompt_tokens":10,"completion_tokens":5}}
    assert "cache chưa có số liệu" in TraceViewer().render(old)[0]


def test_terminal_final_totals_use_cache_snapshot_even_with_partial_log():
    viewer=TraceViewer()
    final={"event":"run_completed","status":"completed","usage":{
        "prompt_tokens":100,"completion_tokens":20,"total_tokens":120,"calls":5,
        "usage_complete":True,"cached_tokens":60,"cache_usage_complete":True}}
    line=viewer.render(final)[0]
    assert "token 120" in line
    assert "cache 60" in line
    final["usage"].pop("cached_tokens")
    assert "cache chưa có số liệu" in TraceViewer().render(final)[0]


class CachedClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_http_cache_survives_into_log_and_run_snapshot_without_extra_token_charge(self):
        with tempfile.TemporaryDirectory() as directory:
            log=Path(directory)/"llm.jsonl"
            def handler(request):
                return httpx.Response(200,json={"choices":[{"finish_reason":"stop",
                    "message":{"content":"{}"}}],"usage":{**observed(),"private_extra":"do-not-log"}})
            client=LLMClient(LLMConfig(model="test-model"),log_path=log,transport=httpx.MockTransport(handler))
            try:
                result=await client.complete([],context=context())
            finally:
                await client.aclose()
            assert result.usage["prompt_tokens_details"]["cached_tokens"] == 6
            snapshot=client.usage.snapshot(run_id="cache-run")
            assert snapshot["cached_tokens"] == 6
            assert snapshot["cache_usage_complete"]
            assert snapshot["total_tokens"] == 15
            record=json.loads(log.read_text())
            assert record["usage"]["cached_tokens"] == 6
            assert "do-not-log" not in log.read_text()

    async def test_missing_cache_does_not_block_further_calls_when_base_usage_is_known(self):
        with tempfile.TemporaryDirectory() as directory:
            client=LLMClient(LLMConfig(model="test-model"),log_path=Path(directory)/"llm.jsonl",
                transport=httpx.MockTransport(lambda request:httpx.Response(200,json={
                    "choices":[{"finish_reason":"stop","message":{"content":"{}"}}],
                    "usage":{"prompt_tokens":10,"completion_tokens":5}})))
            try:
                await client.complete([],context=context())
                await client.complete([],context=context())
            finally:
                await client.aclose()
            snapshot=client.usage.snapshot(run_id="cache-run")
            assert snapshot["total_tokens"] == 30
            assert snapshot["calls"] == 2
            assert snapshot["usage_complete"]
            assert snapshot["cached_tokens"] is None
