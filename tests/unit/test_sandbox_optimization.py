import hashlib
import json
from dataclasses import replace
from unittest.mock import Mock

import pytest

from raac.benchmark.dataset import load_tasks
from raac.harness.layer3_execution.sandbox import cache
from raac.harness.layer3_execution.sandbox.docker_sandbox import (
    ROOT,
    DockerSandbox,
    dockerfile,
    load_profile,
)
from raac.harness.layer3_execution.sandbox.evaluation import (
    DEFAULT_ARTIFACTS,
    load_evaluation,
)
from raac.harness.layer3_execution.sandbox.optimization import (
    build_identity,
    evaluation_identity,
    needs_native_rebuild,
    reusable_record,
)


def test_build_identity_excludes_test_only_configuration():
    task = load_tasks(ROOT / "data/tasks/cleaned_benchmark.json")["psf__requests-6027"]
    profile = load_profile(ROOT / "configs/repos/requests-legacy.yaml", task)
    recipe = dockerfile(profile)
    changed = dict(profile, test_command=["pytest", "other.py"], test_timeout=9)
    assert build_identity(task, "source", recipe) == build_identity(
        task, "source", dockerfile(changed)
    )
    changed["environment"] = {"BUILD_AFFECTING": "yes"}
    assert build_identity(task, "source", recipe) != build_identity(
        task, "source", dockerfile(changed)
    )
    assert build_identity(task, "source", recipe) != build_identity(
        replace(task, base_commit="b" * 40), "source", recipe
    )


def test_evaluation_identity_is_per_task_and_stage():
    tasks = load_tasks(ROOT / "data/tasks/cleaned_benchmark.json")
    task = tasks["psf__requests-6027"]
    row = load_evaluation(ROOT / DEFAULT_ARTIFACTS, task)
    args = (task, {"test_command": ["pytest"]}, row, {}, "1")
    original = evaluation_identity(*args, "full")
    assert original != evaluation_identity(*args, "pilot")
    assert original != evaluation_identity(
        task, {"test_command": ["pytest", "-x"]}, row, {}, "1", "full"
    )
    row = dict(row, PASS_TO_PASS=[])
    assert original != evaluation_identity(task, args[1], row, {}, "1", "full")


@pytest.mark.parametrize(
    "path,expected",
    [
        ("src/pure.py", False),
        ("src/generate.py", True),
        ("src/native.pyx", True),
        ("unknown.py", True),
        ("setup.py", True),
    ],
)
def test_native_fast_path_is_explicit(path, expected):
    profile = {
        "post_patch_commands": [["rebuild"]],
        "native_rebuild_paths": ["*.pyx", "*generate*", "*setup*"],
        "native_safe_paths": ["src/*.py"],
    }
    patch = ("--- a/" + path + "\n+++ b/" + path + "\n").encode()
    assert needs_native_rebuild(profile, patch) is expected
    assert needs_native_rebuild({"post_patch_commands": [["rebuild"]]}, patch)


def test_reuse_requires_complete_untampered_full_evidence(tmp_path):
    directory = tmp_path / "run"
    directory.mkdir()
    record = {
        "evaluation_identity": "identity",
        "status": "validated",
        "stage": "full",
        "baseline_validation": {"valid": True},
        "gold_validation": {"valid": True},
    }
    path = directory / "record.json"
    path.write_text(json.dumps(record))
    assert reusable_record(tmp_path, "identity") is None
    record["evidence_hashes"] = {}
    for phase in ("baseline", "gold"):
        for suffix in (".log", ".tests.json"):
            file = directory / (phase + suffix)
            file.write_text("evidence")
            record["evidence_hashes"][file.name] = hashlib.sha256(
                file.read_bytes()
            ).hexdigest()
    path.write_text(json.dumps(record))
    assert reusable_record(tmp_path, "identity")["status"] == "validated"
    assert reusable_record(tmp_path, "changed") is None
    (directory / "gold.tests.json").write_text("tampered")
    assert reusable_record(tmp_path, "identity") is None


def test_retention_dry_run_protects_active_images(tmp_path, monkeypatch):
    monkeypatch.setattr(cache, "ROOT", tmp_path)
    runner = Mock(root=tmp_path)
    inventory = {
        "total_bytes_upper_bound": 4 * cache.GIB,
        "legacy_build_bytes": 0,
        "managed_build_bytes": 0,
        "images": [
            {
                "id": "active",
                "tags": ["raac-sandbox:a"],
                "protected": True,
                "last_used": 0,
                "bytes_upper_bound": 2 * cache.GIB,
            },
            {
                "id": "old",
                "tags": ["raac-sandbox:b"],
                "protected": False,
                "last_used": 1,
                "bytes_upper_bound": 2 * cache.GIB,
            },
        ],
    }
    monkeypatch.setattr(cache, "inventory", lambda *a: inventory)
    result = cache.prune(runner, max_gib=1)
    assert result["actions"] == [{"kind": "image", "id": "old"}]
    runner.client.images.remove.assert_not_called()
    result = cache.prune(runner, max_gib=1, apply=True, workload_locked=True)
    runner.client.images.remove.assert_called_once_with("raac-sandbox:b", force=False)
    assert result["over_budget"]


def test_inventory_uses_bulk_metadata_and_counts_buildx_sizes(tmp_path, monkeypatch):
    runner = Mock(root=tmp_path, config={"results": "results", "repo_cache": "repos"})
    runner.client.api.containers.return_value = [{"ImageID": "active"}]
    runner.client.api.images.return_value = [
        {"Id": "active", "RepoTags": ["raac-sandbox:a"], "Size": 100},
        {"Id": "external", "RepoTags": ["other:a"], "Size": 500},
    ]
    runner.client.df.return_value = {
        "Images": [{"Id": "active", "Size": 200}],
        "LayersSize": 700,
    }
    monkeypatch.setattr(
        cache.subprocess,
        "run",
        lambda *a, **kw: Mock(returncode=0, stdout='{"Size":"4.096kB"}\n'),
    )
    result = cache.inventory(runner)
    assert len(result["images"]) == 1
    assert result["images"][0]["protected"]
    assert result["image_bytes_upper_bound"] == 200
    assert result["accounting_complete"]
    assert result["managed_build_bytes"] >= 4096
    runner.client.images.list.assert_not_called()


@pytest.mark.parametrize(
    "value,minimum",
    [(4096, 4096), ("4.096kB", 4096), ("28.4MB", 28400000), ("1.2GiB", 1288490188)],
)
def test_buildx_human_size_is_counted_conservatively(value, minimum):
    assert cache.size_bytes(value) >= minimum
    with pytest.raises(ValueError):
        cache.size_bytes("unknown")


def test_pilot_and_preflight_run_arguments_are_guarded(tmp_path):
    runner = DockerSandbox(client=Mock())
    runner.config["results"] = str(tmp_path)
    result = runner.run("missing", stage="pilot")
    assert "Pilot requires validation" in result["error"]
    result = runner.run("missing", preflight=True, test_mode="suite")
    assert "Preflight requires issue mode" in result["error"]
