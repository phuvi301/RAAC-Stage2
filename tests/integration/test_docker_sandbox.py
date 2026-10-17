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


def test_persistent_builder_reuses_layers(environment, tmp_path):
    from raac.harness.layer3_execution.sandbox.build import build_image

    runner, _, _ = environment
    marker = uuid.uuid4().hex
    (tmp_path / "Dockerfile").write_text(
        "FROM python:3.11-slim-bookworm\nRUN echo " + marker + " > /cache-marker\n"
    )
    tags = ["raac-fixture:cache-" + uuid.uuid4().hex for _ in range(2)]
    try:
        for i, tag in enumerate(tags):
            build_image(tmp_path, tag, tmp_path / (str(i) + ".log"), 120)
        second = (tmp_path / "1.log").read_text(encoding="utf-8")
        assert "CACHED" in second
        assert "exporting cache to client" not in second
        assert (
            runner.client.images.get(tags[0]).id == runner.client.images.get(tags[1]).id
        )
        assert runner.client.containers.list(
            filters={"name": "buildx_buildkit_raac-sandbox-managed-v1"}
        )
    finally:
        for tag in tags:
            try:
                runner.client.images.remove(tag, force=False)
            except docker.errors.ImageNotFound:
                pass


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
        batch.run_batch(sandbox, test_mode="suite", batch_id="docker-batch")
    result = batch.run_batch(
        sandbox, test_mode="suite", batch_id="docker-batch", resume=True
    )
    assert result["executed"] == 2
    assert result["statuses"] == {"test_failed": 1, "passed": 1}
    batch.run_batch(sandbox, test_mode="suite", batch_id="docker-batch", resume=True)
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


def test_issue_baseline_gold_candidate_and_missing_selector(environment):
    from raac.harness.layer3_execution.sandbox.evaluation import grade

    runner, image, root = environment
    row = {
        "FAIL_TO_PASS": ["test_issue.py::test_issue"],
        "PASS_TO_PASS": ["test_issue.py::test_regression"],
    }
    patch = b"--- /dev/null\n+++ b/test_issue.py\n@@ -0,0 +1,5 @@\n+from value import VALUE\n+def test_issue():\n+    assert VALUE == 1\n+def test_regression():\n+    assert isinstance(VALUE, int)\n"
    spec = {
        "ids": row["FAIL_TO_PASS"] + row["PASS_TO_PASS"],
        "roots": ["test_issue.py"],
        "framework": "pytest",
        "command": ["python", "-m", "pytest"],
    }
    evaluation = {"test_patch": patch.decode(), "selectors": spec}
    command = ["python", "/tmp/raac-issue-runner.py", "/tmp/raac-selectors.json"]
    baseline = runner.execute(image, command, root, "issue-base", evaluation=evaluation)
    assert grade(row, baseline, baseline=True)["valid"]
    gold = b"--- a/value.py\n+++ b/value.py\n@@ -1 +1 @@\n-VALUE = 0\n+VALUE = 1\n"
    result = runner.execute(
        image, command, root, "issue-gold", gold, evaluation=evaluation
    )
    assert grade(row, result)["valid"]
    assert not grade(row, baseline)["valid"]
    broken = dict(evaluation, test_patch="bad patch")
    assert (
        runner.execute(image, command, root, "issue-invalid", evaluation=broken)[
            "status"
        ]
        == "test_patch_error"
    )
    spec["ids"].append("test_issue.py::test_missing")
    result = runner.execute(
        image, command, root, "issue-missing", evaluation=evaluation
    )
    assert result["status"] == "collection_error"
    assert not grade(row, result)["valid"]
    assert not runner.client.containers.list(
        all=True, filters={"label": "raac=" + root.name}
    )


