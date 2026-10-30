"""Remote snapshots are untrusted file payloads, not host filesystem paths."""

import base64
import json
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

from raac.harness.layer3_execution.tools.docker_workspace import (
    FILE_RPC,
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


def test_symlink_entries_are_kept_apart_from_file_bytes():
    links = {}
    files, modes = decode_snapshot([entry(), {"path": "docs/icon.png", "link": "../static/icon.png"}],
                                   max_files=3, max_file_bytes=20, max_repo_bytes=20, links=links)
    assert files == {"value.py": b"VALUE = 1\n"} and modes == {"value.py": 420}
    assert links == {"docs/icon.png": "../static/icon.png"}


@pytest.mark.parametrize("entries", [
    [{"path": "a", "link": "b"}, {"path": "a", "link": "c"}],
    [{"path": "a", "link": 5}],
    [entry("a"), {"path": "a", "link": "b"}],
    [{"path": "../x", "link": "b"}]])
def test_invalid_symlink_entries_are_rejected(entries):
    with pytest.raises(ValueError):
        decode_snapshot(entries, max_files=3, max_file_bytes=20, max_repo_bytes=20, links={})


def test_symlink_entry_is_rejected_when_the_caller_did_not_ask_for_links():
    with pytest.raises(ValueError):
        decode_snapshot([{"path": "a", "link": "b"}], max_files=3, max_file_bytes=20, max_repo_bytes=20)


@pytest.mark.skipif(os.name != "posix", reason="needs POSIX symlinks and paths")
def test_container_helper_records_inside_links_and_refuses_escapes(tmp_path):
    repo = tmp_path / "workspace"
    (repo / "docs").mkdir(parents=True)
    (repo / "docs" / "real.png").write_bytes(b"x")
    (repo / "docs" / "alias.png").symlink_to("real.png")
    (repo / "docs" / "dir_alias").symlink_to(repo / "docs")
    request = {"operation": "snapshot", "excluded": [], "secrets": [], "max_files": 50,
               "max_file_bytes": 1000, "max_repo_bytes": 10000}

    def run():
        path = tmp_path / "request.json"
        path.write_text(json.dumps(request))
        script = FILE_RPC.replace("root = '/workspace'", "root = " + repr(os.path.realpath(repo)))
        return subprocess.run([sys.executable, "-c", script, str(path)], capture_output=True, text=True)

    done = run()
    assert done.returncode == 0, done.stderr
    found = {item["path"]: item for item in json.loads(done.stdout)}
    assert set(found) == {"docs/real.png", "docs/alias.png", "docs/dir_alias"}
    assert found["docs/alias.png"]["link"] == "real.png" and "content" not in found["docs/alias.png"]
    (repo / "escape").symlink_to(tmp_path)
    assert run().returncode != 0


@pytest.mark.asyncio
async def test_command_cannot_change_symlinks():
    tools = object.__new__(DockerWorkspaceTools)
    snapshots = iter([{}, {}])
    tools.baseline_symlinks = {"docs/icon.png": "../static/icon.png"}

    def snapshot():
        tools.current_symlinks = {"docs/icon.png": "../static/other.png"}
        return next(snapshots)

    tools._snapshot = snapshot
    tools.current_modes = {}
    tools.baseline_files, tools.coder_paths, tools.tester_paths = {}, set(), set()
    tools.executor = SimpleNamespace(trace=None)

    async def execute():
        return {"status": "completed"}

    with pytest.raises(PermissionError, match="symlink"):
        await tools._mutating_command("Coder", execute)
    assert tools.integrity_error == "Symlinks changed"


def test_exported_candidate_rejects_changed_symlinks(tmp_path):
    tools = object.__new__(DockerWorkspaceTools)
    tools.baseline_symlinks = {}
    tools.baseline_files, tools.baseline_modes, tools.coder_paths, tools.tester_paths = {}, {}, set(), set()
    tools.current_modes = {}

    def snapshot():
        tools.current_symlinks = {"new_link": "target"}
        return {}

    tools._snapshot = snapshot
    with pytest.raises(ValueError, match="symlinks changed"):
        tools.export_candidate(tmp_path / "candidate.patch")


def test_generated_test_artifact_directories_are_not_workspace_source():
    from raac.harness.layer3_execution.tools.paths import EXCLUDED_DIRS, excluded_directory
    assert {"result_images", ".eggs"} <= EXCLUDED_DIRS
    assert excluded_directory(".eggs")
    assert excluded_directory("result_images") and excluded_directory("__pycache__")
    assert not excluded_directory("images") and not excluded_directory("baseline_images")


@pytest.mark.parametrize("output,kind,text", [
    (b"Traceback (most recent call last):\nFileNotFoundError: [Errno 2] No such file or directory: '/workspace/x'\n", FileNotFoundError, None),
    (b"Traceback...\nValueError: symlink\n", ValueError, "symlink"),
    (b"Traceback...\nValueError: file too large\n", ValueError, "file too large"),
    (b"Traceback...\nValueError: something with /secret/path\n", ValueError, "Container file operation failed or exceeded its limit"),
    (b"", ValueError, "Container file operation failed or exceeded its limit")])
def test_container_failures_are_typed_without_forwarding_text(output, kind, text):
    from raac.harness.layer3_execution.tools.docker_workspace import container_error
    error = container_error(output)
    assert type(error) is kind
    if text:
        assert str(error) == text


def _environment_tools(baseline):
    tools = object.__new__(DockerWorkspaceTools)
    tools.baseline_files, tools.baseline_modes = dict(baseline), {}
    tools.coder_paths, tools.tester_paths = set(), set()
    tools.environment_generated_paths = set()
    tools.code_edit_count = 0
    tools.current_modes = {}
    tools.executor = SimpleNamespace(trace=None)
    return tools


async def _run_with_snapshots(tools, role, before, after):
    snapshots = iter([before, after])
    tools._snapshot = lambda: next(snapshots)

    async def execute():
        return {"status": "completed"}

    return await tools._mutating_command(role, execute)


@pytest.mark.asyncio
async def test_environment_may_leave_new_files_behind():
    tools = _environment_tools({"app.py": b"1"})
    before = {"app.py": b"1"}
    after = {"app.py": b"1", "result_images/a.png": b"png"}
    assert await _run_with_snapshots(tools, "Environment", before, after) == {"status": "completed"}
    assert tools.environment_generated_paths == {"result_images/a.png"}
    assert not getattr(tools, "integrity_error", None)


@pytest.mark.asyncio
async def test_environment_still_may_not_edit_or_delete_base_source():
    for after in ({"app.py": b"2"}, {}):
        tools = _environment_tools({"app.py": b"1"})
        with pytest.raises(PermissionError) as raised:
            await _run_with_snapshots(tools, "Environment", {"app.py": b"1"}, after)
        assert raised.value.paths == ["app.py"]
        assert tools.integrity_error == "Environment modified source"


@pytest.mark.asyncio
async def test_regenerating_an_environment_artifact_is_not_a_coder_edit():
    tools = _environment_tools({"app.py": b"1"})
    tools.environment_generated_paths = {"result_images/a.png"}
    before = {"app.py": b"1", "result_images/a.png": b"old"}
    after = {"app.py": b"1", "result_images/a.png": b"new"}
    await _run_with_snapshots(tools, "Coder", before, after)
    assert tools.coder_paths == set() and tools.code_edit_count == 0


@pytest.mark.asyncio
async def test_environment_symlinks_join_the_baseline_but_cannot_be_retargeted():
    tools = _environment_tools({"app.py": b"1"})
    tools.baseline_symlinks = {"docs/a.png": "../x.png"}
    snapshot = {"app.py": b"1"}
    seen = iter([{"docs/a.png": "../x.png"}, {"docs/a.png": "../x.png", "tmp/new": "app.py"}])
    tools._snapshot = lambda: (setattr(tools, "current_symlinks", next(seen)) or snapshot)

    async def execute():
        return {"status": "completed"}

    await tools._mutating_command("Environment", execute)
    assert tools.baseline_symlinks == {"docs/a.png": "../x.png", "tmp/new": "app.py"}

    retargeted = iter([{"docs/a.png": "../x.png", "tmp/new": "app.py"}, {"docs/a.png": "../other.png", "tmp/new": "app.py"}])
    tools._snapshot = lambda: (setattr(tools, "current_symlinks", next(retargeted)) or snapshot)
    with pytest.raises(PermissionError):
        await tools._mutating_command("Environment", execute)


@pytest.mark.skipif(os.name != "posix", reason="needs POSIX paths")
def test_container_helper_leaves_cython_output_out_of_the_snapshot(tmp_path):
    repo = tmp_path / "workspace"
    (repo / "pkg").mkdir(parents=True)
    (repo / "pkg" / "fast.c").write_text("/* Generated by Cython 0.29.36 */\n" + "x\n" * 50)
    (repo / "pkg" / "fast.cpp").write_text("/* Generated by Cython 3.0 */\n")
    (repo / "pkg" / "real.c").write_text("int main(void) { return 0; }\n")
    (repo / "pkg" / "mod.py").write_text("x = 1\n")
    request = {"operation": "snapshot", "excluded": [], "secrets": [], "max_files": 50,
               "max_file_bytes": 10000, "max_repo_bytes": 100000}
    path = tmp_path / "request.json"
    path.write_text(json.dumps(request))
    script = FILE_RPC.replace("root = '/workspace'", "root = " + repr(os.path.realpath(repo)))
    done = subprocess.run([sys.executable, "-c", script, str(path)], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    assert {item["path"] for item in json.loads(done.stdout)} == {"pkg/real.c", "pkg/mod.py"}
    (repo / "pkg" / "huge.c").write_text("int x;\n" * 5000)  # hand-written but oversized: still refused
    path.write_text(json.dumps(request))
    refused = subprocess.run([sys.executable, "-c", script, str(path)], capture_output=True, text=True)
    assert refused.returncode != 0


@pytest.mark.asyncio
@pytest.mark.parametrize("after, tainted", [({"app.py": b"1"}, False), ({"app.py": b"1", "junk.txt": b"x"}, True),
                                            (RuntimeError("daemon unavailable"), True)])
async def test_a_failed_command_only_taints_the_run_if_it_changed_the_workspace(after, tainted):
    """A rejected `run_tests` (absolute report_path) used to make every later candidate export fail."""
    tools = _environment_tools({"app.py": b"1"})
    tools.baseline_symlinks = {}
    tools.current_symlinks = {}
    snapshots = iter([{"app.py": b"1"}, after])

    def snapshot():
        value = next(snapshots)
        if isinstance(value, Exception):
            raise value
        return value

    tools._snapshot = snapshot

    async def refused():
        raise ValueError("Invalid repository path")

    with pytest.raises(ValueError):
        await tools._mutating_command("Tester", refused)
    assert bool(getattr(tools, "integrity_error", None)) is tainted


async def _tester_run_tests(monkeypatch, tools, before, after, **kwargs):
    from raac.harness.layer3_execution.tools.workspace import WorkspaceTools

    async def fake_run_tests(self, **_):
        return {"status": "passed"}

    monkeypatch.setattr(WorkspaceTools, "run_tests", fake_run_tests)
    snapshots = iter([before, after])
    tools._snapshot = lambda: next(snapshots)
    tools.test_spec, tools.active_role = None, "Tester"
    return await tools.run_tests(**kwargs)


@pytest.mark.asyncio
async def test_tester_junit_report_inside_repo_is_a_tool_artifact(monkeypatch):
    # A Tester run_tests(report_path="dev_junit.xml") once tainted a passing run: export then failed.
    tools = _environment_tools({"app.py": b"1"})
    before = {"app.py": b"1", "tests/test_dev.py": b"t"}
    after = {**before, "dev_junit.xml": b"<testsuite/>"}
    result = await _tester_run_tests(monkeypatch, tools, before, after,
                                     command=["python", "-m", "pytest"], report_path="dev_junit.xml")
    assert result == {"status": "passed"}
    assert not getattr(tools, "integrity_error", None)
    assert "dev_junit.xml" in tools.environment_generated_paths and tools.tester_paths == set()


@pytest.mark.asyncio
async def test_tester_report_path_may_not_overwrite_base_source(monkeypatch):
    tools = _environment_tools({"app.py": b"1"})
    with pytest.raises(PermissionError):
        await _tester_run_tests(monkeypatch, tools, {"app.py": b"1"}, {"app.py": b"<testsuite/>"},
                                command=["python", "-m", "pytest"], report_path="app.py")
    assert tools.integrity_error == "Tester modified baseline tests"
