"""Collect development test results in a separate process on the laptop."""

import contextlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import unittest
import xml.etree.ElementTree as ET


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
    results = []
    for case in cases:
        name = (case.get("classname", "") + "." + case.get("name", "")).strip(".") or "<unnamed JUnit case>"
        status, details = "passed", ""
        for tag, outcome in (("error", "error"), ("failure", "failed"), ("skipped", "skipped")):
            element = case.find(tag)
            if element is not None:
                status = outcome
                details = ((element.get("message", "") + "\n" + (element.text or "")).strip())[:2000]
                break
        results.append({"test": name, "status": status, "details": details})
    return {"results": results, "tests_run": len(cases),
            "failed": sum(case.find("failure") is not None or case.find("error") is not None for case in cases),
            "skipped": sum(case.find("skipped") is not None for case in cases),
            "test_cases": [(case.get("classname", "") + "." + case.get("name", "")).strip(".") for case in cases]}


def main():
    config = json.loads(sys.argv[1])
    command = config["command"]
    root = Path(config["workspace_root"]).resolve()
    cwd = (root / config.get("cwd", ".")).resolve()
    if not cwd.is_relative_to(root):
        raise ValueError("Invalid test directory")
    os.chdir(cwd)
    os.environ.update(config.get("env", {}))
    sys.path[:0] = [str(cwd), str(root)]
    for entry in os.environ.get("PYTHONPATH", "").split(os.pathsep):
        if entry:
            candidate = (cwd / entry).resolve()
            if candidate.is_relative_to(root):
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
        for test in result.unexpectedSuccesses:
            details[test.id()] = {"status": "failed", "details": "Unexpected success"}
        results = [{"test": name, **details.get(name, {"status": "passed", "details": ""})} for name in result.test_ids]
        stats = {"results": results, "tests_run": result.testsRun, "failed": len(result.failed_method_ids()),
                 "subtest_failures": sum(hasattr(test, "test_case") for test, _ in result.failures + result.errors),
                 "skipped": len(result.skipped), "test_cases": result.test_ids}
        exit_code = 0 if result.wasSuccessful() else 1
        text = output.getvalue()
    else:
        pytest = Path(command[0]).name.lower() in {"pytest", "pytest.exe"} or (is_python and command[1:3] == ["-m", "pytest"])
        report = Path(config["default_report_path"]) if pytest else None
        if config.get("report_path"):
            report = (cwd / config["report_path"]).resolve()
            if not report.is_relative_to(root):
                raise ValueError("Invalid report path")
        if report and report.exists():
            report.unlink()
        if pytest:
            command = [*command, "--junitxml=" + str(report)]
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
        passed = exit_code == 0 and stats["failed"] == 0 and stats["skipped"] == 0
        report = {**stats, "status": "passed" if passed else "failed", "summary": text[-16000:]}
    report["command"] = command
    report["exit_code"] = exit_code
    print("RAAC_TEST_REPORT=" + json.dumps(report))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print("RAAC_TEST_REPORT=" + json.dumps({"status": "error", "tests_run": 0, "failed": 0,
            "test_cases": [], "summary": "Test command unavailable or invalid: " + type(error).__name__ + ": " + str(error)}))
        raise SystemExit(1)