def test_real_preflight_pilot_full_and_reuse(environment, tmp_path):
    import json
    import shutil

    import yaml

    from raac.harness.layer3_execution.sandbox.docker_sandbox import digest
    from raac.harness.layer3_execution.sandbox.optimization import build_identity

    original, image, root = environment
    shutil.copytree(root / "cache", tmp_path / "cache")
    commit = (
        subprocess.check_output(
            ["git", "-C", str(root / "source"), "rev-parse", "HEAD~1"]
        )
        .decode()
        .strip()
    )
    profile = {
        "version": 1,
        "repo": "fixture/repo",
        "base_image": "python:3.11-slim-bookworm",
        "system_packages": ["git", "gcc", "libc6-dev"],
        "install_commands": [
            ["python", "-m", "pip", "install", "pytest==8.3.5"],
            ["gcc", "-shared", "-fPIC", "native.c", "-o", "native.so"],
        ],
        "test_command": ["python", "-m", "pytest"],
        "preflight_imports": ["value"],
    }
    (tmp_path / "profile.yaml").write_text(yaml.safe_dump(profile))
    (tmp_path / "dataset.json").write_text(
        json.dumps(
            {
                "repos": [
                    {
                        "repo": "fixture/repo",
                        "repo_url": "https://github.com/fixture/repo",
                        "issues": [
                            {
                                "id": "fixture",
                                "title": "fixture",
                                "body": "fixture",
                                "base_commit": commit,
                            }
                        ],
                    }
                ]
            }
        )
    )
    row = {
        "repo": "fixture/repo",
        "base_commit": commit,
        "instance_id": "fixture-pr",
        "pr_url": "fixture",
        "version": "1",
        "environment_setup_commit": None,
        "FAIL_TO_PASS": ["test_issue.py::test_issue"],
        "PASS_TO_PASS": ["test_issue.py::test_regression"],
        "test_patch": "--- /dev/null\n+++ b/test_issue.py\n@@ -0,0 +1,5 @@\n+from value import VALUE\n+def test_issue():\n+    assert VALUE == 1\n+def test_regression():\n+    assert isinstance(VALUE, int)\n",
        "gold_patch": "--- a/value.py\n+++ b/value.py\n@@ -1 +1 @@\n-VALUE = 0\n+VALUE = 1\n",
    }
    (tmp_path / "evaluation.json").write_text(
        json.dumps({"source_sha256": "fixture", "tasks": {"fixture": row}})
    )
    (tmp_path / "registry").mkdir()
    (tmp_path / "registry/VERSION").write_text("fixture")
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "dataset": "dataset.json",
                "repo_cache": "cache",
                "results": "results",
                "profiles": {"fixture/repo": "profile.yaml"},
                "evaluation": "evaluation.json",
            }
        )
    )
    # Same actual fixture image/source/recipe; exercise cache lookup without an unnecessary second build.
    task = Task(
        "fixture", "fixture/repo", str(root / "source"), "fixture", "fixture", commit
    )
    key = build_identity(
        task, digest((root / "context/source.tar").read_bytes()), dockerfile(profile)
    )
    image.tag("raac-sandbox", key)
    runner = DockerSandbox(root=tmp_path, config="config.yaml", client=original.client)
    preflight = runner.run("fixture", preflight=True)
    assert preflight["status"] == "preflight_passed", preflight
    assert preflight["baseline"] is None
    assert not preflight["preflight"]["test_results"]
    pilot = runner.run("fixture", validate=True, stage="pilot")
    assert pilot["status"] == "pilot_passed", pilot
    assert pilot["baseline_validation"]["required"] == 1
    full = runner.run("fixture", validate=True)
    assert full["status"] == "validated", full
    assert full["baseline_validation"]["required"] == 2
    reused = runner.run("fixture", validate=True, reuse_validated=True)
    assert (
        reused["status"] == "validated" and reused["reused_from"] == full["result_path"]
    )
    assert not (tmp_path / "results" / reused["run_id"] / "gold.log").exists()
    patch = tmp_path / "candidate.patch"
    patch.write_bytes(row["gold_patch"].encode())
    candidate = runner.run("fixture", patch_path=patch)
    assert candidate["status"] == "resolved" and "gold" not in candidate
    assert not original.client.volumes.list(
        filters={"label": "raac=" + candidate["run_id"]}
    )


def test_issue_native_rebuild_and_timeout(environment):
    from dataclasses import replace

    from raac.harness.layer3_execution.sandbox.evaluation import grade

    runner, image, root = environment
    spec = {
        "ids": ["native_check.py::test_native"],
        "roots": ["native_check.py"],
        "framework": "pytest",
        "command": ["python", "-m", "pytest"],
    }
    patch = "--- a/native_check.py\n+++ b/native_check.py\n@@ -1 +1,2 @@\n+# evaluator test marker\n import ctypes\n"
    evaluation = {"test_patch": patch, "selectors": spec}
    command = ["python", "/tmp/raac-issue-runner.py", "/tmp/raac-selectors.json"]
    gold = b"--- a/native.c\n+++ b/native.c\n@@ -1 +1 @@\n-int value(void) { return 0; }\n+int value(void) { return 1; }\n"
    profile = {
        "post_patch_commands": [
            ["gcc", "-shared", "-fPIC", "native.c", "-o", "native.so"]
        ]
    }
    result = runner.execute(
        image,
        command,
        root,
        "issue-native",
        gold,
        profile=profile,
        evaluation=evaluation,
    )
    assert grade({"FAIL_TO_PASS": spec["ids"], "PASS_TO_PASS": []}, result)["valid"]
    previous = runner.policy
    runner.policy = replace(previous, timeout=0.01)
    try:
        result = runner.execute(
            image, command, root, "issue-timeout", evaluation=evaluation
        )
        assert result["status"] == "timeout"
    finally:
        runner.policy = previous


def test_native_unittest_selector_recording(environment):
    import difflib

    runner, image, root = environment
    source = """import unittest
import sys
from value import VALUE
class Sample(unittest.TestCase):
    def test_issue(self):
        self.assertEqual(VALUE, 1)
    def test_pass(self):
        "Regression remains stable."
        self.assertIsInstance(VALUE, int)
suite = unittest.defaultTestLoader.loadTestsFromTestCase(Sample)
r = unittest.TextTestRunner().run(suite)
sys.exit(not r.wasSuccessful())
"""
    patch = "".join(
        difflib.unified_diff(
            [],
            source.splitlines(keepends=True),
            fromfile="/dev/null",
            tofile="b/tests/runtests.py",
        )
    )
    ids = ["test_issue (__main__.Sample)", "Regression remains stable."]
    evaluation = {
        "test_patch": patch,
        "selectors": {
            "ids": ids,
            "roots": [],
            "framework": "django",
            "command": ["python", "tests/runtests.py"],
        },
    }
    command = ["python", "/tmp/raac-issue-runner.py", "/tmp/raac-selectors.json"]
    result = runner.execute(
        image, command, root, "issue-unittest", evaluation=evaluation
    )
    assert result["test_results"][ids[0]]["outcome"] == "failed"
    assert result["test_results"][ids[1]]["outcome"] == "passed"
