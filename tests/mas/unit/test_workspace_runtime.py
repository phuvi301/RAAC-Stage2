"""Environment artifacts must not enter source snapshots or hide source errors."""

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from raac.collaboration.levels.l3_collaboration import ChainLimits, ChainNodes, WorkItem, initial_chain_state
from raac.common.errors import WorkspaceSnapshotError
from raac.harness.layer1_lifecycle import AgentRunner
from raac.harness.layer2_core.context_manager import RoleContextPolicy
from raac.harness.layer3_execution.tool_orchestrator import ToolOrchestrator
from raac.harness.layer3_execution.tools.paths import repo_path
from raac.harness.layer3_execution.tools.workspace import WorkspaceTools
from raac.llm.client import LLMClient
from raac.llm.config import LLMConfig
from raac.registry.loader import load_registry


ROOT = Path(__file__).resolve().parents[3]
RUNTIME_FILES = (
    "xarray/.venv38/Lib/site-packages/numpy/core.pyd",
    "xarray/.venv39/Lib/site-packages/numpy/core.pyd",
    "xarray/.venv-3.12/Lib/site-packages/numpy/core.pyd",
    "xarray/.python38/python38.dll",
    "xarray/.python39/python39.dll",
    "xarray/.python-3.9.13/python.exe",
    "xarray/.python-3.8.10-installer.exe",
    "xarray/.python-3.9.13-installer.exe",
)


@pytest.fixture
def project(tmp_path):
    (tmp_path / "repo/xarray").mkdir(parents=True)
    (tmp_path / "requirement").mkdir()
    (tmp_path / "requirement/user_chat.txt").write_text("Fix the weighted mean", encoding="utf-8")
    (tmp_path / "repo/xarray/app.py").write_text("value = 1\n", encoding="utf-8")
    return tmp_path


