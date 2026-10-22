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


def test_empty_candidate_is_a_noop_that_reaches_evaluation(tmp_path):
    patch = tmp_path / "empty.patch"
    patch.write_bytes(b"")
    runner = DockerSandbox(client=Mock())
    runner.config["results"] = str(tmp_path / "results")
    runner.connect = Mock(side_effect=RuntimeError("fixture Docker boundary"))
    record = runner.run("psf__requests-6027", patch_path=patch)
    runner.connect.assert_called_once()
    assert record["patch_sha256"]
    assert "fixture Docker boundary" in record["error"]


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
    assert (
        runner.django_label("test_one (app.tests.Example.test_one)")
        == "app.tests.Example.test_one"
    )


@pytest.mark.parametrize(
    "repo", ["sphinx-doc/sphinx", "scikit-learn/scikit-learn", "astropy/astropy"]
)
def test_upstream_whitespace_parameter_aliases(repo, monkeypatch):
    from raac.harness.layer3_execution.sandbox import issue_runner as runner

    monkeypatch.setattr(runner, "spec", {"repo": repo})
    assert (
        runner.upstream_alias("a.py::test_case[Loss must be positive]")
        == "a.py::test_case[Loss"
    )


def test_ambiguous_sympy_names_remain_errors(tmp_path, monkeypatch):
    from raac.harness.layer3_execution.sandbox import issue_runner as runner

    monkeypatch.setattr(runner, "REPORT", str(tmp_path / "report.json"))
    monkeypatch.setattr(runner, "spec", {"repo": "sympy/sympy", "ids": ["test_shared"]})
    monkeypatch.setattr(runner, "errors", [])
    monkeypatch.setattr(runner, "selection_errors", {})
    monkeypatch.setattr(runner, "found", [])
    from types import SimpleNamespace

    items = [
        SimpleNamespace(nodeid="a.py::test_shared", name="test_shared"),
        SimpleNamespace(nodeid="b.py::test_shared", name="test_shared"),
    ]
    with pytest.raises(pytest.UsageError):
        runner.SelectorPlugin().pytest_collection_modifyitems(None, Mock(), items)
    diagnostic = json.loads((tmp_path / "report.json").read_text())
    assert diagnostic["selection_errors"]["test_shared"]["reason"] == "ambiguous"
    assert len(diagnostic["selection_errors"]["test_shared"]["matches"]) == 2


