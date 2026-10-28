"""Collect development test results in a separate process on the laptop."""

import contextlib
import io
import json
import os
import re
import site
import subprocess
import sys
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path


def is_pytest(command):
    if not isinstance(command, list) or not command or not all(isinstance(arg, str) for arg in command):
        return False
    executable = command[0].replace("\\", "/").rsplit("/", 1)[-1]
    return executable.lower() in {"pytest", "pytest.exe"} or (
        re.fullmatch(r"python(?:3(?:\.\d+)?)?(?:\.exe)?", executable, re.IGNORECASE) is not None
        and command[1:3] == ["-m", "pytest"])


def normalize_pytest_report(command, report_path):
    """The harness owns pytest reports; never let a model overwrite repository files."""
    if not is_pytest(command):
        return command, report_path
    selected, index = [], 0
    while index < len(command):
        argument = command[index]
        if argument == "--":
            selected.extend(command[index:])
            break
        if argument in {"--junitxml", "--junit-xml"}:
            if index + 1 >= len(command):
                raise ValueError("JUnit option requires a path")
            index += 2
            continue
        if not argument.startswith(("--junitxml=", "--junit-xml=")):
            selected.append(argument)
        index += 1
    return selected, None


def is_within(path, root):
    """Containment check compatible with the project's Python 3.8 runtime."""
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


class RecordingResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.test_ids = []

    def startTest(self, test):
        self.test_ids.append(test.id())
        super().startTest(test)

    def failed_method_ids(self):
        # unittest may record hundreds of failed subTest blocks under one method.
        # Report methods consistently with testsRun and keep subtest detail separately.
        return {getattr(test, "test_case", test).id() for test, _ in self.failures + self.errors}


def junit_report(path):
    if not path.exists() or path.stat().st_size > 2097152:
        return None
    cases = list(ET.parse(path).getroot().iter("testcase"))
    if not cases:
        return None
    results, expected_failures = [], 0
    for case in cases:
        name = (case.get("classname", "") + "." + case.get("name", "")).strip(".") or "<unnamed JUnit case>"
        status, details = "passed", ""
        for tag, outcome in (("error", "error"), ("failure", "failed"), ("skipped", "skipped")):
            element = case.find(tag)
            if element is not None:
                status = outcome
                details = ((element.get("message", "") + "\n" + (element.text or "")).strip())[:2000]
                if tag == "skipped" and element.get("type") == "pytest.xfail":
                    expected_failures += 1
                    details = "Expected failure (pytest.xfail): " + details
                break
        results.append({"test": name, "status": status, "details": details})
    return {"results": results, "tests_run": len(cases),
            "failed": sum(case.find("failure") is not None or case.find("error") is not None for case in cases),
            "skipped": sum(case.find("skipped") is not None for case in cases),
            "expected_failures": expected_failures,
            "test_cases": [(case.get("classname", "") + "." + case.get("name", "")).strip(".") for case in cases]}


def emit_report(report, config):
    destination = config.get("observed_report_path")
    if destination:
        Path(destination).write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
        # The complete report is read from a parent-selected temporary file;
        # hundreds of cases/failure traces must not overflow the pipe budget.
        print("RAAC_TEST_REPORT_STORED=" + json.dumps({key: report.get(key) for key in
              ("status", "tests_run", "failed", "skipped", "expected_failures", "exit_code")}))
    else:
        print("RAAC_TEST_REPORT=" + json.dumps(report))


