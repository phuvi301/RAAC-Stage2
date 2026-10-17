"""Evaluator-only SWE-bench artifacts. Never imported by the agent Task loader."""

import hashlib
import json
from pathlib import Path, PurePosixPath

DEFAULT_ARTIFACTS = "data/evaluation/swe_bench_verified.json"


def sha(data):
    return hashlib.sha256(data).hexdigest()


def selectors(value):
    value = json.loads(value) if isinstance(value, str) else value
    if not isinstance(value, list) or any(
        not isinstance(x, str) or not x.strip() or "\n" in x or "\x00" in x
        for x in value
    ):
        raise ValueError("Invalid test selectors")
    if len(value) != len(set(value)):
        raise ValueError("Duplicate test selectors")
    return value


def import_verified(raw, cleaned, output):
    source = Path(raw).read_bytes()
    rows = [
        json.loads(line) for line in source.decode("utf-8").splitlines() if line.strip()
    ]
    data = json.loads(Path(cleaned).read_text(encoding="utf-8"))
    index = {}
    for row in rows:
        index.setdefault((row["repo"], row["base_commit"]), []).append(row)
    tasks = {}
    for repo in data["repos"]:
        for issue in repo["issues"]:
            found = index.get((repo["repo"], issue["base_commit"]), [])
            if len(found) != 1:
                raise ValueError("Missing/ambiguous raw match: " + issue["id"])
            row = found[0]
            number = row["instance_id"].rsplit("-", 1)[1]
            url = "https://github.com/" + row["repo"] + "/pull/" + number
            if issue.get("url", "").rstrip("/") != url:
                raise ValueError("PR URL mismatch: " + issue["id"])
            if issue["id"] in tasks:
                raise ValueError("Duplicate task ID")
            f2p, p2p = selectors(row["FAIL_TO_PASS"]), selectors(row["PASS_TO_PASS"])
            if not f2p or set(f2p) & set(p2p):
                raise ValueError("Invalid F2P/P2P groups")
            tasks[issue["id"]] = {
                "task_id": issue["id"],
                "instance_id": row["instance_id"],
                "repo": row["repo"],
                "pr_url": url,
                "base_commit": row["base_commit"],
                "version": row["version"],
                "environment_setup_commit": row["environment_setup_commit"],
                "test_patch": row["test_patch"],
                "gold_patch": row["patch"],
                "FAIL_TO_PASS": f2p,
                "PASS_TO_PASS": p2p,
            }
    result = {
        "schema_version": 1,
        "source_sha256": sha(source),
        "source_name": Path(raw).name,
        "tasks": tasks,
    }
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    Path(output).write_text(
        json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return {"tasks": len(tasks), "source_sha256": sha(source)}


def load_evaluation(path, task):
    row = json.loads(Path(path).read_text(encoding="utf-8"))["tasks"][task.id]
    if row["repo"] != task.repo or row["base_commit"] != task.base_commit:
        raise ValueError("Evaluator task/base mismatch")
    selectors(row["FAIL_TO_PASS"])
    selectors(row["PASS_TO_PASS"])
    return row


def patch_paths(patch):
    paths = set()
    for line in patch.splitlines():
        if line.startswith(("--- ", "+++ ")):
            value = line[4:].split("\t")[0]
            if value == "/dev/null":
                continue
            if value.startswith(("a/", "b/")):
                value = value[2:]
            path = PurePosixPath(value)
            if path.is_absolute() or ".." in path.parts or value.startswith('"'):
                raise ValueError("Unsupported/unsafe patch path")
            paths.add(value)
    if not paths:
        raise ValueError("Patch has no file paths")
    return paths


def payload(row, profile):
    ids = list(dict.fromkeys(row["FAIL_TO_PASS"] + row["PASS_TO_PASS"]))
    roots = sorted({x.split("::")[0] for x in ids if "::" in x})
    if row["repo"] == "django/django":
        roots = sorted(
            {
                p.removeprefix("tests/")[:-3].replace("/", ".")
                for p in patch_paths(row["test_patch"])
                if p.startswith("tests/")
                and p.endswith(".py")
                and "/test" in p
                and not p.endswith("__init__.py")
            }
        )
        for identifier in ids:
            if identifier.endswith(")") and " (" in identifier:
                class_name = identifier.rsplit(" (", 1)[1][:-1]
                if "." in class_name:
                    roots.append(class_name.rsplit(".", 1)[0])
        roots = sorted(set(roots))
        if not roots:
            raise ValueError("No Django collection modules in test patch")
    if row["repo"] == "sympy/sympy":
        roots = sorted(p for p in patch_paths(row["test_patch"]) if p.endswith(".py"))
    return {
        "ids": ids,
        "roots": roots,
        "framework": profile.get("test_framework", "pytest"),
        "command": profile["test_command"],
        "repo": row["repo"],
    }


def grade(row, phase, baseline=False):
    tests = phase.get("test_results", {})
    groups = {}
    for group in ("FAIL_TO_PASS", "PASS_TO_PASS"):
        expected = "failed" if baseline and group == "FAIL_TO_PASS" else "passed"
        groups[group] = {
            "expected": expected,
            "total": len(row[group]),
            "matched": sum(
                tests.get(x, {}).get("outcome") == expected for x in row[group]
            ),
            "unmet": [
                x for x in row[group] if tests.get(x, {}).get("outcome") != expected
            ],
        }
    valid = phase["status"] in {"passed", "test_failed"} and all(
        not g["unmet"] for g in groups.values()
    )
    return {
        "valid": valid,
        "groups": groups,
        "required": sum(g["total"] for g in groups.values()),
        "found": sum(x in tests for x in row["FAIL_TO_PASS"] + row["PASS_TO_PASS"]),
        "executed": sum(
            v.get("outcome") in {"passed", "failed"} for v in tests.values()
        ),
        "failure_reason_review_required": baseline,
    }
