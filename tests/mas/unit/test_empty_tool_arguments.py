"""A tool call with empty `arguments` (a tool without parameters, streamed) means `{}`.

The run that crashed first sent `provision_environment` with arguments '' : the runner refused it as invalid JSON and
then sent the same assistant message back to the provider, which answered HTTP 400.
"""

import json
from pathlib import Path

import httpx
import pytest

from raac.harness.layer1_lifecycle import AgentRunner
from raac.harness.layer3_execution.local_executor import LocalExecutionPolicy, LocalExecutor
from raac.harness.layer3_execution.tool_orchestrator import ToolOrchestrator
from raac.harness.layer3_execution.tools.workspace import WorkspaceTools
from raac.llm.client import LLMClient
from raac.llm.config import LLMConfig
from raac.registry.loader import load_registry

ROOT = Path(__file__).resolve().parents[3]
SPEC = {
    "product_goal": "goal", "target_users": ["dev"],
    "user_flows": [{"id": "UF1", "title": "Read", "actor": "dev", "steps": ["open value.py"]}],
    "functional_requirements": [{"id": "FR1", "description": "Read source", "priority": "high"}],
    "non_functional_requirements": [], "interface_contracts": {},
    "acceptance_criteria": ["Read the source"], "out_of_scope": [], "assumptions": [], "open_questions": [],
}


def runner_for(tmp_path, arguments):
    project = tmp_path / "project"
    (project / "repo").mkdir(parents=True)
    (project / "requirement").mkdir()
    (project / "repo/value.py").write_text("VALUE = 1\n", newline="")
    (project / "requirement/user_chat.txt").write_text("Read source")
    replies = [
        {"tool_calls": [{"id": "call-1", "type": "function", "function": {"name": "list_files", "arguments": arguments}}]},
        {"content": json.dumps(SPEC)},
    ]
    seen = []

    def respond(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": replies.pop(0)}],
                                         "usage": {"prompt_tokens": 10, "completion_tokens": 2}})

    client = LLMClient(LLMConfig(model="fixture"), log_path=tmp_path / "results/run-one/llm.jsonl",
                       transport=httpx.MockTransport(respond), trace_content=True)
    workspace = WorkspaceTools(project, LocalExecutor(LocalExecutionPolicy()))
    runner = AgentRunner(load_registry(ROOT / "registry"), client, ToolOrchestrator(workspace), provenance={})
    return runner, client, seen


def request():
    return {"run_id": "run-one", "task_id": "task-one", "work_id": "task-one", "scope": "task", "round": 0,
            "artifacts": {}, "incoming_message": {"from": "caller"}}


@pytest.mark.asyncio
@pytest.mark.parametrize("arguments", ["", "   "])
async def test_empty_arguments_run_the_tool_and_are_sent_back_as_an_empty_object(tmp_path, arguments):
    runner, client, seen = runner_for(tmp_path, arguments)
    try:
        await runner("Analyst", request())
        second = seen[1]["messages"]
        assistant = next(message for message in second if message.get("tool_calls"))
        assert assistant["tool_calls"][0]["function"]["arguments"] == "{}"  # never '' (the provider answers 400)
        observation = json.loads(next(message for message in second if message["role"] == "tool")["content"])
        assert observation["ok"] is True and "value.py" in json.dumps(observation)
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_genuinely_broken_arguments_are_still_reported_as_invalid(tmp_path):
    runner, client, seen = runner_for(tmp_path, "{not json")
    try:
        await runner("Analyst", request())
        observation = json.loads(next(message for message in seen[1]["messages"] if message["role"] == "tool")["content"])
        assert observation["ok"] is False and "Invalid JSON" in observation["error"]
    finally:
        await client.aclose()
