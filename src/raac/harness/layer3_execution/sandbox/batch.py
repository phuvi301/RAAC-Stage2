"""Sequential baseline batches, durable checkpoints, and provenance-checked resume."""

import csv
import json
import re
import uuid
from collections import Counter
from pathlib import Path

import yaml

from raac.benchmark.dataset import load_tasks

from .docker_sandbox import (
    ROOT,
    DockerSandbox,
    cache_lock,
    digest,
    load_profile,
    resolve_path,
)
from .policy import SandboxPolicy

RETRYABLE = {
    "preparation_error",
    "execution_error",
    "build_timeout",
    "timeout",
    "interrupted",
}
EXECUTED = {
    "passed",
    "test_failed",
    "validated",
    "validation_failed",
    "resolved",
    "unresolved",
    "pilot_passed",
}


def atomic_json(path, data):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    temporary.replace(path)


def check_profiles(sandbox, dataset=None):
    dataset_path = resolve_path(sandbox.root, dataset or sandbox.config["dataset"])
    tasks = load_tasks(dataset_path)
    errors = []
    covered = 0
    for repo in sorted({t.repo for t in tasks.values()}):
        name = sandbox.config.get("profiles", {}).get(repo)
        if name is None:
            errors.append(f"{repo}: profile_missing")
            continue
        path = resolve_path(sandbox.root, name)
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
            for task_id in raw.get("task_overrides", {}):
                if task_id not in tasks or tasks[task_id].repo != repo:
                    errors.append(f"{repo}: unknown override {task_id}")
            for task in tasks.values():
                if task.repo == repo:
                    load_profile(path, task)
                    covered += 1
        except (OSError, ValueError, TypeError, KeyError) as exc:
            errors.append(f"{repo}: {exc}")
    return {
        "status": "valid" if not errors else "invalid",
        "tasks": len(tasks),
        "covered": covered,
        "repos": len({t.repo for t in tasks.values()}),
        "errors": errors,
    }


def fingerprint(sandbox, dataset, selection, budget):
    paths = {
        sandbox.config_path,
        dataset,
        sandbox.root / "registry/VERSION",
        ROOT / "docker/Dockerfile.sandbox",
        Path(__file__),
        Path(__file__).with_name("docker_sandbox.py"),
        Path(__file__).with_name("build.py"),
        Path(__file__).with_name("policy.py"),
    }
    paths.update(
        resolve_path(sandbox.root, p) for p in sandbox.config["profiles"].values()
    )
    paths.update(
        Path(__file__).with_name(name)
        for name in ("evaluation.py", "issue_runner.py", "optimization.py", "cache.py")
    )
    if budget.get("test_mode") == "issue":
        from .evaluation import DEFAULT_ARTIFACTS

        paths.add(
            resolve_path(
                sandbox.root, sandbox.config.get("evaluation", DEFAULT_ARTIFACTS)
            )
        )
    files = {str(p): digest(p.read_bytes()) for p in sorted(paths)}
    return digest(
        json.dumps(
            {"files": files, "selection": selection, "budget": budget}, sort_keys=True
        ).encode()
    )


