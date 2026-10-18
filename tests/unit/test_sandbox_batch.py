import json
from pathlib import Path
from unittest.mock import Mock

import pytest
import yaml

from raac.harness.layer3_execution.sandbox import batch
from raac.harness.layer3_execution.sandbox.docker_sandbox import (
    DockerSandbox,
    load_profile,
)


@pytest.fixture
def setup_batch(tmp_path, monkeypatch):
    profile = {
        "version": 1,
        "repo": "owner/repo",
        "base_image": "python:3.10",
        "install_commands": [],
        "test_command": ["python", "-m", "pytest"],
    }
    (tmp_path / "profile.yaml").write_text(yaml.safe_dump(profile))
    data = {
        "repos": [
            {
                "repo": "owner/repo",
                "repo_url": "https://github.com/owner/repo",
                "issues": [
                    {"id": x, "title": "t", "body": "b", "base_commit": "a" * 40}
                    for x in ("first", "second")
                ],
            }
        ]
    }
    (tmp_path / "dataset.json").write_text(json.dumps(data))
    (tmp_path / "registry").mkdir()
    (tmp_path / "registry/VERSION").write_text("1")
    config = {
        "version": 1,
        "dataset": "dataset.json",
        "repo_cache": "cache",
        "results": "results",
        "profiles": {"owner/repo": "profile.yaml"},
    }
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(config))
    sandbox = DockerSandbox(root=tmp_path, config="config.yaml")
    calls = []
    statuses = {"first": "test_failed", "second": "timeout"}

    class FakeRunner:
        def __init__(self, **kwargs):
            pass

        def run(self, task_id, **kwargs):
            calls.append(task_id)
            return {
                "run_id": kwargs["run_id"],
                "task_id": task_id,
                "status": statuses[task_id],
                "duration_seconds": 1,
                "baseline": {"pytest": None, "log": "test.log"},
            }

    monkeypatch.setattr(batch, "DockerSandbox", FakeRunner)
    return sandbox, calls, statuses


def test_batch_report_resume_and_retry(setup_batch):
    sandbox, calls, statuses = setup_batch
    result = batch.run_batch(sandbox, test_mode="suite", batch_id="test")
    assert result["total"] == 2 and result["executed"] == 1
    assert calls == ["first", "second"]
    batch.run_batch(sandbox, test_mode="suite", batch_id="test", resume=True)
    assert calls == ["first", "second"]
    statuses["second"] = "passed"
    result = batch.run_batch(
        sandbox, test_mode="suite", batch_id="test", resume=True, retry_errors=True
    )
    assert calls == ["first", "second", "second"]
    assert result["executed"] == 2
    assert result["tasks"][1]["attempts"] == 2
    directory = sandbox.root / "results/batches/test"
    assert (directory / "summary.csv").exists()
    assert "owner/repo" in (directory / "repos.csv").read_text()
    assert (
        json.loads((directory / "manifest.json").read_text())["status"] == "processed"
    )


def test_cooperative_stop_and_resume_preserves_checkpoint(setup_batch):
    sandbox, calls, _ = setup_batch
    directory = sandbox.root / "results/batches/stopped"

    def stop(event):
        if event.get("event") == "batch_started":
            (directory / "STOP").write_text("stop")

    with pytest.raises(KeyboardInterrupt):
        batch.run_batch(sandbox, test_mode="suite", batch_id="stopped", progress=stop)
    assert not calls
    assert (
        json.loads((directory / "manifest.json").read_text())["status"] == "interrupted"
    )
    batch.run_batch(sandbox, test_mode="suite", batch_id="stopped", resume=True)
    assert calls == ["first", "second"]
    assert not (directory / "STOP").exists()


def test_resume_rejects_pilot_full_and_reuse_changes(setup_batch):
    sandbox, _, _ = setup_batch
    batch.run_batch(sandbox, test_mode="suite", batch_id="stages")
    with pytest.raises(ValueError, match="fingerprint"):
        batch.run_batch(
            sandbox, test_mode="suite", batch_id="stages", resume=True, stage="pilot"
        )
    with pytest.raises(ValueError, match="fingerprint"):
        batch.run_batch(
            sandbox,
            test_mode="suite",
            batch_id="stages",
            resume=True,
            reuse_validated=True,
        )


