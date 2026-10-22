"""Real isolated MAS tool execution; opt in explicitly, never simulate Docker."""

import asyncio
import io
import os
import time
from uuid import uuid4

import pytest

import docker
from raac.harness.layer3_execution.docker_execution import (
    DockerExecutionPolicy,
    DockerExecutor,
    DockerRunWorkspace,
)

pytestmark = pytest.mark.skipif(os.getenv("RAAC_DOCKER_TESTS") != "1", reason="Set RAAC_DOCKER_TESTS=1")


@pytest.fixture(scope="module")
def image():
    client = docker.from_env()
    image, _ = client.images.build(fileobj=io.BytesIO(
        b"FROM python:3.11-slim-bookworm\n"
        b"RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*\n"
        b"RUN python -m pip install pytest==8.3.5\n"
        b"RUN mkdir /workspace && chown 1000:1000 /workspace\n"
    ), rm=True, use_config_proxy=False)
    yield image.id
    client.images.remove(image.id)
    client.close()


@pytest.fixture
def workspace(image):
    client = docker.from_env()
    run_id = "mas-fixture-" + uuid4().hex
    with DockerRunWorkspace(client, image, run_id) as session:
        yield session.container
    assert not client.containers.list(all=True, filters={"label": "raac.mas.run_id=" + run_id})
    client.close()


@pytest.mark.asyncio
async def test_repository_cannot_shadow_trusted_helpers(workspace):
    from raac.harness.layer3_execution.tools.docker_workspace import (
        DockerWorkspaceTools,
    )

    workspace.exec_run(["python", "-c", "open('/workspace/json.py','w').write(\"print('[]'); raise SystemExit(0)\"); open('/workspace/hidden.py','w').write('VALUE=1')"], user="1000:1000")
    executor = DockerExecutor(workspace, DockerExecutionPolicy())
    tools = DockerWorkspaceTools("fixture", executor)
    assert set(tools.baseline_files) == {"json.py", "hidden.py"}
    result = await executor.run_command("/workspace", command=["/bin/echo", "trusted-wrapper"])
    assert result["exit_code"] == 0 and result["stdout"].strip() == "trusted-wrapper"
    result = await executor.run_command("/workspace", command=["/bin/echo", "safe-env"], env={"PATH": "/workspace", "PYTHONPATH": "/workspace"})
    assert result["exit_code"] == 0 and result["stdout"].strip() == "safe-env"


@pytest.mark.asyncio
async def test_command_really_runs_in_restricted_container(workspace, monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "host-only-fixture-secret")
    executor = DockerExecutor(workspace, DockerExecutionPolicy())
    result = await executor.run_command("/workspace", command=["python", "-c",
        "import os; print(os.getuid()); print(os.getenv('LLM_API_KEY')); open('value.txt','w').write('container-only')"])
    assert result["status"] == "completed" and result["exit_code"] == 0
    assert result["stdout"].splitlines() == ["1000", "None"]
    assert executor.read_file("/workspace/value.txt", 1024) == b"container-only"
    workspace.reload()
    assert workspace.attrs["HostConfig"]["NetworkMode"] == "none"
    assert not workspace.attrs["Mounts"]


@pytest.mark.asyncio
async def test_unittest_report_comes_from_container(workspace):
    executor = DockerExecutor(workspace, DockerExecutionPolicy())
    command = ["python", "-c", "open('test_value.py','w').write('import unittest\\nclass TestValue(unittest.TestCase):\\n def test_value(self): self.assertEqual(2+2,4)\\n')"]
    assert (await executor.run_command("/workspace", command=command))["exit_code"] == 0
    report = await executor.run_tests("/workspace", command=["python", "-m", "unittest", "discover"])
    assert report["status"] == "passed", report
    assert report["tests_run"] == 1


@pytest.mark.asyncio
async def test_timeout_kills_real_container(workspace):
    executor = DockerExecutor(workspace, DockerExecutionPolicy(timeout_seconds=0.2))
    result = await executor.run_command("/workspace", command=["python", "-c", "import time; time.sleep(30)"])
    assert result["status"] == "error" and result["error_kind"] == "TimeoutError"
    workspace.reload()
    assert workspace.status != "running"


@pytest.mark.asyncio
async def test_streaming_output_precedes_completion(workspace):
    events = []
    executor = DockerExecutor(workspace, DockerExecutionPolicy(), trace=lambda event, data: events.append((event, data)))
    task = asyncio.create_task(executor.run_command("/workspace", command=["python", "-u", "-c",
        "import time; print('started',flush=True); time.sleep(2)"]))
    deadline = time.monotonic() + 1.5
    while not events and time.monotonic() < deadline:
        await asyncio.sleep(0.02)
    assert events and not task.done()
    assert (await task)["exit_code"] == 0


