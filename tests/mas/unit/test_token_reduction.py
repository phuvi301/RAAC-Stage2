"""Offline policies preserve raw evidence, protocol and provider accounting."""

import importlib.util
import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import yaml
from test_context_manager import exchange
from test_lifecycle_loop import FINAL, make_runner, request, tool

from raac.benchmark.harness_config import load_harness
from raac.harness.layer2_core.command_output import filter_messages, filter_observation
from raac.harness.layer2_core.context_manager import (
    CONCISE_GUIDANCE,
    RoleContextPolicy,
    prepare_messages,
)
from raac.harness.layer2_core.context_metrics import request_metrics
from raac.harness.layer3_execution.tool_orchestrator import ToolOrchestrator
from raac.registry import load_registry

ROOT = Path(__file__).resolve().parents[3]


def observation(**updates):
    return {"ok": True, "result": {"command": ["python", "-m", "pytest"], "status": "completed",
            "exit_code": 0, "stdout": "\x1b[32m" + ("." * 80 + "\n") * 30 + "\x1b[0m\n\n\n30 passed — đúng 日本語\n", "stderr": "", **updates}}


def test_filter_is_conservative_immutable_and_idempotent():
    raw = observation()
    original = deepcopy(raw)
    filtered = filter_observation(raw, "run_command")
    assert len(filtered["result"]["stdout"]) < len(raw["result"]["stdout"])
    assert "29 more times" in filtered["result"]["stdout"]
    assert "30 passed — đúng 日本語" in filtered["result"]["stdout"]
    assert "\x1b" not in filtered["result"]["stdout"]
    assert raw == original
    assert filter_observation(filtered, "run_command") == filtered
    for updates in ({"exit_code": 1}, {"status": "error"}, {"stderr": "warning"},
                    {"command": ["python", "-c", "pytest"]}, {"command": ["git", "diff"]},
                    {"stdout": "Traceback\n" * 100}, {"stdout": "diff --git a/x b/x\n" * 100},
                    {"stdout": "FAILED test_x\n" * 100}, {"stdout": "small\n"}, {"stdout": "def example():\n\n\n    return 1\n"}):
        raw = observation(**updates)
        assert filter_observation(raw, "run_command") == raw
    assert filter_observation(original, "read_file") == original


def test_policy_preserves_tool_pairs_task_and_batch_source():
    batch = {"ok": True, "result": {"results": [{"observation": observation()}]}}
    messages = [{"role": "system", "content": "rules"}, {"role": "user", "content": "AC and constraints"}]
    messages += exchange("run_commands", {}, batch)
    original = deepcopy(messages)
    filtered = filter_messages(messages)
    assert filtered[-1]["tool_call_id"] == messages[-1]["tool_call_id"]
    assert filtered[:3] == messages[:3]
    assert filter_messages(filtered) == filtered
    assert messages == original
    concise, _ = prepare_messages(messages, RoleContextPolicy(enabled=False, concise_output=True))
    assert concise[1:] == messages[1:]
    assert concise[0]["content"] == "rules" + CONCISE_GUIDANCE
    assert prepare_messages(concise, RoleContextPolicy(enabled=False, concise_output=True))[0] == concise


@pytest.mark.asyncio
async def test_command_refs_batch_pages_resume_and_cross_run():
    registry = load_registry(ROOT / "registry")
    raw = observation()["result"]
    workspace = SimpleNamespace(run_command=AsyncMock(return_value=raw),
        snapshot_runtime=lambda **kwargs: {}, restore_runtime=lambda snapshot: None)
    tools = ToolOrchestrator(workspace)
    result = await tools.execute(registry.get("Coder"), "run_commands", {
        "commands": [{"command": ["python", "-m", "pytest"]}]}, context={"run_id": "one"})
    child = result["result"]["results"][0]["observation"]
    ref = child["result"]["observation_ref"]
    filtered = filter_observation(child, "run_command")
    assert filtered["result"]["stdout"] != raw["stdout"]
    snapshot = tools.snapshot_runtime()
    tools = ToolOrchestrator(workspace)
    tools.restore_runtime(snapshot)
    for offset in (0, 15, 100):
        page = await tools.execute(registry.get("Coder"), "read_tool_result", {
            "observation_ref": ref, "path": ["stdout"], "offset": offset, "limit": 15}, context={"run_id": "one"})
        assert page["result"]["value"] == raw["stdout"][offset:offset + 15]
    for run, reference in (("other", ref), ("one", "bad")):
        page = await tools.execute(registry.get("Coder"), "read_tool_result", {"observation_ref": reference}, context={"run_id": run})
        assert not page["ok"]
    workspace.run_command.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["baseline", "compact", "filtered", "concise"])
