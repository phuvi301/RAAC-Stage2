import json
from pathlib import Path
from unittest.mock import Mock

import pytest
import yaml

from raac.benchmark.dataset import Task, load_tasks
from raac.harness.layer3_execution.sandbox.docker_sandbox import (
    DockerSandbox,
    cache_lock,
    dockerfile,
    junit_stats,
    load_profile,
    resolve_path,
)
from raac.harness.layer3_execution.sandbox.policy import SandboxPolicy


def task_data():
    return {
        "repos": [
            {
                "repo": "psf/requests",
                "repo_url": "https://github.com/psf/requests",
                "issues": [
                    {
                        "id": "a",
                        "title": "issue",
                        "body": "body",
                        "base_commit": "a" * 40,
                    }
                ],
            }
        ]
    }


def test_dataset(tmp_path):
    path = tmp_path / "data.json"
    data = task_data()
    path.write_text(json.dumps(data))
    assert load_tasks(path)["a"].repo == "psf/requests"
    data["repos"][0]["issues"] *= 2
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="Duplicate"):
        load_tasks(path)
    data["repos"][0]["issues"] = [{}]
    path.write_text(json.dumps(data))
    with pytest.raises(KeyError):
        load_tasks(path)


def test_paths_lock_and_stats(tmp_path):
    assert resolve_path(tmp_path, "repos") == tmp_path / "repos"
    with (
        cache_lock(tmp_path / "cache.lock"),
        pytest.raises(TimeoutError),
        cache_lock(tmp_path / "cache.lock", timeout=0),
    ):
        pass
    with cache_lock(tmp_path / "cache.lock", timeout=0):
        pass
    assert (
        junit_stats(b'<testsuites><testsuite tests="3" failures="1"/></testsuites>')[
            "tests"
        ]
        == 3
    )


def test_profile_override(tmp_path):
    path = tmp_path / "p.yaml"
    data = {
        "version": 1,
        "repo": "psf/requests",
        "base_image": "python:3.10-slim",
        "install_commands": [],
        "test_command": ["pytest"],
        "task_overrides": {"a": {"test_command": ["pytest", "-q"]}},
    }
    path.write_text(yaml.safe_dump(data))
    task = Task("a", "psf/requests", "url", "t", "b", "a" * 40)
    assert load_profile(path, task)["test_command"] == ["pytest", "-q"]
    data["task_overrides"]["a"]["test_command"] = "pytest"
    path.write_text(yaml.safe_dump(data))
    with pytest.raises(ValueError, match="argument lists"):
        load_profile(path, task)


@pytest.mark.parametrize(
    "exit_code,patch,status",
    [
        (0, None, "passed"),
        (1, None, "test_failed"),
        (2, None, "collection_error"),
        (5, None, "no_tests"),
        (86, b"bad", "patch_error"),
        (87, b"bad", "patch_setup_error"),
        (90, None, "preparation_error"),
    ],
)
def test_execute_cleanup(tmp_path, exit_code, patch, status):
    runner = DockerSandbox(client=Mock())
    container = runner.client.containers.create.return_value
    container.status = "exited"
    container.attrs = {"State": {"ExitCode": exit_code}}
    container.logs.return_value = b"log"
    import docker

    container.get_archive.side_effect = docker.errors.NotFound("missing")
    result = runner.execute(Mock(id="image"), ["pytest"], tmp_path, "test", patch)
    assert result["status"] == status
    container.remove.assert_called_once_with(force=True)
    runner.client.volumes.create.return_value.remove.assert_called_once_with(force=True)
    options = runner.client.containers.create.call_args.kwargs
    assert options["network_mode"] == "none" and options["user"] == "1000:1000"
    assert options["cap_drop"] == ["ALL"]


def test_timeout_and_create_error_cleanup(tmp_path):
    import docker

    runner = DockerSandbox(client=Mock(), policy=SandboxPolicy(timeout=0))
    container = runner.client.containers.create.return_value
    container.status = "running"
    container.attrs = {"State": {"ExitCode": 137}}
    container.logs.return_value = b""
    container.get_archive.side_effect = docker.errors.NotFound("missing")
    assert runner.execute(Mock(id="i"), ["sleep", "10"], tmp_path, "timeout")["timeout"]
    container.kill.assert_called_once()
    runner.client.reset_mock()
    runner.client.containers.create.side_effect = RuntimeError("create failed")
    with pytest.raises(RuntimeError):
        runner.execute(Mock(id="i"), ["pytest"], tmp_path, "error")
    runner.client.volumes.create.return_value.remove.assert_called_once_with(force=True)


