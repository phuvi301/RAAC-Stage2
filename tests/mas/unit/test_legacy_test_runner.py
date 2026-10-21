"""The standalone helper runs under project Python, including Python 3.8."""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from raac.harness.layer3_execution import local_test_runner


RUNTIMES = list(dict.fromkeys([sys.executable, *filter(None, [os.getenv("RAAC_TEST_PYTHON38")])]))


def helper_run(tmp_path, python, *, command, cwd="nested", env=None, report_path=None):
    root = tmp_path / "repo"
    (root / "nested").mkdir(parents=True, exist_ok=True)
    report = tmp_path / "observed.json"
    config = {"workspace_root": str(root), "command": command, "cwd": cwd,
              "env": env or {}, "report_path": report_path,
              "default_report_path": str(tmp_path / "junit.xml"),
              "observed_report_path": str(report)}
    process = subprocess.run([python, "-I", "-B", str(Path(local_test_runner.__file__)), json.dumps(config)],
                             capture_output=True, text=True, timeout=20)
    assert report.is_file(), process.stderr
    return process, json.loads(report.read_text(encoding="utf-8"))


@pytest.mark.parametrize("python", RUNTIMES)
def test_unittest_helper_uses_nested_cwd_and_only_inside_pythonpath(tmp_path, python):
    nested = tmp_path / "repo/nested"
    (nested / "src").mkdir(parents=True)
    (nested / "src/legacy_fixture.py").write_text("VALUE = 42\n", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "forbidden_import.py").write_text("VALUE = 99\n", encoding="utf-8")
    (nested / "test_case.py").write_text(
        "import unittest, importlib.util, legacy_fixture\n"
        "class Case(unittest.TestCase):\n"
        " def test_ok(self):\n"
        "  self.assertEqual(legacy_fixture.VALUE, 42)\n"
        "  self.assertIsNone(importlib.util.find_spec('forbidden_import'))\n", encoding="utf-8")
    process, report = helper_run(tmp_path, python, command=[python, "-m", "unittest", "discover"],
                                 env={"PYTHONPATH": os.pathsep.join(["src", str(outside)])})
    assert process.returncode == 0, report
    assert report["status"] == "passed" and report["tests_run"] == 1


@pytest.mark.parametrize("python", RUNTIMES)
def test_custom_junit_report_with_project_python(tmp_path, python):
    nested = tmp_path / "repo/nested"
    nested.mkdir(parents=True)
    (nested / "checks.py").write_text(
        "from pathlib import Path\nPath('result.xml').write_text("
        "'<testsuite><testcase name=\"fixture\"/></testsuite>')\n", encoding="utf-8")
    process, report = helper_run(tmp_path, python, command=[python, "checks.py"], report_path="result.xml")
    assert process.returncode == 0, report
    assert report["test_cases"] == ["fixture"] and report["tests_run"] == 1


@pytest.mark.parametrize("python", RUNTIMES)
@pytest.mark.parametrize("escape", ("cwd", "report"))
def test_legacy_helper_preserves_path_boundaries(tmp_path, python, escape):
    cwd = "../../outside" if escape == "cwd" else "nested"
    process, report = helper_run(tmp_path, python, command=[python, "-c", "raise AssertionError('must not run')"],
                                 cwd=cwd, report_path="../../escape.xml" if escape == "report" else None)
    assert process.returncode == 1
    assert report["status"] == "error" and report["tests_run"] == 0
    assert "Invalid test directory" in report["summary"] if escape == "cwd" else "Invalid report path" in report["summary"]
    assert not (tmp_path / "escape.xml").exists()


@pytest.mark.parametrize("python", RUNTIMES)
@pytest.mark.parametrize("scenario,expected_status,expected_failures", (
    ("pass_xfail", "passed", 1), ("only_xfail", "failed", 1),
    ("pass_skip", "failed", 0), ("strict_xpass", "failed", 0),
    ("pass_fail", "failed", 0),
))
def test_pytest_expected_failures_do_not_hide_skips_or_real_failures(
        tmp_path, python, scenario, expected_status, expected_failures):
    nested = tmp_path / "repo/nested"
    nested.mkdir(parents=True)
    source = "import pytest\n"
    if scenario != "only_xfail":
        source += "def test_pass(): assert True\n"
    if "xfail" in scenario:
        source += "@pytest.mark.xfail(reason='known baseline failure', strict=True)\ndef test_known(): assert False\n"
    elif scenario == "pass_skip":
        source += "@pytest.mark.skip(reason='not executed')\ndef test_skipped(): assert False\n"
    elif scenario == "strict_xpass":
        source += "@pytest.mark.xfail(reason='unexpected success', strict=True)\ndef test_xpass(): assert True\n"
    else:
        source += "def test_failed(): assert False\n"
    (nested / "test_case.py").write_text(source, encoding="utf-8")
    process, report = helper_run(tmp_path, python, command=[python, "-m", "pytest", "test_case.py", "-q"],
                                 env={"PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"})
    assert report["status"] == expected_status, report
    assert (process.returncode == 0) == (expected_status == "passed")
    assert report["expected_failures"] == expected_failures
    if expected_failures:
        assert report["skipped"] == 1
        assert any(item["status"] == "skipped" and "Expected failure" in item["details"]
                   for item in report["results"])
    if scenario in {"strict_xpass", "pass_fail"}:
        assert report["failed"] == 1


@pytest.mark.parametrize("python", RUNTIMES)
@pytest.mark.parametrize("unexpected", (False, True))
def test_unittest_expected_failures_and_unexpected_successes(tmp_path, python, unexpected):
    nested = tmp_path / "repo/nested"
    nested.mkdir(parents=True)
    (nested / "test_case.py").write_text(
        "import unittest\nclass Case(unittest.TestCase):\n"
        " def test_pass(self): self.assertTrue(True)\n"
        " @unittest.expectedFailure\n def test_known(self): self.assertTrue(" + str(unexpected) + ")\n",
        encoding="utf-8")
    process, report = helper_run(tmp_path, python, command=[python, "-m", "unittest", "discover"])
    assert report["status"] == ("failed" if unexpected else "passed"), report
    assert report["failed"] == int(unexpected)
    assert report["expected_failures"] == int(not unexpected)
    assert process.returncode == int(unexpected)


def test_expected_failure_counts_are_framework_metadata():
    from raac.collaboration.levels.l3_collaboration.chain_state import execution_report
    output = {"tests_to_run": ["passed", "known_xfail"], "results": [
        {"test": "passed", "status": "passed", "details": ""},
        {"test": "known_xfail", "status": "skipped", "details": "Expected failure"}],
        "verdict": "pass", "covered_ac_ids": ["AC1"], "blocking_issues": []}
    report = execution_report(output, {"status": "passed", "tests_run": 2, "failed": 0,
                                      "skipped": 1, "expected_failures": 1}, "patch-hash")
    assert report.status == "passed" and report.expected_failures == report.skipped == 1