@pytest.mark.asyncio
async def test_file_tools_and_shell_share_container_source(workspace):
    from raac.harness.layer3_execution.tools.docker_workspace import (
        DockerWorkspaceTools,
    )

    executor = DockerExecutor(workspace, DockerExecutionPolicy())
    tools = DockerWorkspaceTools("Fix value", executor)
    tools.edit_file(role="Coder", path="value.py", content="VALUE = 1\n")
    assert tools.read_file("value.py")["content"] == "VALUE = 1\n"
    tools.active_role = "Coder"
    result = await tools.run_command(command=["python", "-c", "open('new.py','w').write('NEW = 2\\n')"])
    assert result["exit_code"] == 0
    assert tools.coder_paths == {"value.py", "new.py"}
    assert tools.list_files()["total"] == 2
    tools.delete_file(role="Coder", path="value.py")
    assert "value.py" not in tools.readable


@pytest.mark.asyncio
async def test_container_environment_uses_container_interpreter(workspace):
    from raac.harness.layer3_execution.tools.docker_workspace import (
        DockerWorkspaceTools,
    )

    executor = DockerExecutor(workspace, DockerExecutionPolicy())
    tools = DockerWorkspaceTools("Fix value", executor)
    tools.edit_file(role="Tester", path="test_value.py", content="import unittest\nclass TestValue(unittest.TestCase):\n def test_ok(self): self.assertTrue(True)\n")
    configured = await tools.environment.configure(python_executable="python", cwd=".",
        test_command=["python", "-m", "unittest", "discover"], check_command=["python", "-c", "import unittest"])
    assert configured["ready"] is True
    assert configured["configuration"]["python_executable"].startswith("/usr/")
    tools.active_role = "Tester"
    report = await tools.run_tests()
    assert report["status"] == "passed", report


@pytest.mark.asyncio
async def test_source_symlink_cannot_be_read_as_a_file(workspace):
    from raac.harness.layer3_execution.tools.docker_workspace import (
        DockerWorkspaceTools,
    )

    executor = DockerExecutor(workspace, DockerExecutionPolicy())
    tools = DockerWorkspaceTools("Fix value", executor)
    await executor.run_command("/workspace", command=["python", "-c", "import os; os.symlink('/etc/passwd','escape.py')"])
    with pytest.raises(ValueError):
        tools.read_file("escape.py")


