"""Frozen cohort selection must not drift with subsequent sandbox results."""

import hashlib
import json
from dataclasses import asdict
from unittest.mock import Mock

import pytest

from raac.benchmark.dataset import load_tasks, source_dataset
from raac.harness.layer3_execution.sandbox.batch import check_profiles
from raac.harness.layer3_execution.sandbox.docker_sandbox import ROOT, DockerSandbox


def test_frozen_benchmark_partitions_original_120_without_agent_secrets():
    path = ROOT / "data/tasks/benchmark105-v1.json"
    manifest = json.loads(path.read_text())
    exclusions = json.loads(
        path.with_name("benchmark105-v1-exclusions.json").read_text()
    )
    original = load_tasks(ROOT / "data/tasks/cleaned_benchmark.json")
    selected = load_tasks(path)
    excluded = {entry["task_id"] for entry in exclusions["tasks"]}
    assert len(selected) == 105 and len(excluded) == 15
    assert not set(selected) & excluded
    assert set(selected) | excluded == set(original)
    assert set(manifest["validation_evidence"]) == set(selected)
    assert manifest["scoring"]["denominator"] == 105
    assert len({task.repo for task in selected.values()}) == 12
    for identifier, task in selected.items():
        assert task == original[identifier]
        assert "gold_patch" not in asdict(task) and "test_patch" not in asdict(task)
    assert source_dataset(path).name == "cleaned_benchmark.json"
    assert (
        check_profiles(DockerSandbox(config="configs/benchmark105.yaml"))["covered"]
        == 105
    )


@pytest.fixture
def selection(tmp_path):
    source = tmp_path / "original.json"
    source.write_text(
        json.dumps(
            {
                "repos": [
                    {
                        "repo": "owner/repo",
                        "repo_url": "https://github.com/owner/repo",
                        "issues": [
                            {
                                "id": "keep",
                                "title": "t",
                                "body": "b",
                                "base_commit": "a" * 40,
                            }
                        ],
                    }
                ]
            }
        )
    )
    path = tmp_path / "selection.json"
    data = {
        "kind": "raac-task-selection",
        "schema_version": 1,
        "source_dataset": source.name,
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "task_ids": ["keep"],
        "task_count": 1,
    }
    path.write_text(json.dumps(data))
    return path, source, data


def test_frozen_source_change_is_rejected(selection):
    path, source, _ = selection
    assert list(load_tasks(path)) == ["keep"]
    source.write_text(source.read_text() + "\n")
    with pytest.raises(ValueError, match="source hash changed"):
        load_tasks(path)


def test_changed_evaluator_is_rejected_before_docker(tmp_path):
    artifact = tmp_path / "changed-evaluator.json"
    original = ROOT / "data/evaluation/swe_bench_verified.json"
    artifact.write_bytes(original.read_bytes() + b"\n")
    client = Mock()
    runner = DockerSandbox(config="configs/benchmark105.yaml", client=client)
    runner.config["results"] = str(tmp_path / "results")
    runner.config["evaluation"] = str(artifact)
    task_id = next(iter(load_tasks(ROOT / "data/tasks/benchmark105-v1.json")))
    result = runner.run(task_id)
    assert result["status"] == "preparation_error"
    assert "Frozen benchmark evaluator hash changed" in result["error"]
    client.info.assert_not_called()


def test_default_configuration_cannot_prepare_retired_task(tmp_path):
    client = Mock()
    runner = DockerSandbox(client=client)
    runner.config["results"] = str(tmp_path / "results")
    record = runner.run("psf__requests-1723", prepare=True)
    assert record["status"] == "preparation_error"
    client.info.assert_not_called()


@pytest.mark.parametrize(
    "ids,count", [(["missing"], 1), (["keep", "keep"], 2), ([], 0), (["keep"], 2)]
)
def test_invalid_membership_is_rejected(selection, ids, count):
    path, _, data = selection
    data.update(task_ids=ids, task_count=count)
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="selection|count"):
        load_tasks(path)