def main():
    config = json.loads(sys.argv[1])
    command = config["command"]
    root = Path(config["workspace_root"]).resolve()
    cwd = (root / config.get("cwd", ".")).resolve()
    if not is_within(cwd, root):
        raise ValueError("Invalid test directory")
    # -I protects bootstrap imports, but installed user dependencies must remain available.
    if sys.prefix == sys.base_prefix:
        user_site = Path(site.getusersitepackages()).resolve()
        if user_site.is_dir() and not is_within(user_site, root):
            site.addsitedir(str(user_site))
    os.chdir(cwd)
    os.environ.update(config.get("env", {}))
    sys.path[:0] = [str(cwd), str(root)]
    for entry in os.environ.get("PYTHONPATH", "").split(os.pathsep):
        if entry:
            candidate = (cwd / entry).resolve()
            if is_within(candidate, root):
                sys.path.insert(0, str(candidate))
    is_python = re.fullmatch(r"python(?:3(?:\.\d+)?)?(?:\.exe)?", Path(command[0]).name, re.I) is not None
    output = io.StringIO()
    if is_python and command[1:3] == ["-m", "unittest"]:
        arguments = command[3:] or ["discover"]
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            program = unittest.TestProgram(module=None, argv=["unittest", *arguments], exit=False,
                testRunner=unittest.TextTestRunner(stream=output, resultclass=RecordingResult))
        result = program.result
        details = {}
        for status, entries in (("failed", result.failures), ("error", result.errors)):
            for test, traceback in entries:
                name = getattr(test, "test_case", test).id()
                previous = details.get(name, {})
                details[name] = {"status": status, "details": (previous.get("details", "") + "\n" + traceback)[-2000:]}
        for test, reason in result.skipped:
            details[test.id()] = {"status": "skipped", "details": reason}
        for test, traceback in result.expectedFailures:
            details[test.id()] = {"status": "skipped", "details": ("Expected failure (unittest): " + traceback)[-2000:]}
        for test in result.unexpectedSuccesses:
            details[test.id()] = {"status": "failed", "details": "Unexpected success"}
        results = [{"test": name, **details.get(name, {"status": "passed", "details": ""})} for name in result.test_ids]
        stats = {"results": results, "tests_run": result.testsRun,
                 "failed": len(result.failed_method_ids()) + len(result.unexpectedSuccesses),
                 "subtest_failures": sum(hasattr(test, "test_case") for test, _ in result.failures + result.errors),
                 "skipped": len(result.skipped) + len(result.expectedFailures),
                 "expected_failures": len(result.expectedFailures), "test_cases": result.test_ids}
        exit_code = 0 if result.wasSuccessful() else 1
        text = output.getvalue()
    else:
        pytest = is_pytest(command)
        report = Path(config["default_report_path"]) if pytest else None
        if config.get("report_path") and not pytest:
            report = (cwd / config["report_path"]).resolve()
            if not is_within(report, root):
                raise ValueError("Invalid report path")
        if report and report.exists():
            report.unlink()
        if pytest:
            command, _ = normalize_pytest_report(command, None)
            position = command.index("--") if "--" in command else len(command)
            command.insert(position, "--junitxml=" + str(report))
        completed = subprocess.run(command, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   text=True, errors="replace", env=os.environ.copy())
        exit_code, text = completed.returncode, completed.stdout
        stats = junit_report(report) if report else None
        if stats is None:
            # Node's TAP reporter. Other runners need JUnit; do not invent counts.
            tests = re.findall(r"^# tests (\d+)\s*$", text, re.M)
            fails = re.findall(r"^# fail (\d+)\s*$", text, re.M)
            skips = re.findall(r"^# skipped (\d+)\s*$", text, re.M)
            names = re.findall(r"^\s*# Subtest: (.+)$", text, re.M)
            if tests and names:
                stats = {"tests_run": int(tests[-1]), "failed": int(fails[-1]) if fails else 0,
                         "skipped": int(skips[-1]) if skips else 0, "test_cases": names}
    if not stats or stats["tests_run"] <= 0:
        report = {"status": "error", "tests_run": 0, "failed": 0, "test_cases": [],
                  "summary": "No reliable test report. Select unittest, pytest, Node TAP or a JUnit report_path.\n" + text[-14000:]}
    else:
        # An observed expected failure is different from an unexecuted skip.
        # Keep skipped totals intact and require at least one actual pass.
        passed = (exit_code == 0 and stats["failed"] == 0
                  and stats["skipped"] == stats.get("expected_failures", 0)
                  and stats["tests_run"] > stats["skipped"])
        report = {**stats, "status": "passed" if passed else "failed", "summary": text[-16000:]}
    report["command"] = command
    report["exit_code"] = exit_code
    if is_pytest(config["command"]):
        report["report_note"] = "Pytest JUnit output is managed in a temporary directory outside the repository."
    emit_report(report, config)
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        emit_report({"status": "error", "tests_run": 0, "failed": 0,
            "test_cases": [], "summary": "Test command unavailable or invalid: " + type(error).__name__ + ": " + str(error)},
            json.loads(sys.argv[1]))
        raise SystemExit(1)
