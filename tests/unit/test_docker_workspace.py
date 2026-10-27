"""Remote snapshots are untrusted file payloads, not host filesystem paths."""

import base64
from types import SimpleNamespace

import pytest

from raac.harness.layer3_execution.tools.docker_workspace import (
    DockerWorkspaceTools,
    decode_snapshot,
)


def entry(path="value.py", content=b"VALUE = 1\n", **extra):
    return {"path": path, "content": base64.b64encode(content).decode(), "mode": 420, **extra}


def test_decode_remote_snapshot_retains_source_bytes():
    files, modes = decode_snapshot([entry()], max_files=2, max_file_bytes=20, max_repo_bytes=20)
    assert files == {"value.py": b"VALUE = 1\n"}
    assert modes == {"value.py": 420}


@pytest.mark.parametrize("entries", [[entry("../host")], [entry("/etc/passwd")],
    [entry(".env")], [entry(), entry()], [entry(content=b"a" * 21)],
    [entry(content=b"a" * 11), entry("other.py", b"b" * 11)]])
def test_invalid_snapshot_is_rejected(entries):
    with pytest.raises(ValueError):
        decode_snapshot(entries, max_files=2, max_file_bytes=20, max_repo_bytes=20)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["passed", "failed", "error"])
async def test_test_execution_cannot_overwrite_coder_source(status):
    tools = object.__new__(DockerWorkspaceTools)
    snapshots = iter([{"value.py": b"1"}, {"value.py": b"999"}])
    tools._snapshot = lambda: next(snapshots)
    tools.current_modes = {"value.py": 420}
    tools.baseline_files = {"value.py": b"1"}
    tools.coder_paths, tools.tester_paths = {"value.py"}, set()
    tools.executor = SimpleNamespace(trace=None)

    async def execute():
        return {"status": status}

    with pytest.raises(PermissionError, match="Tester"):
        await tools._mutating_command("Tester", execute)


@pytest.mark.parametrize("role,path", [("Coder", "tests/test_value.py"), ("Tester", "tests/test_existing.py"), ("Tester", "new_source.py")])
def test_benchmark_roles_keep_tests_separate(role, path):
    tools = object.__new__(DockerWorkspaceTools)
    tools.baseline_files = {"tests/test_existing.py": b"assert True\n"}
    tools.coder_paths, tools.tester_paths = set(), set()
    with pytest.raises(PermissionError):
        tools._can_write(role, path)


@pytest.mark.asyncio
async def test_coder_shell_cannot_add_test_to_candidate():
    tools = object.__new__(DockerWorkspaceTools)
    snapshots = iter([{}, {"tests/test_value.py": b"assert True\n"}])
    tools._snapshot = lambda: next(snapshots)
    tools.current_modes = {}
    tools.baseline_files, tools.coder_paths, tools.tester_paths = {}, set(), set()
    tools.executor = SimpleNamespace(trace=None)

    async def execute():
        return {"status": "completed"}

    with pytest.raises(PermissionError, match="Coder"):
        await tools._mutating_command("Coder", execute)
