"""Command-line entry point for standalone sandbox runs."""

import argparse
import json
import sys

from raac.harness.layer3_execution.sandbox.batch import check_profiles, run_batch
from raac.harness.layer3_execution.sandbox.docker_sandbox import DockerSandbox


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(prog="raac")
    sub = parser.add_subparsers(dest="group", required=True)
    sandbox = sub.add_parser("sandbox")
    commands = sandbox.add_subparsers(dest="command", required=True)
    for name in ("check", "prepare", "run"):
        command = commands.add_parser(name)
        command.add_argument("--config")
        if name != "check":
            command.add_argument("--task-id", required=True)
            command.add_argument("--dataset")
        if name == "run":
            command.add_argument("--patch")
    batch = commands.add_parser("batch")
    batch.add_argument("--config")
    batch.add_argument("--dataset")
    batch.add_argument("--repo", action="append")
    batch.add_argument("--task-id", action="append")
    batch.add_argument("--batch-id")
    batch.add_argument("--resume", action="store_true")
    batch.add_argument("--retry-errors", action="store_true")
    profiles = commands.add_parser("profiles")
    profiles.add_argument("action", choices=["check"])
    profiles.add_argument("--config")
    profiles.add_argument("--dataset")
    args = parser.parse_args(argv)
    try:
        runner = DockerSandbox(config=args.config)
        if args.command == "batch":
            result = run_batch(
                runner,
                dataset=args.dataset,
                repos=args.repo,
                task_ids=args.task_id,
                batch_id=args.batch_id,
                resume=args.resume,
                retry_errors=args.retry_errors,
                progress=lambda event: print(
                    json.dumps(event), file=sys.stderr, flush=True
                ),
            )
        elif args.command == "profiles":
            result = check_profiles(runner, args.dataset)
        elif args.command == "check":
            result = runner.check()
        else:
            result = runner.run(
                args.task_id,
                dataset=args.dataset,
                patch_path=getattr(args, "patch", None),
                prepare=args.command == "prepare",
            )
    except KeyboardInterrupt:
        print(
            json.dumps(
                {
                    "status": "interrupted",
                    "hint": "Resume with the same batch ID and selection",
                }
            )
        )
        return 130
    except Exception as exc:  # noqa: BLE001 -- CLI reports configuration and backend failures
        result = {"status": "preparation_error", "error": str(exc)}
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return (
        0
        if result["status"] in {"ready", "prepared", "passed", "valid"}
        or (result["status"] == "processed" and result["executed"] == result["total"])
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