def large_file(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as stream:
        stream.seek(WorkspaceTools.max_file_bytes)
        stream.write(b"x")


@pytest.mark.parametrize("relative", RUNTIME_FILES)
def test_runtime_excluded_before_and_after_environment_setup(project, relative):
    workspace = WorkspaceTools(project, None)
    baseline_ref = workspace.baseline_ref
    artifact = workspace.root / relative
    large_file(artifact)
    # The Environment creates these after the baseline, before Coder lists files.
    assert list(workspace._snapshot()) == ["xarray/app.py"]
    assert workspace.list_files()["files"] == [
        {"path": "xarray/app.py", "bytes": len(workspace.baseline_files["xarray/app.py"])}]
    assert workspace.search_repo("value")["matches"][0]["path"] == "xarray/app.py"
    # A later run must also work with the installed runtimes still present.
    assert WorkspaceTools(project, None).baseline_ref == baseline_ref
    assert artifact.exists()  # Exclusion never removes an installed environment.
    with pytest.raises(ValueError, match="excluded path"):
        repo_path(relative)
    with pytest.raises(ValueError, match="excluded path"):
        workspace.edit_file(role="Coder", path=relative, content="overwrite runtime")
    workspace.edit_file(role="Coder", path="xarray/app.py", content="value = 2\n")
    patch = workspace.current_patch()
    assert "+value = 2" in patch and relative not in patch


@pytest.mark.parametrize("relative", (
    "xarray/.python-tools/helper.py", "xarray/.venv_config/settings.py",
    "xarray/.venv39_notes.py", "xarray/python39/helper.py", "xarray/venv_helpers.py",
))
def test_similarly_named_source_is_preserved(project, relative):
    source = project / "repo" / relative
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("source_marker = True\n", encoding="utf-8")
    workspace = WorkspaceTools(project, None)
    assert relative in workspace.baseline_files
    assert workspace.read_file(relative)["content"] == "source_marker = True\n"


def test_real_large_source_still_fails_with_specific_code(project):
    large_file(project / "repo/xarray/large_source.dat")
    with pytest.raises(WorkspaceSnapshotError) as caught:
        WorkspaceTools(project, None)
    assert caught.value.code == "workspace_file_too_large"


def test_python_candidates_are_hints_and_do_not_scan_runtime_packages(project):
    workspace = WorkspaceTools(project, None)
    runtimes = ("xarray/.python38/python.exe", "xarray/.venv39/Scripts/python.exe")
    for relative in (*runtimes, ".git/cache/.venv/Scripts/python.exe", "xarray/python39/python.exe"):
        executable = workspace.root / relative
        executable.parent.mkdir(parents=True, exist_ok=True)
        executable.write_text("unverified executable fixture", encoding="utf-8")
    large_file(workspace.root / "xarray/.venv39/Lib/site-packages/large.dll")
    candidates = workspace.python_candidates()
    assert set(candidates) == {str((workspace.root / name).absolute()) for name in runtimes}
    assert len(workspace.python_candidates(limit=1)) == 1
    assert not workspace.environment.ready
    assert all(".venv39" not in name for name in workspace._snapshot())


def test_python_candidates_do_not_follow_runtime_symlinks(project, monkeypatch):
    workspace = WorkspaceTools(project, None)
    executable = workspace.root / "xarray/.venv39/Scripts/python.exe"
    executable.parent.mkdir(parents=True)
    executable.write_text("unverified executable fixture", encoding="utf-8")
    monkeypatch.setattr(Path, "is_symlink", lambda path: path.name == ".venv39")
    assert workspace.python_candidates() == []


@pytest.mark.parametrize("limit", ("max_files", "max_repo_bytes"))
def test_aggregate_snapshot_limits_are_preserved(project, monkeypatch, limit):
    (project / "repo/xarray/second.py").write_text("second = True\n", encoding="utf-8")
    monkeypatch.setattr(WorkspaceTools, limit, 1)
    with pytest.raises(WorkspaceSnapshotError) as caught:
        WorkspaceTools(project, None)
    assert caught.value.code == "workspace_snapshot_limit"


def coder_state(workspace):
    return initial_chain_state(
        WorkItem(run_id="runtime-regression", task_id="task", work_id="task", scope="task",
                 requirement="Fix weighted mean", baseline_ref=workspace.baseline_ref),
        ChainLimits(max_rounds=1, hard_max_rounds=4, timeout_seconds=30.0, agent_timeout_seconds=10.0))


def runner_for(workspace, tmp_path, handler):
    registry = load_registry(ROOT / "registry")
    for key, spec in registry.roles.items():
        registry.roles[key] = spec.model_copy(update={"model": spec.model.model_copy(update={"ref": "default"})})
    client = LLMClient(LLMConfig(model="test-model"), log_path=tmp_path / "trace.jsonl",
                       trace_content=True, transport=httpx.MockTransport(handler))
    runner = AgentRunner(registry, client, ToolOrchestrator(workspace), provenance={},
                             context_policy=RoleContextPolicy(enabled=False))
    return runner, client


def test_coder_starts_after_environment_artifacts_with_full_context(project, tmp_path):
    workspace = WorkspaceTools(project, None)
    for relative in RUNTIME_FILES:
        large_file(workspace.root / relative)
    payloads = []

    def handler(request):
        payloads.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": ""},
                                                     "finish_reason": "stop"}],
                                         "usage": {"prompt_tokens": 10, "completion_tokens": 1}})

    runner, client = runner_for(workspace, tmp_path, handler)

    async def run():
        try:
            return await ChainNodes(runner).coder(coder_state(workspace))
        finally:
            await client.aclose()

    result = asyncio.run(run())
    assert result["next_node"] == "tester"
    assert len(payloads) == 1
    request = json.loads(payloads[0]["messages"][1]["content"])
    assert request["repository_files"] == ["xarray/app.py"]
    assert client.usage.snapshot(run_id="runtime-regression")["calls"] == 1


@pytest.mark.parametrize("code", ("workspace_file_too_large", "workspace_snapshot_limit"))
def test_preflight_errors_are_logged_without_calling_model_or_claiming_invalid_output(
        project, tmp_path, monkeypatch, code):
    workspace = WorkspaceTools(project, None)
    if code == "workspace_file_too_large":
        large_file(workspace.root / "xarray/large_source.dat")
    else:
        monkeypatch.setattr(workspace, "max_repo_bytes", 1)

    def handler(request):
        pytest.fail("Model must not be called after a workspace preflight failure")

    runner, client = runner_for(workspace, tmp_path, handler)

    async def run():
        try:
            return await ChainNodes(runner).coder(coder_state(workspace))
        finally:
            await client.aclose()

    result = asyncio.run(run())
    assert result["stop_reason"] == code
    assert result["error"] == {"node": "coder", "kind": code}
    events = [json.loads(line) for line in client.log_path.read_text(encoding="utf-8").splitlines()]
    failure = next(event for event in events if event["event"] == "agent_failed")
    assert failure["code"] == code and failure["stage"] == "workspace_preflight"
    assert not any(event["event"] == "llm_request" for event in events)