@pytest.mark.parametrize("trace_content", [True, False])
async def test_http_lifecycle_metrics_match_wire_and_keep_usage(tmp_path, name, trace_content):
    _, harness = load_harness(ROOT, {"harness_config": f"configs/experiments/token_reduction/{name}.yaml"})
    runner, client, seen = make_runner(tmp_path, [{"tool_calls": [tool()]}, {"content": json.dumps(FINAL)}],
        context_policy=RoleContextPolicy.model_validate(harness["context"]))
    client.trace_content = trace_content
    try:
        assert (await runner("Analyst", request()))["acceptance_criteria"] == FINAL["acceptance_criteria"]
        events = [json.loads(line) for line in client.log_path.read_text().splitlines()]
        metrics = [event for event in events if event["event"] == "context_metrics"]
        assert len(metrics) == len(seen) == 2
        assert metrics[0]["repeated_representation_bytes"] == 0
        assert metrics[1]["repeated_representation_bytes"] > 0
        for event, wire in zip(metrics, seen):
            assert event["after"] == request_metrics(wire["messages"], wire.get("tools", []))
            assert "VALUE = 1" not in json.dumps(event)
            assert event["llm_call_id"]
        usage = client.usage.snapshot(run_id="run-one")
        assert usage["total_tokens"] == 24 and usage["cached_tokens"] is None
        assert not usage["cache_usage_complete"]
        assert bool(CONCISE_GUIDANCE in seen[0]["messages"][0]["content"]) == (name == "concise")
    finally:
        await client.aclose()


def test_metrics_read_only_and_missing_trace_does_not_invent_data(tmp_path):
    messages = [{"role": "system", "content": "private source"}]
    original = deepcopy(messages)
    metrics = request_metrics(messages, [])
    assert messages == original and "private source" not in json.dumps(metrics)
    assert metrics["estimator"] == "json-chars-div4-v1"
    spec = importlib.util.spec_from_file_location("analyze_context", ROOT / "scripts/analyze_context.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    path = tmp_path / "old.jsonl"
    path.write_text(json.dumps({"event": "llm_call", "usage": None}))
    report = module.analyze(path)
    assert report["availability"] == "unavailable" and report["provider_usage"] == [None]
    assert all(not calls for calls in report["policies"].values())


def test_invalid_harness_rejected_before_workspace(tmp_path):
    config = yaml.safe_load((ROOT / "configs/harness.yaml").read_text())
    config["context"]["command_output_filter"] = "true"
    path = tmp_path / "harness.yaml"
    path.write_text(yaml.safe_dump(config))
    with pytest.raises(ValueError):
        load_harness(ROOT, {"harness_config": str(path)})
    assert load_harness(ROOT, {})[0] == ROOT / "configs/harness.yaml"


@pytest.mark.asyncio
async def test_lifecycle_resume_rejects_changed_context_policy(tmp_path):
    from raac.harness.layer1_lifecycle.approval import AgentPaused
    from raac.harness.layer1_lifecycle.checkpoint import LifecycleError

    runner, client, seen = make_runner(tmp_path, [{"tool_calls": [tool()]}], auto_approve=False,
                                      context_policy=RoleContextPolicy(enabled=False))
    try:
        with pytest.raises(AgentPaused) as paused:
            await runner("Analyst", request())
        runner.context_policy.concise_output = True
        with pytest.raises(LifecycleError, match="config"):
            await runner.resume(paused.value.invocation_id)
        assert len(seen) == 1
    finally:
        await client.aclose()
