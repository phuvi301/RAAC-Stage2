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
        b"FROM python:3.11-slim-bookworm\nRUN mkdir /workspace && chown 1000:1000 /workspace\n"
    ), rm=True)
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
