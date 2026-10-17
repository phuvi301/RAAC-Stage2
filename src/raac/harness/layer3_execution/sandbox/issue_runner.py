"""Copied into a container, runs with the repository's Python (including 2.7)."""

import json
import os
import re
import runpy
import sys
import traceback
import unittest

REPORT = "/workspace/raac-tests.json"
results = {}
found = []
errors = []
spec = {}


def save():
    with open(REPORT, "w") as output:
        json.dump({"tests": results, "found": found, "errors": errors}, output)


def key(value):
    return value


def upstream_alias(value):
    repo = spec.get("repo", "")
    if repo == "matplotlib/matplotlib":
        value = value.replace("MouseButton.LEFT", "1").replace("MouseButton.RIGHT", "3")
    if repo in (
        "psf/requests",
        "pylint-dev/pylint",
        "pytest-dev/pytest",
        "pydata/xarray",
        "matplotlib/matplotlib",
        "pallets/flask",
        "mwaskom/seaborn",
    ):
        value = value.split()[0]
    if repo in ("psf/requests", "pylint-dev/pylint"):
        match = re.match(r"(.*?)\[(.*)\]$", value)
        if match:
            head, option = match.groups()
            if (
                option.startswith("/")
                and not option.startswith("//")
                and "*" not in option
            ):
                value = head + "[/" + option.split("/")[-1] + "]"
    return value


class SelectorPlugin:
    def __init__(self):
        self.node_map = {}
        self.outcomes = {}

    def pytest_collection_modifyitems(self, session, config, items):
        selected = []
        for wanted in spec["ids"]:
            matches = [
                item
                for item in items
                if item.nodeid == wanted or ("::" not in wanted and item.name == wanted)
            ]
            if not matches and "::" in wanted:
                matches = [
                    item for item in items if upstream_alias(item.nodeid) == wanted
                ]
            if not matches or ("::" not in wanted and len(matches) != 1):
                errors.append("selector missing or ambiguous: " + wanted)
                continue
            found.append(wanted)
            self.outcomes[wanted] = {}
            for item in matches:
                self.node_map.setdefault(item.nodeid, []).append(wanted)
                if item not in selected:
                    selected.append(item)
        rejected = [item for item in items if item not in selected]
        if rejected:
            config.hook.pytest_deselected(items=rejected)
        items[:] = selected
        save()
        if errors:
            import pytest

            raise pytest.UsageError("; ".join(errors))

    def pytest_runtest_logreport(self, report):
        if str(getattr(report, "longrepr", "")).startswith("[XPASS(strict)]"):
            outcome = "xpass"
        elif getattr(report, "wasxfail", None):
            outcome = "xfail" if report.skipped else "xpass"
        elif report.failed:
            outcome = "failed" if report.when == "call" else "runner_error"
        elif report.skipped:
            outcome = "skipped"
        elif report.when == "call":
            outcome = "passed"
        else:
            return
        for wanted in self.node_map.get(report.nodeid, []):
            previous = self.outcomes[wanted].get(report.nodeid, {}).get("outcome")
            if (
                previous in ("runner_error", "failed", "xfail", "skipped")
                and outcome == "passed"
            ):
                continue
            self.outcomes[wanted][report.nodeid] = {
                "outcome": outcome,
                "phase": report.when,
                "detail": str(getattr(report, "longrepr", "")),
            }
            expected_nodes = [
                node for node, ids in self.node_map.items() if wanted in ids
            ]
            values = [
                self.outcomes[wanted].get(node, {}).get("outcome", "missing")
                for node in expected_nodes
            ]
            combined = values[0] if len(set(values)) == 1 else "mixed"
            results[wanted] = {
                "outcome": combined,
                "nodeids": expected_nodes,
                "cases": self.outcomes[wanted],
            }
        save()


def django_label(value):
    matched = re.match(r"^(\w+) \(([^)]+)\)$", value)
    return matched.group(2) + "." + matched.group(1) if matched else value


def run_django():
    requested = {django_label(x): x for x in spec["ids"]}
    identities = {}
    outcomes = {}
    original_run = unittest.TextTestRunner.run

    def flatten(suite):
        for test in suite:
            if isinstance(test, unittest.TestSuite):
                for item in flatten(test):  # noqa: UP028 -- Python 2.7 compatibility
                    yield item
            else:
                yield test

    def select_and_run(self, suite):
        tests = list(flatten(suite))
        chosen = []
        for normalized, wanted in requested.items():
            matches = [
                t
                for t in tests
                if t.id() == normalized
                or (t.shortDescription() or "").strip() == wanted
            ]
            if not matches:
                errors.append("Django selector missing: " + wanted)
                continue
            found.append(wanted)
            outcomes[wanted] = {t.id(): {"outcome": "missing"} for t in matches}
            for test in matches:
                identities.setdefault(test.id(), []).append(wanted)
                if test not in chosen:
                    chosen.append(test)
        save()
        if errors:
            raise ValueError("; ".join(errors))
        chosen_ids = {t.id() for t in chosen}
        return original_run(
            self, unittest.TestSuite([t for t in tests if t.id() in chosen_ids])
        )

    unittest.TextTestRunner.run = select_and_run
    for method, outcome in [
        ("addSuccess", "passed"),
        ("addFailure", "failed"),
        ("addError", "failed"),
        ("addSkip", "skipped"),
        ("addExpectedFailure", "xfail"),
        ("addUnexpectedSuccess", "xpass"),
    ]:
        original = getattr(unittest.TextTestResult, method)

        def wrap(original, outcome):
            def record(self, test, *args, **kwargs):
                for wanted in identities.get(test.id(), []):
                    outcomes[wanted][test.id()] = {
                        "outcome": outcome,
                        "detail": str(args),
                    }
                    values = [value["outcome"] for value in outcomes[wanted].values()]
                    combined = values[0] if len(set(values)) == 1 else "mixed"
                    results[wanted] = {
                        "outcome": combined,
                        "nodeids": list(outcomes[wanted]),
                        "cases": outcomes[wanted],
                    }
                    save()
                return original(self, test, *args, **kwargs)

            return record

        setattr(unittest.TextTestResult, method, wrap(original, outcome))
    command = spec["command"]
    script = command[1]
    flags = [x for x in command[2:] if x.startswith("--")]
    sys.argv = [script] + flags + (spec.get("roots") or list(requested))
    sys.path.insert(0, os.path.abspath("tests"))
    runpy.run_path(script, run_name="__main__")


def main():
    global spec
    with open(sys.argv[1]) as source:
        spec = json.load(source)
    sys.path.insert(0, os.getcwd())
    if spec["framework"] == "django":
        run_django()
        return 0
    import pytest

    roots = spec["roots"]
    if not roots:
        raise ValueError("No collection roots")
    args = roots + ["-q"]
    return pytest.main(args, plugins=[SelectorPlugin()])


if __name__ == "__main__":
    code = 3
    try:
        code = main()
    except SystemExit as exc:
        code = exc.code or 0
    except Exception:  # noqa: BLE001 -- runner must persist failures before exit
        errors.append(traceback.format_exc())
        traceback.print_exc()
    finally:
        save()
    sys.exit(code)