def test_selector_preserves_collection_order(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from raac.harness.layer3_execution.sandbox import issue_runner as runner

    monkeypatch.setattr(runner, "REPORT", str(tmp_path / "report.json"))
    monkeypatch.setattr(
        runner, "spec", {"ids": ["a.py::test_last", "a.py::test_first"]}
    )
    for name, value in [
        ("errors", []),
        ("selection_errors", {}),
        ("found", []),
        ("results", {}),
    ]:
        monkeypatch.setattr(runner, name, value)
    items = [
        SimpleNamespace(nodeid="a.py::" + name, name=name)
        for name in ["test_first", "test_excluded", "test_last"]
    ]
    first, excluded, last = items
    config = Mock()
    runner.SelectorPlugin().pytest_collection_modifyitems(None, config, items)
    assert items == [first, last]
    config.hook.pytest_deselected.assert_called_once_with(items=[excluded])


def test_missing_collection_root_is_explicit(tmp_path, monkeypatch):
    from raac.harness.layer3_execution.sandbox import issue_runner as runner

    path = tmp_path / "selectors.json"
    path.write_text(
        json.dumps(
            {"framework": "pytest", "roots": [str(tmp_path / "generated_only.py")]}
        )
    )
    monkeypatch.setattr(runner.sys, "argv", ["runner", str(path)])
    monkeypatch.setattr(runner, "errors", [])
    assert runner.main() == 4
    assert "collection root missing" in runner.errors[0]


def test_sympy_expansion_retains_all_cases_and_cannot_hide_failure(
    tmp_path, monkeypatch
):
    from types import SimpleNamespace

    from raac.harness.layer3_execution.sandbox import issue_runner as runner

    monkeypatch.setattr(runner, "REPORT", str(tmp_path / "report.json"))
    monkeypatch.setattr(
        runner,
        "spec",
        {
            "repo": "sympy/sympy",
            "ids": ["test_shared"],
            "selector_policy": "sympy-all-matches-v1",
        },
    )
    for name, value in [
        ("errors", []),
        ("selection_errors", {}),
        ("found", []),
        ("results", {}),
    ]:
        monkeypatch.setattr(runner, name, value)
    items = [
        SimpleNamespace(nodeid=p + "::test_shared", name="test_shared")
        for p in ["a.py", "b.py"]
    ]
    plugin = runner.SelectorPlugin()
    plugin.pytest_collection_modifyitems(None, Mock(), items)
    assert len(items) == 2
    for item, failed in zip(items, [True, False], strict=True):
        plugin.pytest_runtest_logreport(
            SimpleNamespace(
                nodeid=item.nodeid,
                when="call",
                longrepr="assertion" if failed else "",
                failed=failed,
                skipped=False,
            )
        )
    result = runner.results["test_shared"]
    assert result["outcome"] == "mixed"
    assert len(result["cases"]) == 2
    assert not grade(
        {"FAIL_TO_PASS": ["test_shared"], "PASS_TO_PASS": []},
        {"status": "test_failed", "test_results": runner.results},
    )["valid"]


def test_expansion_policy_changes_only_evaluation_and_retired_override_is_removed():
    from raac.harness.layer3_execution.sandbox.docker_sandbox import (
        dockerfile,
        load_profile,
    )

    tasks = load_tasks(ROOT / "data/tasks/cleaned_benchmark.json")
    profile = load_profile(
        ROOT / "configs/repos/sympy.yaml", tasks["sympy__sympy-14237"]
    )
    row = load_evaluation(ROOT / DEFAULT_ARTIFACTS, tasks["sympy__sympy-14237"])
    assert payload(row, profile)["selector_policy"] == "unique-v1"
    profile = dict(profile, selector_policy="sympy-all-matches-v1")
    unique = dict(profile, selector_policy="unique-v1")
    assert dockerfile(profile) == dockerfile(unique)
    other = next(
        t
        for t in tasks.values()
        if t.repo == "sympy/sympy" and t.id != "sympy__sympy-14237"
    )
    assert "selector_policy" not in load_profile(
        ROOT / "configs/repos/sympy.yaml", other
    )


def test_all_effective_profiles_have_consistent_pip_pins():
    from raac.harness.layer3_execution.sandbox.docker_sandbox import (
        load_profile,
        resolve_path,
    )

    sandbox = DockerSandbox()
    for task in load_tasks(ROOT / "data/tasks/cleaned_benchmark.json").values():
        profile = load_profile(
            resolve_path(ROOT, sandbox.config["profiles"][task.repo]), task
        )
        for command in profile.get("dependency_commands", []):
            pins = {}
            for argument in command:
                if "==" in argument:
                    name, version = argument.split("==", 1)
                    assert name not in pins or pins[name] == version, task.id
                    pins[name] = version


@pytest.mark.parametrize("skipped,expected", [(True, "xfail"), (False, "xpass")])
def test_empty_xfail_reason_is_not_skip_or_pass(
    tmp_path, monkeypatch, skipped, expected
):
    from types import SimpleNamespace

    from raac.harness.layer3_execution.sandbox import issue_runner as runner

    monkeypatch.setattr(runner, "REPORT", str(tmp_path / "report.json"))
    monkeypatch.setattr(runner, "results", {})
    plugin = runner.SelectorPlugin()
    node = "test_category.py::test_plot"
    plugin.node_map = {node: [node]}
    plugin.outcomes = {node: {}}
    plugin.pytest_runtest_logreport(
        SimpleNamespace(
            nodeid=node,
            when="call",
            longrepr="",
            wasxfail="",
            skipped=skipped,
            failed=False,
        )
    )
    assert runner.results[node]["outcome"] == expected
    row = {"FAIL_TO_PASS": [], "PASS_TO_PASS": [node]}
    phase = {"status": "passed", "test_results": runner.results}
    assert not grade(row, phase)["valid"]
