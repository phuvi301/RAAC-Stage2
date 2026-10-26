"""Use an already installed image, network disabled; never build/pull or call a provider."""

import json
import os
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

import docker
from raac.benchmark.harness_config import load_harness
from raac.harness.layer2_core.context_manager import RoleContextPolicy, prepare_messages
from raac.harness.layer2_core.context_metrics import request_metrics
from raac.harness.layer3_execution.docker_execution import (
    DockerExecutionPolicy,
    DockerExecutor,
    DockerRunWorkspace,
)
from raac.harness.layer3_execution.tool_orchestrator import ToolOrchestrator
from raac.harness.layer3_execution.tools.docker_workspace import DockerWorkspaceTools
from raac.llm.client import LLMClient
from raac.llm.config import LLMConfig
from raac.registry import load_registry

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(not os.getenv("RAAC_LIFECYCLE_IMAGE"), reason="Set RAAC_LIFECYCLE_IMAGE to an installed image")


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["baseline", "compact", "filtered", "concise"])
async def test_same_pytest_fixture_with_http_mock_and_raw_detail(tmp_path, name):
    connection = docker.from_env()
    container = None
    client = None
    try:
        image = connection.images.get(os.environ["RAAC_LIFECYCLE_IMAGE"])
        options = DockerRunWorkspace.options(image.id, "tokens-" + uuid4().hex)
        container = connection.containers.create(**options, tmpfs={"/workspace": "uid=1000,gid=1000,mode=0755"})
        container.start()
        source = "def test_example():\n    print(chr(10) + (chr(46) * 80 + chr(10)) * 50)\n    assert True\n"
        setup = container.exec_run(["python", "-c", "open('/workspace/test_example.py','w').write(" + repr(source) + ")"], user="1000:1000")
        assert setup.exit_code == 0
        workspace = DockerWorkspaceTools("Run offline fixture", DockerExecutor(container, DockerExecutionPolicy()))
        tools = ToolOrchestrator(workspace)
        registry = load_registry(ROOT / "registry")
        context = {"run_id": "fixture", "agent_id": "coder", "task_id": "fixture", "level": "L3",
                   "registry_version": registry.version, "prompt_version": "fixture", "provenance": {}}
        command = ["python", "-m", "pytest", "-q", "-s", "test_example.py"]
        observed = await tools.execute(registry.get("Coder"), "run_commands", {"commands": [{"command": command}]}, context=context)
        child = observed["result"]["results"][0]["observation"]["result"]
        assert child["exit_code"] == 0 and "1 passed" in child["stdout"]
        _, harness = load_harness(ROOT, {"harness_config": f"configs/experiments/token_reduction/{name}.yaml"})
        policy = RoleContextPolicy.model_validate(harness["context"])
        messages = [{"role": "system", "content": "fixture rules"}, {"role": "user", "content": "fixture task"},
                    {"role": "assistant", "content": None, "tool_calls": [{"id": "batch", "type": "function", "function": {
                        "name": "run_commands", "arguments": json.dumps({"commands": [{"command": command}]})}}]},
                    {"role": "tool", "tool_call_id": "batch", "content": json.dumps(observed)}]
        prepared, _ = prepare_messages(messages, policy)
        seen = []
        def respond(request):
            seen.append(json.loads(request.content))
            return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": "done"}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 2}})
        client = LLMClient(LLMConfig(model="offline-fixture"), log_path=tmp_path / "llm.jsonl", transport=httpx.MockTransport(respond))
        await client.complete(prepared, context=context | {"context_metrics": {"before": request_metrics(messages, []), "after": request_metrics(prepared, [])}})
        assert seen[0]["messages"] == prepared
        page = await tools.execute(registry.get("Coder"), "read_tool_result", {
            "observation_ref": child["observation_ref"], "path": ["stdout"], "offset": 20, "limit": 100}, context=context)
        assert page["result"]["value"] == child["stdout"][20:120]
        structured = await tools.execute(registry.get("Tester"), "run_tests", {"command": command}, context=context)
        assert structured["result"]["tests_run"] == 1 and structured["result"]["failed"] == 0
        if name in {"filtered", "concise"}:
            assert sum(len(m.get("content") or "") for m in prepared) < sum(len(m.get("content") or "") for m in messages)
    finally:
        if client is not None:
            await client.aclose()
        if container is not None:
            container.remove(force=True)
        connection.close()
