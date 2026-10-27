"""Single-agent loop behavior over real local tools and HTTP-only fixtures."""

import json
from pathlib import Path
import httpx
import pytest
from raac.harness.layer1_lifecycle import AgentRunner
from raac.harness.layer3_execution.local_executor import (
    LocalExecutor,
    LocalExecutionPolicy,
)
from raac.harness.layer3_execution.tools.workspace import WorkspaceTools
from raac.harness.layer3_execution.tool_orchestrator import ToolOrchestrator
from raac.llm.client import LLMClient
from raac.llm.config import LLMConfig
from raac.registry.loader import load_registry

ROOT = Path(__file__).resolve().parents[3]
FINAL = {
    "summary": "Observed source",
    "requirements": [{"id": "R1", "description": "Read source", "priority": "medium"}],
    "acceptance_criteria": ["Read source"],
    "constraints": [],
    "open_questions": [],
}


def tool(name="read_file", arguments=None, identifier="read-1"):
    return {
        "id": identifier,
        "type": "function",
        "function": {
            "name": name,
            "arguments": json.dumps(arguments or {"path": "value.py"}),
        },
    }


def make_runner(tmp_path, replies, **kwargs):
    project = tmp_path / "project"
    (project / "repo").mkdir(parents=True, exist_ok=True)
    (project / "requirement").mkdir(exist_ok=True)
    if not (project / "repo/value.py").exists():
        (project / "repo/value.py").write_text("VALUE = 1\n", newline="")
    (project / "requirement/user_chat.txt").write_text("Read source")
    seen = []

    def respond(request):
        seen.append(json.loads(request.content))
        message = replies.pop(0)
        return httpx.Response(
            200,
            json={
                "choices": [{"message": message}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 2},
            },
        )

    client = LLMClient(
        LLMConfig(model="fixture"),
        log_path=tmp_path / "results/run-one/llm.jsonl",
        transport=httpx.MockTransport(respond),
        trace_content=True,
    )
    workspace = WorkspaceTools(project, LocalExecutor(LocalExecutionPolicy()))
    runner = AgentRunner(
        load_registry(ROOT / "registry"),
        client,
        ToolOrchestrator(workspace),
        provenance={},
        **kwargs,
    )
    return runner, client, seen


def request():
    return {
        "run_id": "run-one",
        "task_id": "task-one",
        "work_id": "task-one",
        "scope": "task",
        "round": 0,
        "artifacts": {},
        "incoming_message": {"from": "caller"},
    }


@pytest.mark.asyncio
async def test_tool_observation_then_final_output_uses_lifecycle(tmp_path):
    runner, client, seen = make_runner(
        tmp_path, [{"tool_calls": [tool()]}, {"content": json.dumps(FINAL)}]
    )
    try:
        assert await runner("Analyst", request()) == FINAL | {"interface_contracts": {}}
        assert "VALUE = 1" in json.dumps(seen[1]["messages"])
        rows = [json.loads(line) for line in client.log_path.read_text().splitlines()]
        states = [
            row["to_state"] for row in rows if row["event"] == "lifecycle_transition"
        ]
        assert states == ["PLANNING", "EXECUTING_TOOL", "PLANNING", "COMPLETED"]
        assert (client.log_path.parent / "lifecycle.sqlite3").is_file()
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_two_tools_execute_in_order_without_extra_model_call(tmp_path):
    runner, client, seen = make_runner(
        tmp_path,
        [
            {"tool_calls": [tool(identifier="one"), tool(identifier="two")]},
            {"content": json.dumps(FINAL)},
        ],
    )
    try:
        await runner("Analyst", request())
        assert [
            m["tool_call_id"] for m in seen[1]["messages"] if m["role"] == "tool"
        ] == ["one", "two"]
        assert client.usage.snapshot(run_id="run-one")["calls"] == 2
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_bad_arguments_are_observations_and_bad_output_is_failure(tmp_path):
    call = tool()
    call["function"]["arguments"] = "not-json"
    runner, client, seen = make_runner(
        tmp_path, [{"tool_calls": [call]}, {"content": "invalid output"}]
    )
    try:
        with pytest.raises(ValueError):
            await runner("Analyst", request())
        assert json.loads(seen[1]["messages"][-1]["content"])["ok"] is False
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_more_than_four_tools_and_final_call_tool_are_rejected(tmp_path):
    from raac.common.errors import AgentExecutionError

    runner, client, seen = make_runner(
        tmp_path, [{"tool_calls": [tool(identifier=str(i)) for i in range(5)]}]
    )
    try:
        with pytest.raises(AgentExecutionError, match="too_many_tool_calls"):
            await runner("Analyst", request())
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_provider_reusing_tool_id_in_later_turn_reads_current_source(tmp_path):
    runner, client, seen = make_runner(
        tmp_path,
        [
            {"tool_calls": [tool(identifier="same")]},
            {"tool_calls": [tool("list_files", {"limit": 1}, "same")]},
            {"content": json.dumps(FINAL)},
        ],
    )
    try:
        assert (await runner("Analyst", request()))["summary"] == "Observed source"
        results = [
            json.loads(m["content"]) for m in seen[2]["messages"] if m["role"] == "tool"
        ]
        assert results[-1]["result"]["files"][0]["path"] == "value.py"
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_wall_deadline_cancels_a_hanging_model_request(tmp_path):
    import asyncio, time

    runner, client, _ = make_runner(tmp_path, [])

    async def hanging(request):
        await asyncio.sleep(10)
        raise AssertionError("Deadline failed to cancel request")

    await client.aclose()
    client._http = httpx.AsyncClient(
        base_url="http://fixture/v1/", transport=httpx.MockTransport(hanging)
    )
    req = request()
    req["deadline_at"] = time.time() + 0.2
    try:
        with pytest.raises(TimeoutError):
            await runner("Analyst", req)
        assert client.usage.snapshot(run_id="run-one")["usage_complete"] is False
    finally:
        await client.aclose()