def test_fingerprint_and_selection_validation(setup_batch):
    sandbox, _, _ = setup_batch
    batch.run_batch(sandbox, test_mode="suite", batch_id="test", task_ids=["first"])
    with pytest.raises(ValueError, match="fingerprint"):
        batch.run_batch(
            sandbox,
            test_mode="suite",
            batch_id="test",
            resume=True,
            task_ids=["first", "second"],
        )
    (sandbox.root / "registry/VERSION").write_text("2")
    with pytest.raises(ValueError, match="fingerprint"):
        batch.run_batch(
            sandbox, test_mode="suite", batch_id="test", resume=True, task_ids=["first"]
        )
    with pytest.raises(ValueError, match="Unknown"):
        batch.run_batch(sandbox, test_mode="suite", task_ids=["missing"])
    with pytest.raises(ValueError, match="requires"):
        batch.run_batch(sandbox, test_mode="suite", retry_errors=True)


def test_profile_override_validation(setup_batch):
    sandbox, _, _ = setup_batch
    path = sandbox.root / "profile.yaml"
    raw = yaml.safe_load(path.read_text())
    raw["task_overrides"] = {"missing": {"test_command": ["pytest"]}}
    path.write_text(yaml.safe_dump(raw))
    result = batch.check_profiles(sandbox)
    assert result["status"] == "invalid" and "unknown override" in result["errors"][0]


def test_resume_recovers_completed_record_after_interrupt(setup_batch, monkeypatch):
    sandbox, calls, _ = setup_batch
    original = batch.DockerSandbox

    class InterruptRunner(original):
        def run(self, task_id, **kwargs):
            result = super().run(task_id, **kwargs)
            directory = sandbox.root / "results" / kwargs["run_id"]
            directory.mkdir(parents=True)
            (directory / "record.json").write_text(json.dumps(result))
            raise KeyboardInterrupt

    monkeypatch.setattr(batch, "DockerSandbox", InterruptRunner)
    with pytest.raises(KeyboardInterrupt):
        batch.run_batch(sandbox, test_mode="suite", batch_id="interrupted")
    monkeypatch.setattr(batch, "DockerSandbox", original)
    result = batch.run_batch(
        sandbox, test_mode="suite", batch_id="interrupted", resume=True
    )
    assert calls == ["first", "second"]
    assert result["tasks"][0]["attempts"] == 1


def test_non_pytest_runner_exit_codes():
    from tempfile import TemporaryDirectory

    sandbox = DockerSandbox(client=Mock())
    container = sandbox.client.containers.create.return_value
    container.status = "exited"
    container.attrs = {"State": {"ExitCode": 2}}
    container.logs.return_value = b"runner usage error"
    with TemporaryDirectory() as directory:
        result = sandbox.execute(
            Mock(id="image"),
            ["python", "runtests.py"],
            Path(directory),
            "django",
            profile={"test_framework": "django", "report_format": "none"},
        )
    assert result["status"] == "execution_error"
    assert result["pytest"] is None
    container.get_archive.assert_not_called()


def test_all_dataset_profiles_validate():
    result = batch.check_profiles(DockerSandbox())
    assert result == {
        "status": "valid",
        "tasks": 120,
        "covered": 120,
        "repos": 12,
        "errors": [],
    }


def test_profile_rejects_secret_environment(setup_batch):
    from raac.benchmark.dataset import load_tasks

    sandbox, _, _ = setup_batch
    path = sandbox.root / "profile.yaml"
    profile = yaml.safe_load(path.read_text())
    profile["environment"] = {"API_KEY": "do-not-log-me"}
    path.write_text(yaml.safe_dump(profile))
    with pytest.raises(ValueError, match="Secrets"):
        load_profile(path, load_tasks(sandbox.root / "dataset.json")["first"])