def report(directory, manifest):
    rows = []
    for task_id, task in manifest["tasks"].items():
        attempts = task["attempts"]
        latest = attempts[-1] if attempts else {}
        baseline = latest.get("baseline") or {}
        policy = (
            latest.get("provenance", {}).get("effective_config", {}).get("policy", {})
        )
        stats = baseline.get("test_statistics", baseline.get("pytest")) or {}
        rows.append(
            {
                "task_id": task_id,
                "repo": task["repo"],
                "base_commit": task["base_commit"],
                "profile": latest.get("profile_path"),
                "status": latest.get("status", "not_run"),
                "attempts": len(attempts),
                "runner": baseline.get("runner"),
                "test_mode": latest.get("test_mode", manifest.get("test_mode")),
                "baseline_valid": latest.get("baseline_validation", {}).get("valid"),
                "gold_valid": latest.get("gold_validation", {}).get("valid"),
                "candidate_valid": latest.get("candidate_validation", {}).get("valid"),
                "required_tests": latest.get("baseline_validation", {}).get("required"),
                "found_tests": latest.get("baseline_validation", {}).get("found"),
                "executed_tests": latest.get("baseline_validation", {}).get("executed"),
                "preparation_seconds": latest.get("preparation_seconds"),
                "build_seconds": latest.get(
                    "build_seconds", 0 if latest.get("image_cache_hit") else None
                ),
                "image_cache_hit": latest.get("image_cache_hit"),
                "build_identity": latest.get("build_identity"),
                "evaluation_identity": latest.get("evaluation_identity"),
                "preflight_required": latest.get("preflight", {}).get(
                    "required_selectors"
                ),
                "preflight_found": latest.get("preflight", {}).get("found_count"),
                "baseline_seconds": baseline.get("duration_seconds"),
                "gold_seconds": (latest.get("gold") or {}).get("duration_seconds"),
                "statistics_available": baseline.get("statistics_available", False),
                "network_mode": policy.get("network_mode"),
                "test_timeout_seconds": policy.get("timeout"),
                "build_timeout_seconds": latest.get("build_timeout"),
                "image_id": latest.get("image_id"),
                "duration_seconds": sum(a.get("duration_seconds", 0) for a in attempts),
                "tests": stats.get("tests"),
                "failures": stats.get("failures"),
                "errors": stats.get("errors"),
                "skipped": stats.get("skipped"),
                "expected_failures": stats.get("expected_failures"),
                "unexpected_successes": stats.get("unexpected_successes"),
                "record": latest.get("result_path"),
                "reused_from": latest.get("reused_from"),
                "stage": latest.get("stage", "full"),
                "log": baseline.get("log"),
                "error": latest.get("error"),
            }
        )
    statuses = dict(Counter(row["status"] for row in rows))
    repos = {}
    for row in rows:
        repo = repos.setdefault(
            row["repo"], {"total": 0, "executed": 0, "statuses": {}}
        )
        repo["total"] += 1
        repo["executed"] += row["status"] in EXECUTED
        repo["statuses"][row["status"]] = repo["statuses"].get(row["status"], 0) + 1
    result = {
        "batch_id": manifest["batch_id"],
        "status": manifest["status"],
        "total": len(rows),
        "validated": sum(r["status"] == "validated" for r in rows),
        "pilot_passed": sum(r["status"] == "pilot_passed" for r in rows),
        "preflight_passed": sum(r["status"] == "preflight_passed" for r in rows),
        "executed": sum(r["status"] in EXECUTED for r in rows),
        "active_run": manifest.get("active_run"),
        "statuses": statuses,
        "repos": repos,
        "tasks": rows,
    }
    atomic_json(directory / "summary.json", result)
    with (directory / "repos.csv").open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(
            output, fieldnames=["repo", "total", "executed", "statuses"]
        )
        writer.writeheader()
        for repo, values in repos.items():
            writer.writerow(
                {
                    "repo": repo,
                    **values,
                    "statuses": json.dumps(values["statuses"], sort_keys=True),
                }
            )
    if rows:
        with (directory / "summary.csv").open(
            "w", newline="", encoding="utf-8"
        ) as output:
            writer = csv.DictWriter(output, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    return result


def run_batch(
    sandbox,
    dataset=None,
    repos=None,
    task_ids=None,
    batch_id=None,
    resume=False,
    retry_errors=False,
    progress=None,
    test_mode="issue",
    validate=False,
    stage="full",
    preflight=False,
    reuse_validated=False,
    auto_prune=True,
):
    if retry_errors and not resume:
        raise ValueError("--retry-errors requires --resume")
    if resume and not batch_id:
        raise ValueError("--resume requires --batch-id")
    batch_id = batch_id or uuid.uuid4().hex
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", batch_id):
        raise ValueError("Invalid batch ID")
    path = resolve_path(sandbox.root, dataset or sandbox.config["dataset"])
    tasks = load_tasks(path)
    if set(repos or []) - {t.repo for t in tasks.values()} or set(task_ids or []) - set(
        tasks
    ):
        raise ValueError("Unknown repo or task selection")
    selection = [
        tid
        for tid in (dict.fromkeys(task_ids) if task_ids else tasks)
        if not repos or tasks[tid].repo in repos
    ]
    if resume:
        previous_path = (
            resolve_path(sandbox.root, sandbox.config["results"])
            / "batches"
            / batch_id
            / "manifest.json"
        )
        previous = json.loads(previous_path.read_text(encoding="utf-8"))
        original = list(previous["tasks"])
        if (repos or task_ids) and set(selection) != set(original):
            raise ValueError("Batch fingerprint selection changed; create a new batch")
        if set(original) - set(tasks):
            raise ValueError("Batch dataset selection changed; create a new batch")
        selection = original
    if not selection:
        raise ValueError("Empty task selection")
    validation = check_profiles(sandbox, path)
    if validation["errors"]:
        raise ValueError(
            "Profile validation failed: " + "; ".join(validation["errors"])
        )
    budget = {
        "build_timeout": 1800,
        "test_timeout": 1800,
        "test_mode": test_mode,
        "validate": validate,
        "stage": stage,
        "preflight": preflight,
        "reuse_validated": reuse_validated,
        "auto_prune": auto_prune,
    }
    signature = fingerprint(sandbox, path, selection, budget)
    directory = (
        resolve_path(sandbox.root, sandbox.config["results"]) / "batches" / batch_id
    )
    directory.mkdir(parents=True, exist_ok=True)
    checkpoint = directory / "manifest.json"
    with cache_lock(directory / "batch.lock", timeout=0):
        if resume:
            manifest = json.loads(checkpoint.read_text(encoding="utf-8"))
            if manifest["fingerprint"] != signature:
                raise ValueError("Batch fingerprint changed; create a new batch")
        else:
            if checkpoint.exists():
                raise ValueError("Batch exists; use --resume")
            manifest = {
                "schema_version": 1,
                "batch_id": batch_id,
                "fingerprint": signature,
                "budget": budget,
                "test_mode": test_mode,
                "operation": "preflight"
                if preflight
                else "validate"
                if validate
                else "run",
                "stage": stage,
                "dataset": str(path),
                "status": "running",
                "tasks": {
                    tid: {
                        "repo": tasks[tid].repo,
                        "base_commit": tasks[tid].base_commit,
                        "attempts": [],
                    }
                    for tid in selection
                },
            }
        active = manifest.pop("active_run", None)
        if active:
            record_path = (
                resolve_path(sandbox.root, sandbox.config["results"])
                / active["run_id"]
                / "record.json"
            )
            if record_path.exists():
                recovered = json.loads(record_path.read_text(encoding="utf-8"))
                if not any(
                    a["run_id"] == recovered["run_id"]
                    for a in manifest["tasks"][active["task_id"]]["attempts"]
                ):
                    manifest["tasks"][active["task_id"]]["attempts"].append(recovered)
        manifest["status"] = "running"
        if resume:
            (directory / "STOP").unlink(missing_ok=True)
        atomic_json(checkpoint, manifest)
        report(directory, manifest)
        try:
            if progress:
                progress(
                    {
                        "event": "batch_started",
                        "batch_id": batch_id,
                        "total": len(selection),
                        "directory": str(directory),
                    }
                )
            for task_id in selection:
                if (directory / "STOP").exists():
                    raise KeyboardInterrupt
                if fingerprint(sandbox, path, selection, budget) != signature:
                    raise ValueError(
                        "Configuration changed during batch; create a new batch"
                    )
                task = manifest["tasks"][task_id]
                attempts = task["attempts"]
                if (
                    attempts
                    and attempts[-1]["status"] != "interrupted"
                    and not (retry_errors and attempts[-1]["status"] in RETRYABLE)
                ):
                    continue
                run_id = uuid.uuid4().hex
                manifest["active_run"] = {"task_id": task_id, "run_id": run_id}
                atomic_json(checkpoint, manifest)
                report(directory, manifest)
                # Independent client/policy per task. No mutable run state is shared.
                runner = DockerSandbox(
                    root=sandbox.root,
                    config=sandbox.config_path,
                    policy=SandboxPolicy(timeout=1800),
                )
                runner.stop_file = directory / "STOP"
                runner.auto_prune = auto_prune
                result = runner.run(
                    task_id,
                    dataset=path,
                    build_timeout=1800,
                    test_timeout=1800,
                    run_id=run_id,
                    test_mode=test_mode,
                    validate=validate,
                    stage=stage,
                    preflight=preflight,
                    reuse_validated=reuse_validated,
                    progress=progress,
                )
                attempts.append(result)
                manifest.pop("active_run", None)
                atomic_json(checkpoint, manifest)
                summary = report(directory, manifest)
                if progress:
                    progress(
                        {
                            "task": task_id,
                            "status": result["status"],
                            "finished": sum(
                                bool(t["attempts"]) for t in manifest["tasks"].values()
                            ),
                            "total": len(selection),
                            "executed": summary["executed"],
                        }
                    )
            manifest["status"] = "processed"
        except BaseException:
            manifest["status"] = "interrupted"
            raise
        finally:
            atomic_json(checkpoint, manifest)
            report(directory, manifest)
        return report(directory, manifest)
