"""Summarize candidate runs without dropping failed attempts or unknown usage."""

import argparse
import json
from collections import defaultdict
from pathlib import Path
from statistics import mean, stdev

from raac.benchmark.run_record import RunRecord


def _statistics(samples):
    return {"samples": len(samples), "mean": mean(samples) if samples else None,
            "stddev": stdev(samples) if len(samples) > 1 else 0.0 if samples else None}


def summarize_records(records: list[RunRecord]) -> dict:
    levels = defaultdict(lambda: defaultdict(list))
    seen = set()
    for record in records:
        if record.run_id in seen:
            raise ValueError("Duplicate run ID in report")
        seen.add(record.run_id)
        levels[record.level][(record.task_id, record.repetition)].append(record)
    report = {"schema_version": 1, "levels": {}}
    for level, runs in levels.items():
        attempts = [record for values in runs.values() for record in values]
        latest = [max(values, key=lambda record: record.attempt) for values in runs.values()]
        tokens, seconds = [], []
        for values in runs.values():
            if len({record.attempt for record in values}) != len(values):
                raise ValueError("Duplicate attempt number for one logical run")
            if all(record.usage.usage_complete for record in values):
                tokens.append(sum(record.usage.total_tokens for record in values))
            if all(record.phase == "finished" for record in values):
                seconds.append(sum(record.timing.total_seconds for record in values))
        report["levels"][level] = {
            "tasks": len({record.task_id for record in latest}), "runs": len(latest), "attempts": len(attempts),
            "resolved_runs": sum(record.status == "completed" and record.evaluation_status == "resolved" for record in latest),
            "error_attempts": sum(record.status in {"error", "timeout", "interrupted"} for record in attempts),
            "retryable_infrastructure_attempts": sum(any(error.get("retryable") == "true" for error in record.errors) for record in attempts),
            "observed_total_tokens": sum((record.usage.prompt_tokens or 0) + (record.usage.completion_tokens or 0) for record in attempts),
            "incomplete_token_runs": len(runs) - len(tokens), "incomplete_timing_runs": len(runs) - len(seconds),
            "total_tokens": _statistics(tokens), "total_seconds": _statistics(seconds),
            "phase_seconds": {name: sum(getattr(record.timing, name) for record in attempts) for name in
                              ("setup_seconds", "agent_seconds", "evaluation_seconds")},
        }
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("records", nargs="+", type=Path, help="Explicit record.json files; do not mix sandbox records")
    args = parser.parse_args(argv)
    records = [RunRecord.model_validate_json(path.read_text(encoding="utf-8")) for path in args.records]
    print(json.dumps(summarize_records(records), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
