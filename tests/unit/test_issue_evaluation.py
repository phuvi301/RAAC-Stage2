import json
from dataclasses import asdict
from unittest.mock import Mock

import pytest

from raac.benchmark.dataset import load_tasks
from raac.harness.layer3_execution.sandbox import batch
from raac.harness.layer3_execution.sandbox.docker_sandbox import ROOT, DockerSandbox
from raac.harness.layer3_execution.sandbox.evaluation import (
    DEFAULT_ARTIFACTS,
    grade,
    import_verified,
    load_evaluation,
    patch_paths,
    payload,
    selectors,
)


def test_all_120_evaluator_mappings_are_separate():
    tasks = load_tasks(ROOT / "data/tasks/cleaned_benchmark.json")
    for task in tasks.values():
        row = load_evaluation(ROOT / DEFAULT_ARTIFACTS, task)
        assert row["instance_id"] != task.id
        assert row["pr_url"].endswith(row["instance_id"].rsplit("-", 1)[1])
        assert row["FAIL_TO_PASS"]
        assert "gold_patch" not in asdict(task)
        assert "test_patch" not in asdict(task)
        assert len(payload(row, {"test_command": ["pytest"]})["ids"]) == len(
            set(row["FAIL_TO_PASS"] + row["PASS_TO_PASS"])
        )


@pytest.mark.parametrize("value", ['["a", "a"]', "[null]", '"a"', '["a\\nb"]'])
def test_invalid_selectors(value):
    with pytest.raises(ValueError):
        selectors(value)


def test_import_rejects_ambiguous_and_url_mismatch(tmp_path):
    row = {"repo": "a/b", "base_commit": "a", "instance_id": "a__b-2"}
    cleaned = tmp_path / "clean.json"
    raw = tmp_path / "raw.jsonl"
    cleaned.write_text(
        json.dumps(
            {
                "repos": [
                    {
                        "repo": "a/b",
                        "issues": [
                            {"id": "a__b-1", "base_commit": "a", "url": "wrong"}
                        ],
                    }
                ]
            }
        )
    )
    raw.write_text(json.dumps(row) + "\n" + json.dumps(row))
    with pytest.raises(ValueError, match="ambiguous"):
        import_verified(raw, cleaned, tmp_path / "output.json")
    raw.write_text(json.dumps(row))
    with pytest.raises(ValueError, match="URL"):
        import_verified(raw, cleaned, tmp_path / "output.json")
    assert not (tmp_path / "output.json").exists()


@pytest.mark.parametrize("outcome", ["skipped", "xfail", "xpass", "runner_error", None])
def test_grading_requires_every_test(outcome):
    row = {"FAIL_TO_PASS": ["a"], "PASS_TO_PASS": ["b"]}
    phase = {
        "status": "test_failed",
        "test_results": {"a": {"outcome": "failed"}, "b": {"outcome": outcome}},
    }
    assert not grade(row, phase, baseline=True)["valid"]
    phase["test_results"]["b"]["outcome"] = "passed"
    assert grade(row, phase, baseline=True)["valid"]
    assert not grade(row, phase)["valid"]
    phase["test_results"]["a"]["outcome"] = "passed"
    assert grade(row, phase)["valid"]
    phase["status"] = "timeout"
    assert not grade(row, phase)["valid"]


def test_candidate_cannot_modify_evaluation_test(tmp_path):
    task = load_tasks(ROOT / "data/tasks/cleaned_benchmark.json")["psf__requests-6027"]
    row = load_evaluation(ROOT / DEFAULT_ARTIFACTS, task)
    path = min(patch_paths(row["test_patch"]))
    patch = tmp_path / "candidate.patch"
    patch.write_text(
        "--- a/" + path + "\n+++ b/" + path + "\n@@ -1 +1 @@\n-old\n+new\n"
    )
    runner = DockerSandbox(client=Mock())
    runner.config["results"] = str(tmp_path / "results")
    record = runner.run(task.id, patch_path=patch)
    assert record["status"] == "protected_test_error"
    runner.client.info.assert_not_called()


def test_fingerprint_covers_test_mode_and_artifacts(tmp_path):
    sandbox = DockerSandbox()
    dataset = ROOT / "data/tasks/cleaned_benchmark.json"
    args = (sandbox, dataset, ["psf__requests-6027"])
    a = batch.fingerprint(*args, {"test_mode": "suite"})
    b = batch.fingerprint(*args, {"test_mode": "issue"})
    assert a != b
    artifact = tmp_path / "evaluation.json"
    artifact.write_text(
        (ROOT / DEFAULT_ARTIFACTS).read_text(encoding="utf-8"), encoding="utf-8"
    )
    sandbox.config["evaluation"] = str(artifact)
    before = batch.fingerprint(*args, {"test_mode": "issue"})
    artifact.write_text("{}")
    assert batch.fingerprint(*args, {"test_mode": "issue"}) != before


def test_upstream_selector_aliases(monkeypatch):
    from raac.harness.layer3_execution.sandbox import issue_runner as runner

    monkeypatch.setattr(runner, "spec", {"repo": "psf/requests"})
    assert (
        runner.upstream_alias("a.py::test_path[/tmp/base/a.py]")
        == "a.py::test_path[/a.py]"
    )
    assert (
        runner.upstream_alias("a.py::test_value[hello world]")
        == "a.py::test_value[hello"
    )
    monkeypatch.setattr(runner, "spec", {"repo": "matplotlib/matplotlib"})
    assert (
        runner.upstream_alias("a.py::test_click[MouseButton.LEFT]")
        == "a.py::test_click[1]"
    )
    assert (
        runner.django_label("test_one (app.tests.Example)")
        == "app.tests.Example.test_one"
    )
