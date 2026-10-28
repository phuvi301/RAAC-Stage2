import json
import time
from pathlib import Path

import pytest
import yaml
from test_lifecycle_loop import FINAL, make_runner, request, tool
from test_lifecycle_resume import approve

from raac.benchmark.harness_config import HarnessConfig, load_harness
from raac.common.errors import AgentExecutionError, RunGuardError
from raac.harness.layer1_lifecycle.approval import AgentPaused
from raac.harness.layer1_lifecycle.checkpoint import LifecycleError

ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize("limit", [None, 1, 800000])
def test_explicit_budget_configuration(limit):
    data = yaml.safe_load((ROOT / "configs/harness.yaml").read_text())
    data["token_limit"] = limit
    assert HarnessConfig.model_validate(data).token_limit == limit


@pytest.mark.parametrize("limit", [0, -1, True, "null", 1.5])
def test_invalid_budget_not_treated_as_disabled(limit):
    data = yaml.safe_load((ROOT / "configs/harness.yaml").read_text())
    data["token_limit"] = limit
    with pytest.raises(ValueError):
        HarnessConfig.model_validate(data)


def test_trial_config_only_disables_budget():
    config = yaml.safe_load((ROOT / "configs/experiments/benchmark-no-budget.yaml").read_text())
    benchmark = yaml.safe_load((ROOT / "configs/benchmark.yaml").read_text())
    assert config == {**benchmark, "harness_config": "configs/experiments/harness-no-budget.yaml"}
    _, actual = load_harness(ROOT, config)
    _, default = load_harness(ROOT, {})
    assert default["token_limit"] == 800000
    assert actual == {**default, "token_limit": None, "version": "harness-demo-v14-no-budget"}


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", [None, 1])
async def test_optional_budget_keeps_usage_and_enforces_numeric_limit(tmp_path, limit):
    runner, client, seen = make_runner(tmp_path, [{"content": json.dumps(FINAL)}], token_limit=limit)
    try:
        if limit is None:
            assert (await runner("Analyst", request()))["summary"] == FINAL["summary"]
        else:
            with pytest.raises(RunGuardError, match="budget_exceeded"):
                await runner("Analyst", request())
        usage = client.usage.snapshot(run_id="run-one")
        assert usage["total_tokens"] == 12 and usage["calls"] == 1 and len(seen) == 1
        assert runner.budget_guard({"work": {"run_id": "run-one"}}) == (limit is None)
        if limit is None:
            client.usage.add({"run_id": "run-one", "agent_id": "analyst", "level": "L3"}, None)
            assert not runner.budget_guard({"work": {"run_id": "run-one"}})
            with pytest.raises(RunGuardError, match="usage_unavailable"):
                runner._check_budget(request())
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_disabling_budget_preserves_call_cap(tmp_path):
    runner, client, seen = make_runner(tmp_path,
        [{"tool_calls": [tool(identifier=str(i))]} for i in range(6)], token_limit=None)
    try:
        with pytest.raises(AgentExecutionError, match="agent_call_limit"):
            await runner("Analyst", request())
        assert len(seen) == 5
        assert not seen[-1].get("tools")
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_disabling_budget_preserves_deadline(tmp_path):
    runner, client, seen = make_runner(tmp_path, [], token_limit=None)
    try:
        with pytest.raises(TimeoutError):
            runner._guard({"request": request(), "deadline_at": time.time() - 1})
        assert not seen
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_no_budget_resume_keeps_policy_and_rejects_policy_change(tmp_path):
    runner, client, _ = make_runner(tmp_path, [{"tool_calls": [tool()]}], token_limit=None, auto_approve=False)
    try:
        with pytest.raises(AgentPaused) as paused:
            await runner("Analyst", request())
        decision = approve(runner, paused.value)
        resumed, other, _ = make_runner(tmp_path, [{"content": json.dumps(FINAL)}], token_limit=None, auto_approve=False)
        try:
            resumed.token_limit = 800000
            with pytest.raises(LifecycleError, match="config"):
                await resumed.resume(paused.value.invocation_id, approval=decision)
            resumed.token_limit = None
            result = await resumed.resume(paused.value.invocation_id, approval=decision)
            assert result["summary"] == FINAL["summary"]
            assert other.usage.snapshot(run_id="run-one")["total_tokens"] == 24
        finally:
            await other.aclose()
    finally:
        await client.aclose()
