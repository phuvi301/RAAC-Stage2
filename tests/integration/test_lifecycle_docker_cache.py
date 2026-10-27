"""Offline cache and resume checks in an installed, isolated Docker image."""

import os
from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4

import pytest
import test_mas_docker as existing

import docker
from raac.harness.layer3_execution.docker_execution import (
    DockerExecutionPolicy,
    DockerExecutor,
    DockerRunWorkspace,
)
from raac.harness.layer3_execution.tool_orchestrator import ToolOrchestrator
from raac.harness.layer3_execution.tools.docker_workspace import DockerWorkspaceTools
from raac.registry.loader import load_registry

pytestmark = pytest.mark.skipif(not os.getenv("RAAC_LIFECYCLE_IMAGE"), reason="Set RAAC_LIFECYCLE_IMAGE to an installed image")
test_resume = existing.test_lifecycle_resume_on_same_container_preserves_baseline_and_attribution
test_trusted_bootstrap = existing.test_repository_cannot_shadow_trusted_helpers


@pytest.mark.asyncio
async def test_pytest_reports_do_not_poison_integrity_or_overwrite_source(workspace, tmp_path):
    executor = DockerExecutor(workspace, DockerExecutionPolicy())
    setup = "from pathlib import Path; Path('value.py').write_text('VALUE=42\\n'); Path('report.xml').write_text('preserve existing'); Path('test_safe.py').write_text('def test_safe(): assert True\\n')"
    assert (await executor.run_command("/workspace", command=["python", "-c", setup]))["exit_code"] == 0
    tools = DockerWorkspaceTools("fixture", executor)
    tools.active_role = "Tester"
    result = await tools.run_tests(command=["python", "-m", "pytest", "test_safe.py", "--junitxml", "value.py", "--junit-xml=report.xml"],
                                   report_path="/tmp/report.xml")
    assert result["status"] == "passed", result
    assert result["tests_run"] == 1 and "temporary" in result["report_note"]
    assert tools._snapshot() == tools.baseline_files
    assert not getattr(tools, "integrity_error", None)
    tools.export_candidate(tmp_path / "candidate.patch")
    assert (tmp_path / "candidate.patch").read_bytes() == b""


@pytest.fixture
def workspace():
    connection = docker.from_env()
    container = None
    try:
        image = connection.images.get(os.environ["RAAC_LIFECYCLE_IMAGE"])
        options = DockerRunWorkspace.options(image.id, "lifecycle-test-" + uuid4().hex)
        container = connection.containers.create(**options, tmpfs={"/workspace": "uid=1000,gid=1000,mode=0755"})
        container.start()
        yield container
    finally:
        if container is not None:
            container.remove(force=True)
        connection.close()


@pytest.mark.asyncio
async def test_bad_cwd_then_corrected_command_can_export_candidate(workspace, tmp_path):
    tools = DockerWorkspaceTools("fixture", DockerExecutor(workspace, DockerExecutionPolicy()))
    tools.active_role = "Coder"
    with pytest.raises(ValueError):
        await tools.run_command(command=["python", "-c", "print('unused')"], cwd="/workspace")
    assert not getattr(tools, "integrity_error", None)
    assert (await tools.run_command(command=["python", "-c", "open('value.py','w').write('VALUE=1\\n')"]))["exit_code"] == 0
    output = tmp_path / "candidate.patch"
    tools.export_candidate(output)
    assert "+VALUE=1" in output.read_text()


@pytest.mark.asyncio
async def test_unittest_keeps_user_dependencies_with_isolated_bootstrap(workspace):
    executor = DockerExecutor(workspace, DockerExecutionPolicy())
    setup = "import pathlib,site; p=pathlib.Path(site.getusersitepackages()); p.mkdir(parents=True,exist_ok=True); (p/'raac_dependency.py').write_text('VALUE=42\\n'); pathlib.Path('test_dependency.py').write_text('import unittest,raac_dependency\\nclass Tests(unittest.TestCase):\\n def test_dependency(self): self.assertEqual(raac_dependency.VALUE,42)\\n')"
    env = {"PYTHONUSERBASE": "/tmp/raac-dependencies"}
    assert (await executor.run_command("/workspace", command=["python", "-c", setup], env=env))["exit_code"] == 0
    result = await executor.run_tests("/workspace", command=["python", "-m", "unittest", "discover"], env=env)
    assert result["status"] == "passed", result
    assert result["tests_run"] == 1


@pytest.mark.asyncio
async def test_cached_fingerprint_refreshes_after_file_shell_and_external_changes(workspace):
    tools = DockerWorkspaceTools("fixture", DockerExecutor(workspace, DockerExecutionPolicy()))
    snapshot = Mock(wraps=tools._snapshot)
    tools._snapshot = snapshot
    first = tools.snapshot_runtime(refresh=False)
    assert tools.snapshot_runtime(refresh=False) == first
    assert snapshot.call_count == 1
    tools.edit_file(role="Coder", path="value.py", content="VALUE = 1\n")
    changed = tools.snapshot_runtime(refresh=False)
    assert changed["fingerprint"] != first["fingerprint"]
    tools.active_role = "Coder"
    result = await tools.run_command(command=["python", "-c", "open('value.py','w').write('VALUE = 2\\n')"])
    assert result["exit_code"] == 0
    current = tools.snapshot_runtime(refresh=False)
    assert current["fingerprint"] != changed["fingerprint"]
    external = workspace.exec_run(["python", "-c", "open('/workspace/value.py','w').write('VALUE = 3\\n')"], user="1000:1000")
    assert external.exit_code == 0
    with pytest.raises(ValueError, match="workspace_mismatch"):
        tools.restore_runtime(current)


@pytest.mark.asyncio
async def test_read_only_shell_still_cannot_mutate_source(workspace):
    tools = DockerWorkspaceTools("fixture", DockerExecutor(workspace, DockerExecutionPolicy()))
    tools.active_role = "Analyst"
    tools.snapshot_runtime(refresh=False)
    with pytest.raises(PermissionError, match="Read-only role"):
        await tools.run_command(command=["python", "-c", "open('forbidden.py','w').write('VALUE = 1\\n')"])
    with pytest.raises(ValueError, match="attribution"):
        tools.export_candidate(None)


@pytest.mark.asyncio
async def test_verified_command_snapshot_serves_evidence_and_checkpoint(workspace):
    tools = DockerWorkspaceTools("fixture", DockerExecutor(workspace, DockerExecutionPolicy()))
    tools.edit_file(role="Coder", path="value.py", content="VALUE = 1\n")
    snapshot = Mock(wraps=tools._snapshot)
    tools._snapshot = snapshot
    orchestrator = ToolOrchestrator(tools)
    registry = load_registry(Path(__file__).resolve().parents[2] / "registry")
    result = await orchestrator.execute(registry.get("Coder"), "run_command",
        {"command": ["python", "-c", "print('ok')"]}, context={"run_id": "fixture", "agent_call_id": "coder"})
    assert result["ok"] and result["result"]["exit_code"] == 0
    assert snapshot.call_count == 2
    tools.snapshot_runtime(refresh=False)
    assert snapshot.call_count == 2
    assert orchestrator.coder_progress({"agent_call_id": "coder"})[0]["matches_current_revision"]
    tools.edit_file(role="Coder", path="value.py", content="VALUE = 2\n")
    assert tools._verified_command_snapshot is None
    tools.snapshot_runtime(refresh=False)
    assert snapshot.call_count == 3
