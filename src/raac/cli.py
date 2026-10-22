"""Command-line entry point for standalone sandbox runs."""

import argparse
import asyncio
import json
import sys

from raac.harness.layer3_execution.sandbox.batch import check_profiles, run_batch
from raac.harness.layer3_execution.sandbox.docker_sandbox import DockerSandbox


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(prog="raac")
    sub = parser.add_subparsers(dest="group", required=True)
    benchmark = sub.add_parser("benchmark")
    benchmark_commands = benchmark.add_subparsers(dest="command", required=True)
    one = benchmark_commands.add_parser("run")
    one.add_argument("--task-id", required=True)
    one.add_argument("--level", choices=["L3"], default="L3")
    one.add_argument("--repetition", type=int, default=1)
    one.add_argument("--config")
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
            command.add_argument(
                "--test-mode", choices=["suite", "issue"], default="issue"
            )
    for name in ("batch", "validate", "preflight"):
        batch = commands.add_parser(name)
        batch.add_argument("--config")
        batch.add_argument("--dataset")
        batch.add_argument("--repo", action="append")
        batch.add_argument("--task-id", action="append")
        batch.add_argument("--batch-id")
        batch.add_argument("--resume", action="store_true")
        batch.add_argument("--retry-errors", action="store_true")
        batch.add_argument("--test-mode", choices=["suite", "issue"], default="issue")
        if name == "validate":
            batch.add_argument("--stage", choices=["pilot", "full"], default="full")
            batch.add_argument("--reuse-validated", action="store_true")
    cache = commands.add_parser("cache")
    cache.add_argument("action", choices=["status", "prune"])
    cache.add_argument("--config")
    cache.add_argument("--max-gib", type=float, default=30)
    cache.add_argument("--apply", action="store_true")
    stop = commands.add_parser("stop")
    stop.add_argument("--batch-id", required=True)
    stop.add_argument("--config")
    importer = commands.add_parser("import-verified")
    importer.add_argument("--raw", required=True)
    importer.add_argument("--dataset", default="data/tasks/cleaned_benchmark.json")
    importer.add_argument("--output", default="data/evaluation/swe_bench_verified.json")
    profiles = commands.add_parser("profiles")
    profiles.add_argument("action", choices=["check"])
    profiles.add_argument("--config")
    profiles.add_argument("--dataset")
    args = parser.parse_args(argv)
    if args.group == "benchmark":
        from raac.benchmark.runner import execute

        try:
            return asyncio.run(execute(args))
        except KeyboardInterrupt:
            return 130
        except (ValueError, OSError):
            print("Invalid benchmark input or configuration; no local execution fallback is supported.", file=sys.stderr)
            return 2
    try:
        if args.command == "import-verified":
            from raac.harness.layer3_execution.sandbox.evaluation import import_verified

            print(
                json.dumps(
                    import_verified(args.raw, args.dataset, args.output), indent=2
                )
            )
            return 0
        runner = DockerSandbox(config=args.config)
        if args.command == "cache":
            from raac.harness.layer3_execution.sandbox.cache import inventory, prune

            result = (
                inventory(runner)
                if args.action == "status"
                else prune(runner, args.max_gib, args.apply)
            )
        elif args.command == "stop":
            import re

            from raac.harness.layer3_execution.sandbox.docker_sandbox import (
                resolve_path,
            )

            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", args.batch_id):
                raise ValueError("Invalid batch ID")
            directory = (
                resolve_path(runner.root, runner.config["results"])
                / "batches"
                / args.batch_id
            )
            if not (directory / "manifest.json").exists():
                raise ValueError("Unknown batch")
            (directory / "STOP").write_text("User requested stop", encoding="utf-8")
            result = {"status": "ready", "stop_requested": args.batch_id}
        elif args.command in {"batch", "validate", "preflight"}:
            result = run_batch(
                runner,
                dataset=args.dataset,
                repos=args.repo,
                task_ids=args.task_id,
                batch_id=args.batch_id,
                resume=args.resume,
                retry_errors=args.retry_errors,
                test_mode=args.test_mode,
                validate=args.command == "validate",
                stage=getattr(args, "stage", "full"),
                reuse_validated=getattr(args, "reuse_validated", False),
                preflight=args.command == "preflight",
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
                test_mode=getattr(args, "test_mode", "issue"),
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
        if result["status"]
        in {"ready", "prepared", "passed", "valid", "validated", "resolved"}
        or (
            result["status"] == "processed"
            and (
                result.get("validated") == result["total"]
                if args.command == "validate" and args.stage == "full"
                else result.get("pilot_passed") == result["total"]
                if args.command == "validate"
                else result.get("preflight_passed") == result["total"]
                if args.command == "preflight"
                else result["executed"] == result["total"]
            )
        )
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