def test_missing_profile_record(tmp_path):
    path = tmp_path / "dataset.json"
    path.write_text(json.dumps(task_data()))
    config = tmp_path / "config.yaml"
    config.write_text(
        yaml.safe_dump(
            {"version": 1, "dataset": str(path), "results": "results", "profiles": {}}
        )
    )
    runner = DockerSandbox(root=tmp_path, config=config, client=Mock())
    record = runner.run("a")
    assert record["status"] == "profile_missing"
    assert Path(record["result_path"]).exists()
    runner.client.info.assert_not_called()


def test_docker_check():
    runner = DockerSandbox(client=Mock())
    runner.client.info.return_value = {"OSType": "windows"}
    assert runner.check()["status"] == "unavailable"


def test_recipe_uses_exec_arguments():
    recipe = dockerfile(
        {"base_image": "python:3.10", "install_commands": [["echo", "a;false"]]}
    )
    assert 'RUN ["echo", "a;false"]' in recipe


def test_source_preparation_consumes_build_budget(tmp_path, monkeypatch):
    import docker
    from raac.harness.layer3_execution.sandbox import docker_sandbox as module

    elapsed = [0]
    observed = []
    runner = DockerSandbox(client=Mock())
    runner.config["results"] = str(tmp_path / "results")
    runner.client.info.return_value = {"OSType": "linux"}
    runner.client.images.get.side_effect = docker.errors.ImageNotFound("missing")

    def archive(task, cache, destination, **kwargs):
        destination.write_bytes(b"source")
        elapsed[0] = 30

    def build(context, tag, log, timeout):
        observed.append(timeout)
        raise module.BuildTimeout("fixture deadline")

    monkeypatch.setattr(module.time, "monotonic", lambda: elapsed[0])
    monkeypatch.setattr(module, "archive_repo", archive)
    monkeypatch.setattr(module, "build_image", build)
    record = runner.run("psf__requests-6027", build_timeout=60)
    assert observed == [30]
    assert record["status"] == "build_timeout"


def test_archive_exact_base_and_cache_reuse(tmp_path):
    import subprocess
    import tarfile

    from raac.harness.layer3_execution.sandbox.docker_sandbox import archive_repo

    repo = tmp_path / "source"
    repo.mkdir()

    def git(*args):
        return subprocess.check_output(["git", "-C", str(repo), *args]).decode().strip()

    git("init")
    git("config", "user.email", "fixture@example.invalid")
    git("config", "user.name", "Fixture")
    (repo / "value.txt").write_text("base")
    git("add", ".")
    git("commit", "-m", "base")
    commit = git("rev-parse", "HEAD")
    (repo / "value.txt").write_text("later")
    git("commit", "-am", "later")
    task = Task("fixture", "fixture/repo", str(repo), "t", "b", commit)
    for number in range(2):
        archive = tmp_path / f"{number}.tar"
        archive_repo(task, tmp_path / "cache", archive)
        with tarfile.open(archive) as tar:
            assert tar.extractfile("value.txt").read() == b"base"


@pytest.mark.parametrize(
    "field,value", [("base_commit", "main"), ("body", ""), ("id", 10)]
)
def test_invalid_task_fields(tmp_path, field, value):
    data = task_data()
    data["repos"][0]["issues"][0][field] = value
    path = tmp_path / "data.json"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        load_tasks(path)


def test_snapshot_redacts_credentials(monkeypatch):
    from raac.harness.layer3_execution.sandbox.docker_sandbox import safe_snapshot

    monkeypatch.setenv("LLM_API_KEY", "secret-value-123")
    result = safe_snapshot(
        {
            "commands": ["https://user:password@example.org", "secret-value-123"],
            "api_key": "private",
        }
    )
    assert result["api_key"] == "[REDACTED]"
    assert result["commands"] == ["https://[REDACTED]@example.org", "[REDACTED]"]
