"""Real Docker checks. Opt in with RAAC_DOCKER_TESTS=1; never substitute mocks."""

import os
import subprocess
import tarfile
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest

import docker
from raac.benchmark.dataset import Task
from raac.harness.layer3_execution.sandbox.docker_sandbox import (
    DockerSandbox,
    archive_repo,
    dockerfile,
)
from raac.harness.layer3_execution.sandbox.policy import SandboxPolicy

pytestmark = pytest.mark.skipif(
    os.environ.get("RAAC_DOCKER_TESTS") != "1",
    reason="Set RAAC_DOCKER_TESTS=1 for real Docker integration",
)


@pytest.fixture(scope="module")
def environment(tmp_path_factory):
    root = tmp_path_factory.mktemp("docker-fixture")
    repo = root / "source"
    repo.mkdir()

    def git(*args):
        return subprocess.check_output(["git", "-C", str(repo), *args]).decode().strip()

    git("init")
    git("config", "core.autocrlf", "false")
    git("config", "user.email", "fixture@example.invalid")
    git("config", "user.name", "Fixture")
    (repo / "value.py").write_bytes(b"VALUE = 0\n")
    (repo / "test_value.py").write_text(
        "from value import VALUE\ndef test_value():\n    assert VALUE == 1\n"
    )
    (repo / "native.c").write_bytes(b"int value(void) { return 0; }\n")
    (repo / "native_check.py").write_bytes(
        b"import ctypes\ndef test_native():\n    assert ctypes.CDLL('/workspace/native.so').value() == 1\n"
    )
    git("add", ".")
    git("commit", "-m", "base")
    commit = git("rev-parse", "HEAD")
    (repo / "value.py").write_bytes(b"VALUE = 1\n")
    git("commit", "-am", "later")
    task = Task("fixture", "fixture/repo", str(repo), "fixture", "fixture", commit)
    context = root / "context"
    context.mkdir()
    archive_repo(task, root / "cache", context / "source.tar")
    # This validates the actual archive is the old base commit, not HEAD.
    with tarfile.open(context / "source.tar") as tar:
        assert tar.extractfile("value.py").read() == b"VALUE = 0\n"
    profile = {
        "base_image": "python:3.11-slim-bookworm",
        "system_packages": ["git", "gcc", "libc6-dev"],
        "install_commands": [
            ["python", "-m", "pip", "install", "pytest==8.3.5"],
            ["gcc", "-shared", "-fPIC", "native.c", "-o", "native.so"],
        ],
    }
    (context / "Dockerfile").write_text(dockerfile(profile))
    client = docker.from_env(timeout=30)
    assert client.info()["OSType"] == "linux"
    image, _ = client.images.build(
        path=str(context),
        tag="raac-fixture:" + uuid.uuid4().hex,
        rm=True,
        forcerm=True,
        timeout=600,
        use_config_proxy=False,
    )
    runner = DockerSandbox(client=client)
    try:
        yield runner, image, root
    finally:
        client.images.remove(image.id, force=True)
        client.close()


def test_baseline_patch_and_invalid(environment):
    runner, image, root = environment
    command = ["python", "-m", "pytest", "-q", "--junitxml=/workspace/pytest.xml"]
    patch = b"diff --git a/value.py b/value.py\n--- a/value.py\n+++ b/value.py\n@@ -1 +1 @@\n-VALUE = 0\n+VALUE = 1\n"
    assert runner.execute(image, command, root, "baseline")["status"] == "test_failed"
    result = runner.execute(image, command, root, "candidate", patch)
    assert result["status"] == "passed"
    assert result["pytest"]["tests"] == 1
    assert (
        runner.execute(image, command, root, "invalid", b"invalid")["status"]
        == "patch_error"
    )
    assert not runner.client.containers.list(
        all=True, filters={"label": "raac=" + root.name}
    )
    assert not runner.client.volumes.list(filters={"label": "raac=" + root.name})


def test_timeout(environment):
    runner, image, root = environment
    short = DockerSandbox(client=runner.client, policy=SandboxPolicy(timeout=1))
    result = short.execute(
        image, ["python", "-c", "import time; time.sleep(30)"], root, "timeout"
    )
    assert result["status"] == "timeout"


def test_network_host_and_parallel_isolation(environment):
    runner, image, root = environment
    sentinel = root / "host-sentinel"
    sentinel.write_text("unchanged")
    code = """import socket
import os
import re
from pathlib import Path
assert os.getuid() == 1000
status = Path('/proc/self/status').read_text()
assert re.search(r'^NoNewPrivs:\\s+1$', status, re.M)
assert re.search(r'^CapEff:\\s+0+$', status, re.M)
assert int(Path('/sys/fs/cgroup/memory.max').read_text()) == 4 * 1024**3
assert int(Path('/sys/fs/cgroup/pids.max').read_text()) == 256
quota, period = map(int, Path('/sys/fs/cgroup/cpu.max').read_text().split())
assert quota == 2 * period
assert not Path('/var/run/docker.sock').exists()
local = socket.socket(); local.bind(('localhost', 0)); local.close()
s = socket.socket(); s.settimeout(1)
assert s.connect_ex(('1.1.1.1', 443)) != 0
p = Path(HOST_PATH)
p.parent.mkdir(parents=True, exist_ok=True)
p.write_text('container-only')
assert Path('/workspace/value.py').read_text() == 'VALUE = 0\\n'
Path('/workspace/value.py').write_text('changed')
""".replace("HOST_PATH", repr(str(sentinel)))
    # Use a Linux absolute path that has the same spelling as the host path.
    # No bind mounts are supplied, so host files remain inaccessible.
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(
                runner.execute, image, ["python", "-c", code], root, f"isolation-{i}"
            )
            for i in range(2)
        ]
        assert [f.result()["status"] for f in futures] == ["passed", "passed"]
    assert sentinel.read_text() == "unchanged"