def test_versioned_source_keeps_release_ancestry(tmp_path):
    import hashlib
    import subprocess
    import tarfile

    from raac.benchmark.dataset import Task
    from raac.harness.layer3_execution.sandbox.docker_sandbox import archive_repo

    repo = tmp_path / "source"
    repo.mkdir()

    def git(*args):
        return subprocess.check_output(["git", "-C", str(repo), *args]).decode().strip()

    git("init")
    git("config", "user.email", "fixture@example.invalid")
    git("config", "user.name", "Fixture")
    (repo / "value.txt").write_text("release")
    git("add", ".")
    git("commit", "-m", "release")
    git("tag", "v1.0.0")
    (repo / "value.txt").write_text("base")
    git("commit", "-am", "base")
    commit = git("rev-parse", "HEAD")
    git("tag", "non-version-checkpoint")
    (repo / "value.txt").write_text("future patch")
    git("commit", "-am", "future")
    future = git("rev-parse", "HEAD")
    git("tag", "v9.0.0")
    archive = tmp_path / "source.tar"
    archive_repo(
        Task("fixture", "fixture/repo", str(repo), "t", "b", commit),
        tmp_path / "cache",
        archive,
        versioned=True,
    )
    original_hash = hashlib.sha256(archive.read_bytes()).hexdigest()
    # Repacking the mirror and changing Git's pack defaults must not invalidate
    # image cache keys for an unchanged commit and source recipe.
    cached_repo = tmp_path / "cache/fixture/repo.git"
    for key, value in (
        ("pack.window", "100"),
        ("pack.depth", "100"),
        ("pack.threads", "4"),
        ("pack.writeReverseIndex", "true"),
    ):
        subprocess.run(
            ["git", "--git-dir", str(cached_repo), "config", key, value], check=True
        )
    subprocess.run(
        ["git", "--git-dir", str(cached_repo), "repack", "-adf"],
        check=True,
        capture_output=True,
    )
    for _ in range(3):
        repeated = tmp_path / "repeated.tar"
        archive_repo(
            Task("fixture", "fixture/repo", str(repo), "t", "b", commit),
            tmp_path / "cache",
            repeated,
            versioned=True,
        )
        assert hashlib.sha256(repeated.read_bytes()).hexdigest() == original_hash
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    # This archive is produced entirely from the local test fixture.
    with tarfile.open(archive) as tar:
        tar.extractall(checkout, filter="data")
    description = (
        subprocess.check_output(
            [
                "git",
                "-C",
                str(checkout),
                "describe",
                "--tags",
                "--long",
                "--match=v*",
                "HEAD",
            ]
        )
        .decode()
        .strip()
    )
    assert description.startswith("v1.0.0-1-g")
    expected_date = git("show", "-s", "--format=%ci", commit)
    actual_date = (
        subprocess.check_output(
            ["git", "-C", str(checkout), "show", "-s", "--format=%ci", "HEAD"]
        )
        .decode()
        .strip()
    )
    assert actual_date == expected_date

    assert (checkout / ".git/refs/tags/non-version-checkpoint").exists()
    assert not (checkout / ".git/refs/tags/v9.0.0").exists()
    assert (
        subprocess.run(
            ["git", "-C", str(checkout), "cat-file", "-e", future],
            check=False,
            capture_output=True,
        ).returncode
        != 0
    )


@pytest.mark.parametrize(
    "text,code,status,count",
    [
        (b"ImportError before suite startup", 1, "collection_error", None),
        (b"Ran 0 tests in 0.0s\nOK", 0, "no_tests", 0),
        (
            b"Ran 5 tests in 0.1s\nFAILED (failures=1, errors=2, skipped=1)",
            1,
            "test_failed",
            5,
        ),
    ],
)
def test_django_summary_is_required(tmp_path, text, code, status, count):
    runner = DockerSandbox(client=Mock())
    container = runner.client.containers.create.return_value
    container.status = "exited"
    container.attrs = {"State": {"ExitCode": code}}
    container.logs.return_value = text
    result = runner.execute(
        Mock(id="image"),
        ["python", "runtests.py"],
        tmp_path,
        "native",
        profile={"test_framework": "django", "report_format": "none"},
    )
    assert result["status"] == status
    assert result["pytest"] is None
    assert (
        result["test_statistics"]["tests"] if result["test_statistics"] else None
    ) == count


