"""Grade a repository on this machine the way the benchmark evaluator does, without Docker.

Run it inside the project's virtual environment, from the project's repository, after the
task's hidden test patch has been applied:

    python <framework>/scripts/local_grade.py --task-id django__django-28373

Like the evaluator it runs the test modules the task touches, then reports only the task's
FAIL_TO_PASS and PASS_TO_PASS tests; other tests in those modules are run but not graded.
A test skipped on this machine is reported as "not verifiable here", not as a failure.

Standard library only and Python 3.6+ syntax, so the project's own (old) interpreter can run it.
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

FRAMEWORK = Path(__file__).resolve().parents[1]
EVALUATION = FRAMEWORK / "data" / "evaluation" / "swe_bench_verified.json"
STATUS = {"ok": "passed", "passed": "passed", "fail": "failed", "failed": "failed", "error": "failed",
          "skipped": "skipped", "expected failure": "skipped", "xfail": "skipped",
          "unexpected success": "failed", "xpass": "failed"}


def as_list(value):
    if isinstance(value, list):
        return value
    import ast
    return ast.literal_eval(value)


def patch_files(patch):
    return sorted(set(re.findall(r"^\+\+\+ b/(\S+)", patch, re.M)))


def django_command(row):
    """The evaluator's Django roots: test modules in the test patch plus the modules of the graded classes."""
    roots = set()
    for path in patch_files(row["test_patch"]):
        if path.startswith("tests/") and path.endswith(".py") and "/test" in path and not path.endswith("__init__.py"):
            roots.add(path[len("tests/"):-3].replace("/", "."))
    for name in as_list(row["FAIL_TO_PASS"]) + as_list(row["PASS_TO_PASS"]):
        match = re.match(r"^\w+ \(([\w.]+)\)$", name)
        if match and "." in match.group(1):
            roots.add(match.group(1).rsplit(".", 1)[0])
    return [sys.executable, "tests/runtests.py", "--settings=test_sqlite", "--parallel=1", "-v", "2", *sorted(roots)]


def django_results(output):
    """`-v 2` prints `name (module.Class) ... ok`; a test with a docstring prints its name on one line and
    `docstring ... ok` on the next. Both spellings are recorded so either form of the ID matches."""
    results, pending = {}, None
    for line in output.splitlines():
        line = line.rstrip()
        match = re.match(r"^(.*?) \.\.\. (ok|FAIL|ERROR|skipped|expected failure|unexpected success)\b", line)
        if match:
            status = STATUS[match.group(2).lower()]
            results[match.group(1)] = status
            if pending:
                results[pending] = status
            pending = None
        elif re.match(r"^\w+ \([\w.]+\)$", line):
            pending = line
    return results


def pytest_command(row):
    ids = as_list(row["FAIL_TO_PASS"]) + as_list(row["PASS_TO_PASS"])
    files = sorted({name.split("::")[0] for name in ids if "::" in name})
    if not files:  # bare function names (sympy): the evaluator runs the test patch's files
        files = [path for path in patch_files(row["test_patch"]) if path.endswith(".py")]
    return [sys.executable, "-m", "pytest", "-v", "-p", "no:cacheprovider", "-p", "no:randomly", *files]


def pytest_results(output):
    results = {}
    for line in output.splitlines():
        match = re.match(r"^(\S.*?::.+?) (PASSED|FAILED|ERROR|SKIPPED|XFAIL|XPASS)\b", line.strip())
        if match:
            node, status = match.group(1), STATUS[match.group(2).lower()]
            results[node] = status
            function = node.split("::")[-1]
            results.setdefault(function, status)  # sympy IDs are bare function names
            results.setdefault(re.sub(r"\[.*\]$", "", function), status)
    return results


def grade(names, results):
    groups = {"passed": [], "failed": [], "skipped": [], "missing": []}
    for name in names:
        groups[results.get(name, "missing")].append(name)
    return groups


def main(argv=None):
    parser = argparse.ArgumentParser(description="Grade the current repository against a benchmark task, locally")
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--show-output", action="store_true", help="print the raw test output too")
    args = parser.parse_args(argv)
    row = json.loads(EVALUATION.read_text(encoding="utf-8"))["tasks"].get(args.task_id)
    if row is None:
        parser.error("unknown task id")
    django = row["repo"] == "django/django"
    command = django_command(row) if django else pytest_command(row)
    print("Running:", " ".join(command[1:]), flush=True)
    completed = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               universal_newlines=True, errors="replace")
    if args.show_output:
        print(completed.stdout)
    results = django_results(completed.stdout) if django else pytest_results(completed.stdout)
    if not results:
        print(completed.stdout[-3000:])
        print("\nNo test results could be read; see the output above (wrong directory or environment?).")
        return 2
    verdict = True
    for label, key in (("FAIL_TO_PASS", "FAIL_TO_PASS"), ("PASS_TO_PASS", "PASS_TO_PASS")):
        names = as_list(row[key])
        groups = grade(names, results)
        print("\n{}: {}/{} passed, {} failed, {} skipped here (not verifiable on this machine), {} not found".format(
            label, len(groups["passed"]), len(names), len(groups["failed"]), len(groups["skipped"]),
            len(groups["missing"])))
        for status in ("failed", "missing", "skipped"):
            for name in groups[status]:
                print("  {:8} {}".format(status, name))
        verdict = verdict and not groups["failed"] and not groups["missing"]
    print("\nLocal verdict:", "RESOLVED (except tests skipped here)" if verdict else "NOT RESOLVED",
          "- the Docker evaluator remains the official result.")
    return 0 if verdict else 1


if __name__ == "__main__":
    raise SystemExit(main())
