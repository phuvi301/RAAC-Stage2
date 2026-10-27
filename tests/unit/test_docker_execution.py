"""Executor boundaries: host env never enters task containers and faults fail closed."""

import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from raac.harness.layer3_execution.docker_execution import (
    DockerExecutionPolicy,
    DockerExecutor,
    DockerRunWorkspace,
)


class API:
    def __init__(self, chunks=(), exit_code=0):
        self.chunks = chunks
        self.exit_code = exit_code
        self.request = None

    def exec_create(self, **kwargs):
        self.request = kwargs
        return {"Id": "exec-fixture"}

    def exec_start(self, *args, **kwargs):
        return iter(self.chunks)

    def exec_inspect(self, identifier):
        return {"ExitCode": self.exit_code}


class Container:
    id = "fixture-container"

    def __init__(self, api):
        self.client = SimpleNamespace(api=api)
        self.killed = False

    def kill(self):
        self.killed = True


def test_container_options_have_no_host_mount_or_credentials():
    options = DockerRunWorkspace.options("image@sha256:fixture", "benchmark-fixture")
    assert options["network_mode"] == "none"
    assert options["user"] == "1000:1000"
    assert options["cap_drop"] == ["ALL"]
    assert options["mem_limit"] == "4g"
    assert not {"volumes", "mounts", "environment", "privileged"} & options.keys()


@pytest.mark.parametrize("stage", ["create", "start", "start-and-cleanup"])
def test_workspace_startup_keeps_primary_error_and_removes_owned_container(stage):
    client, container = Mock(), Mock()
    primary = RuntimeError("primary startup error")
    if stage == "create":
        client.containers.create.side_effect = primary
    else:
        client.containers.create.return_value = container
        container.start.side_effect = primary
        if stage == "start-and-cleanup":
            container.remove.side_effect = RuntimeError("cleanup error")
    session = DockerRunWorkspace(client, "fixture", "fixture")
    with pytest.raises(RuntimeError) as caught:
        session.__enter__()
    assert caught.value is primary
    if stage != "create":
        container.remove.assert_called_once_with(force=True)


@pytest.mark.parametrize("primary", [None, RuntimeError("agent failure")])
def test_cleanup_reports_failure_without_overwriting_primary_error(primary):
    session = DockerRunWorkspace(Mock(), "fixture", "fixture")
    session.container = Mock()
    session.container.remove.side_effect = RuntimeError("cleanup failure")
    if primary is None:
        with pytest.raises(RuntimeError, match="cleanup failure"):
            session.__exit__(None, None, None)
    else:
        session.__exit__(RuntimeError, primary, None)
        assert session.cleanup_failed


@pytest.mark.asyncio
async def test_command_returns_observed_exit_code_and_separate_streams(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "host-only-secret")
    api = API([(b"hello", None), (None, b"problem")], exit_code=7)
    executor = DockerExecutor(Container(api), DockerExecutionPolicy())
    result = await executor.run_command("/workspace", command=["python", "-c", "raise SystemExit(7)"])
    assert result["exit_code"] == 7
    assert result["status"] == "completed"
    assert result["stdout"] == "hello" and result["stderr"] == "problem"
    assert "host-only-secret" not in str(api.request)
    assert api.request["user"] == "1000:1000"


@pytest.mark.asyncio
async def test_output_limit_stops_container_and_bounds_observation():
    api = API([(b"a" * 2048, None)])
    container = Container(api)
    executor = DockerExecutor(container, DockerExecutionPolicy(max_output_bytes=1024))
    result = await executor.run_command("/workspace", command=["yes"])
    assert result["status"] == "error" and result["error_kind"] == "OutputLimitError"
    assert len(result["stdout"]) <= 1024 and container.killed


@pytest.mark.asyncio
async def test_invalid_cwd_is_rejected_before_docker_exec():
    api = API()
    executor = DockerExecutor(Container(api), DockerExecutionPolicy())
    with pytest.raises(ValueError):
        await executor.run_command("/workspace", command=["true"], cwd="../host")
    assert api.request is None


@pytest.mark.asyncio
async def test_unlimited_role_cannot_bypass_run_deadline():
    executor = DockerExecutor(Container(API()), DockerExecutionPolicy(), deadline=0.0)
    with pytest.raises(TimeoutError):
        await executor.run_command("/workspace", command=["true"], unlimited=True)


@pytest.mark.asyncio
async def test_cancellation_kills_container_before_propagating(monkeypatch):
    container = Container(API())
    executor = DockerExecutor(container, DockerExecutionPolicy())

    async def cancel(*args, **kwargs):
        raise asyncio.CancelledError

    monkeypatch.setattr(executor, "_collect", cancel)
    with pytest.raises(asyncio.CancelledError):
        await executor.run_command("/workspace", command=["sleep", "20"])
    assert container.killed