def test_malicious_patch_cannot_write_host(environment):
    import difflib

    runner, image, root = environment
    sentinel = root / "patch-host-sentinel"
    sentinel.write_text("unchanged")
    code = """from pathlib import Path
assert not Path('/host').exists()
try:
    Path('/host/patch-host-sentinel').write_text('overwritten')
except OSError:
    pass
else:
    raise AssertionError('Unexpected writable host path')
"""
    patch = "".join(
        difflib.unified_diff(
            [],
            code.splitlines(keepends=True),
            fromfile="/dev/null",
            tofile="b/attack.py",
        )
    ).encode()
    result = runner.execute(image, ["python", "attack.py"], root, "malicious", patch)
    assert result["status"] == "passed"
    assert sentinel.read_text() == "unchanged"


def test_build_wall_clock_timeout_and_builder_cleanup(environment, tmp_path):
    from raac.harness.layer3_execution.sandbox.build import BuildTimeout, build_image

    runner, _, _ = environment
    (tmp_path / "Dockerfile").write_text(
        "FROM python:3.11-slim-bookworm\nRUN echo RAAC_BUILD_TIMEOUT && sleep 120\n"
    )
    log = tmp_path / "build.log"
    with pytest.raises(BuildTimeout):
        build_image(tmp_path, "raac-timeout:" + uuid.uuid4().hex, log, 30)
    text = log.read_text(encoding="utf-8")
    assert "RAAC_BUILD_TIMEOUT" in text
    builder = text.splitlines()[0].strip()
    assert builder.startswith("raac-")
    assert not runner.client.containers.list(
        all=True, filters={"name": "buildx_buildkit_" + builder}
    )


def test_real_batch_interrupt_resume_and_error_continuation(
    environment, tmp_path, monkeypatch
):
    import json

    import yaml

    from raac.harness.layer3_execution.sandbox import batch

    runner, image, _ = environment
    data = {
        "repos": [
            {
                "repo": "fixture/repo",
                "repo_url": "https://github.com/fixture/repo",
                "issues": [
                    {"id": tid, "title": "t", "body": "b", "base_commit": "a" * 40}
                    for tid in ("fail", "pass")
                ],
            }
        ]
    }
    (tmp_path / "dataset.json").write_text(json.dumps(data))
    (tmp_path / "registry").mkdir()
    (tmp_path / "registry/VERSION").write_text("1")
    (tmp_path / "profile.yaml").write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "repo": "fixture/repo",
                "base_image": "python:3.11",
                "test_command": ["python"],
                "install_commands": [],
            }
        )
    )
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "dataset": "dataset.json",
                "results": "results",
                "profiles": {"fixture/repo": "profile.yaml"},
            }
        )
    )
    sandbox = DockerSandbox(root=tmp_path, config="config.yaml")
    interrupted = [False]
    calls = []

    class CachedImageRunner:
        def __init__(self, **kwargs):
            pass

        def run(self, task_id, **kwargs):
            calls.append(task_id)
            directory = tmp_path / "results" / kwargs["run_id"]
            directory.mkdir(parents=True)
            command = (
                [
                    "python",
                    "-m",
                    "pytest",
                    "test_value.py",
                    "--junitxml=/workspace/pytest.xml",
                ]
                if task_id == "fail"
                else ["python", "-c", "assert True"]
            )
            result = runner.execute(image, command, directory, "baseline")
            record = {
                "run_id": kwargs["run_id"],
                "task_id": task_id,
                "status": result["status"],
                "baseline": result,
            }
            (directory / "record.json").write_text(json.dumps(record))
            if not interrupted[0]:
                interrupted[0] = True
                raise KeyboardInterrupt
            return record

    monkeypatch.setattr(batch, "DockerSandbox", CachedImageRunner)
    with pytest.raises(KeyboardInterrupt):
        batch.run_batch(sandbox, batch_id="docker-batch")
    result = batch.run_batch(sandbox, batch_id="docker-batch", resume=True)
    assert result["executed"] == 2
    assert result["statuses"] == {"test_failed": 1, "passed": 1}
    batch.run_batch(sandbox, batch_id="docker-batch", resume=True)
    assert calls == ["fail", "pass"]


def test_native_patch_is_rebuilt_before_tests(environment):
    runner, image, root = environment
    command = [
        "python",
        "-m",
        "pytest",
        "native_check.py",
        "--junitxml=/workspace/pytest.xml",
    ]
    patch = b"diff --git a/native.c b/native.c\n--- a/native.c\n+++ b/native.c\n@@ -1 +1 @@\n-int value(void) { return 0; }\n+int value(void) { return 1; }\n"
    profile = {
        "post_patch_commands": [
            ["gcc", "-shared", "-fPIC", "native.c", "-o", "native.so"]
        ]
    }
    assert (
        runner.execute(image, command, root, "native-base")["status"] == "test_failed"
    )
    assert (
        runner.execute(image, command, root, "native-patched", patch, profile)["status"]
        == "passed"
    )
    assert (
        runner.execute(
            image,
            command,
            root,
            "native-build-error",
            patch,
            {"post_patch_commands": [["false"]]},
        )["status"]
        == "patch_setup_error"
    )
