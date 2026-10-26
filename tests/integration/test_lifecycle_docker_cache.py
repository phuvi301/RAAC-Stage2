"""Offline cache and resume checks in an installed, isolated Docker image."""

import os
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
from raac.harness.layer3_execution.tools.docker_workspace import DockerWorkspaceTools

pytestmark = pytest.mark.skipif(not os.getenv("RAAC_LIFECYCLE_IMAGE"), reason="Set RAAC_LIFECYCLE_IMAGE to an installed image")
test_resume = existing.test_lifecycle_resume_on_same_container_preserves_baseline_and_attribution


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