@pytest.mark.asyncio
async def test_complete_chain_with_http_mock_uses_real_container_tools(workspace, tmp_path, monkeypatch):
    import json
    from pathlib import Path

    import httpx
    import yaml

    import raac.benchmark.chain_runner as module
    from raac.benchmark.dataset import Task
    from raac.harness.layer3_execution.tools.docker_workspace import (
        DockerWorkspaceTools,
    )
    from raac.llm.config import ModelProfile, load_llm_config
    from raac.registry import load_registry

    root = Path(__file__).resolve().parents[2]
    executor = DockerExecutor(workspace, DockerExecutionPolicy())
    await executor.run_command("/workspace", command=["python", "-c",
        "open('solution.py','w').write('VALUE = 0\\n'); open('test_value.py','w').write('import unittest\\nfrom solution import VALUE\\nclass TestValue(unittest.TestCase):\\n def test_value(self): self.assertEqual(VALUE, 1)\\n')"])
    tools = DockerWorkspaceTools("Make VALUE equal 1", executor)
    registry = load_registry(root / "registry")
    config = load_llm_config(root / "configs/llm.yaml")
    config.profiles = {r.model.ref: ModelProfile(model="mock") for r in registry.roles.values()}
    counts = {}
    patch = "--- a/solution.py\n+++ b/solution.py\n@@ -1 +1 @@\n-VALUE = 0\n+VALUE = 1\n"

    def handler(request):
        payload = json.loads(request.content)
        messages = payload["messages"]
        system = messages[0]["content"].replace("**", "")
        role = next(name for name in ("Environment", "Analyst", "Planner", "Coder", "Tester", "Reviewer") if system.startswith("You are the " + name))
        counts[role] = counts.get(role, 0) + 1
        work = json.loads(messages[1]["content"])
        assert work["execution_environment"]["mode"] == "docker"
        assert not work["execution_environment"]["python_executable"].startswith("C:")
        calls = None
        if role == "Environment" and counts[role] == 1:
            calls = [{"id": "env", "type": "function", "function": {"name": "configure_environment", "arguments": json.dumps({
                "python_executable": "python", "cwd": ".", "test_command": ["python", "-m", "unittest", "discover"],
                "check_command": ["python", "-c", "import unittest"]})}}]
            output = None
        elif role == "Environment":
            output = {"status": "ready", "summary": "Verified container", "blocking_issues": []}
        elif role == "Analyst":
            output = {"summary": "Fix value", "requirements": [{"id": "R1", "description": "Set value to one"}], "acceptance_criteria": ["Value equals one"]}
        elif role == "Planner":
            output = {"steps": [{"id": 1, "description": "Change value", "files": ["solution.py"], "depends_on": [], "ac_ids": ["AC1"]}], "covered_ac_ids": ["AC1"]}
        elif role == "Coder" and counts[role] == 1:
            calls = [{"id": "write", "type": "function", "function": {"name": "edit_file", "arguments": json.dumps({"path": "solution.py", "content": "VALUE = 1\n"})}}]
            output = None
        elif role == "Coder":
            output = patch
        elif role == "Tester":
            output = {"tests_to_run": ["test_value.TestValue.test_value"], "results": [{"test": "test_value.TestValue.test_value", "status": "passed", "details": "Observed container test"}], "verdict": "pass", "covered_ac_ids": ["AC1"]}
        else:
            output = {"decision": "approve", "summary": "Correct value", "comments": [], "covered_ac_ids": ["AC1"]}
        message = {"role": "assistant", "content": output if isinstance(output, str) else json.dumps(output) if output else None}
        if calls:
            message["tool_calls"] = calls
        return httpx.Response(200, json={"model": "mock", "choices": [{"message": message, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 1, "prompt_tokens_details": {"cached_tokens": 0}}})

    original = module.LLMClient
    monkeypatch.setattr(module, "LLMClient", lambda *args, **kwargs: original(*args, transport=httpx.MockTransport(handler), **kwargs))
    result = await module.run_chain(Task("fixture__repo-1", "fixture/repo", "https://github.com/fixture/repo", "Fix value", "Make VALUE equal 1", "a" * 40),
        run_id="benchmark-chain-fixture", workspace=tools, registry=registry, llm_config=config,
        harness=yaml.safe_load((root / "configs/harness.yaml").read_text()),
        level_config=yaml.safe_load((root / "configs/levels/l3_collaboration.yaml").read_text()), output_dir=tmp_path)
    assert result["verdict"] == "PASS", result
    assert set(counts) == {"Environment", "Analyst", "Planner", "Coder", "Tester", "Reviewer"}
    assert result["usage"]["total_tokens"] == sum(counts.values()) * 11
    artifact = tools.export_candidate(tmp_path / "candidate.patch")
    assert artifact["changed_paths"] == ["solution.py"]

    # Reuse the exact production evaluator, but seed a separate image with pristine source.
    from raac.harness.layer3_execution.sandbox.docker_sandbox import DockerSandbox
    from raac.harness.layer3_execution.sandbox.evaluation import grade, payload

    client = workspace.client
    image, _ = client.images.build(fileobj=io.BytesIO((
        "FROM " + workspace.image.id + "\n"
        "RUN python -c \"open('/workspace/solution.py','w').write('VALUE = 0\\\\n')\"\n"
        "RUN chown -R 1000:1000 /workspace\n"
    ).encode()), rm=True, use_config_proxy=False)
    try:
        hidden_patch = "--- /dev/null\n+++ b/test_official.py\n@@ -0,0 +1,5 @@\n+from solution import VALUE\n+def test_fix():\n+    assert VALUE == 1\n+def test_preserve():\n+    assert 2 + 2 == 4\n"
        row = {"repo": "fixture/repo", "FAIL_TO_PASS": ["test_official.py::test_fix"],
               "PASS_TO_PASS": ["test_official.py::test_preserve"], "test_patch": hidden_patch}
        profile = {"test_command": ["python", "-m", "pytest"]}
        evaluation = {"test_patch": hidden_patch, "selectors": payload(row, profile),
            "runner_source": (root / "src/raac/harness/layer3_execution/sandbox/issue_runner.py").read_bytes()}
        sandbox = DockerSandbox(client=client)
        command = ["python", "/tmp/raac-issue-runner.py", "/tmp/raac-selectors.json"]
        baseline = await asyncio.to_thread(sandbox.execute, image, command, tmp_path, "baseline", profile=profile, evaluation=evaluation)
        candidate = await asyncio.to_thread(sandbox.execute, image, command, tmp_path, "candidate", (tmp_path / "candidate.patch").read_bytes(), profile=profile, evaluation=evaluation)
        assert grade(row, baseline, baseline=True)["valid"], baseline
        assert grade(row, candidate)["valid"], candidate
        assert "test_official.py" not in tools.readable
        assert executor.read_file("/workspace/solution.py", 1024) == b"VALUE = 1\n"
        assert not client.containers.list(all=True, filters={"label": "raac=" + tmp_path.name})
        assert not client.volumes.list(filters={"label": "raac=" + tmp_path.name})
    finally:
        client.images.remove(image.id)