def test_native_summary_uses_final_footer_and_preserves_xfails(tmp_path):
    runner = DockerSandbox(client=Mock())
    container = runner.client.containers.create.return_value
    container.status = "exited"
    container.attrs = {"State": {"ExitCode": 1}}
    container.logs.return_value = b"Captured nested runner output:\nRan 999 tests in 0.0s\nOK\n\nRan 5 tests in 0.1s\nFAILED (failures=1, expected failures=2)\n"
    result = runner.execute(
        Mock(id="image"),
        ["python", "runtests.py"],
        tmp_path,
        "summary",
        profile={"test_framework": "django", "report_format": "none"},
    )
    assert result["test_statistics"] == {
        "tests": 5,
        "failures": 1,
        "errors": 0,
        "skipped": 0,
        "expected_failures": 2,
        "unexpected_successes": 0,
    }


def test_source_preparation_deadline(tmp_path):
    from raac.benchmark.dataset import Task
    from raac.harness.layer3_execution.sandbox.build import BuildTimeout
    from raac.harness.layer3_execution.sandbox.docker_sandbox import archive_repo

    with pytest.raises(BuildTimeout, match="budget exhausted"):
        archive_repo(
            Task("fixture", "owner/repo", "invalid", "t", "b", "a" * 40),
            tmp_path,
            tmp_path / "source.tar",
            deadline=0,
        )


def test_scm_distance_across_long_merge_branch(tmp_path):
    import subprocess
    import tarfile

    from raac.benchmark.dataset import Task
    from raac.harness.layer3_execution.sandbox.docker_sandbox import archive_repo

    repo = tmp_path / "source"
    repo.mkdir()

    def git(*args):
        return subprocess.check_output(["git", "-C", str(repo), *args]).decode().strip()

    git("init", "-b", "main")
    git("config", "user.email", "fixture@example.invalid")
    git("config", "user.name", "Fixture")
    (repo / "value.txt").write_text("root")
    git("add", ".")
    git("commit", "-m", "root")
    root_commit = git("rev-parse", "HEAD")
    (repo / "value.txt").write_text("tagged")
    git("commit", "-am", "tagged")
    tagged_commit = git("rev-parse", "HEAD")
    git("tag", "v1.0.0")
    stream = []
    for number in range(1, 141):
        parent = root_commit if number == 1 else f":{number - 1}"
        stream.append(
            f"commit refs/heads/feature\nmark :{number}\ncommitter Fixture <fixture@example.invalid> {1600000000 + number} +0000\ndata 1\nf\nfrom {parent}\n\n"
        )
    stream.append(
        f"commit refs/heads/main\ncommitter Fixture <fixture@example.invalid> 1600001000 +0000\ndata 5\nmerge\nfrom {tagged_commit}\nmerge :140\n\n"
    )
    subprocess.run(
        ["git", "-C", str(repo), "fast-import", "--quiet"],
        input="".join(stream).encode(),
        check=True,
    )
    commit = git("rev-parse", "refs/heads/main")
    expected = git("describe", "--tags", "--long", commit)
    assert "-141-g" in expected
    archive = tmp_path / "source.tar"
    archive_repo(
        Task("merge", "fixture/merge", str(repo), "t", "b", commit),
        tmp_path / "cache",
        archive,
        versioned=True,
    )
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    with tarfile.open(archive) as tar:
        tar.extractall(checkout, filter="data")
    actual = (
        subprocess.check_output(
            ["git", "-C", str(checkout), "describe", "--tags", "--long", "HEAD"]
        )
        .decode()
        .strip()
    )
    assert actual == expected
