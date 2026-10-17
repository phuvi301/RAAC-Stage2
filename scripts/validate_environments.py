"""Run the optimization acceptance queue in an ordinary terminal, independent of Codex.

Restart with the same --prefix to resume. A changed fingerprint is intentionally fatal.
Use `raac sandbox stop --batch-id <prefix>-<phase>` to stop the active phase;
KeyboardInterrupt stops the queue without starting its next phase.
"""

import argparse
import json
import re
import sys

from raac.benchmark.dataset import load_tasks
from raac.harness.layer3_execution.sandbox.batch import atomic_json, run_batch
from raac.harness.layer3_execution.sandbox.docker_sandbox import (
    DockerSandbox,
    resolve_path,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prefix", default="issue-optimized-v1")
    parser.add_argument("--config")
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", args.prefix):
        parser.error("Invalid prefix")
    runner = DockerSandbox(config=args.config)
    results = resolve_path(runner.root, runner.config["results"])
    tasks = load_tasks(resolve_path(runner.root, runner.config["dataset"]))
    representatives = {}
    for task in tasks.values():
        representatives.setdefault(task.repo, task.id)
    state = {"prefix": args.prefix, "status": "running", "phases": {}}
    state_path = results / (args.prefix + "-queue.json")
    results.mkdir(parents=True, exist_ok=True)
    try:
        for phase, options in (
            ("preflight", {"preflight": True}),
            (
                "pilot",
                {
                    "validate": True,
                    "stage": "pilot",
                    "task_ids": list(representatives.values()),
                },
            ),
            ("full", {"validate": True, "reuse_validated": True}),
        ):
            batch_id = args.prefix + "-" + phase
            state["active_batch"] = batch_id
            atomic_json(state_path, state)
            summary = run_batch(
                runner,
                batch_id=batch_id,
                resume=(results / "batches" / batch_id / "manifest.json").exists(),
                progress=lambda event: print(
                    json.dumps(event), file=sys.stderr, flush=True
                ),
                **options,
            )
            state["phases"][phase] = {
                k: summary[k] for k in ("total", "statuses", "validated")
            }
            atomic_json(state_path, state)
        state.pop("active_batch", None)
        state["status"] = "processed"
    except KeyboardInterrupt:
        state["status"] = "interrupted"
        return 130
    except Exception as exc:
        state["status"] = "error"
        state["error"] = str(exc)
        raise
    finally:
        atomic_json(state_path, state)
    print(json.dumps(state, indent=2))
    return 0 if state["phases"]["full"]["validated"] == len(tasks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
