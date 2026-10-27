from raac.benchmark.analysis.pilot_report import summarize_records
from raac.benchmark.run_record import RunRecord


def record(run_id, task_id, *, attempt=1, tokens=10, seconds=1.0, status="completed", evaluation="resolved"):
    usage = {"prompt_tokens": tokens, "completion_tokens": 0, "total_tokens": tokens,
             "usage_complete": True, "calls": 1} if tokens is not None else {"usage_complete": False, "calls": 1}
    return RunRecord(run_id=run_id, task_id=task_id, level="L3", base_commit="a" * 40,
        attempt=attempt, phase="finished", status=status, evaluation_status=evaluation,
        usage=usage, timing={"total_seconds": seconds})


def test_report_counts_retry_resource_without_inventing_complete_usage():
    report = summarize_records([
        record("attempt-one", "task-one", tokens=None, status="error", evaluation="pending"),
        record("attempt-two", "task-one", attempt=2, tokens=12, seconds=2.0),
        record("attempt-three", "task-two", tokens=20, seconds=9.0, evaluation="unresolved"),
    ])["levels"]["L3"]
    assert report["tasks"] == 2 and report["runs"] == 2 and report["attempts"] == 3
    assert report["resolved_runs"] == 1 and report["error_attempts"] == 1
    assert report["retryable_infrastructure_attempts"] == 0
    assert report["observed_total_tokens"] == 32 and report["incomplete_token_runs"] == 1
    assert report["total_seconds"]["mean"] == 6.0
    assert report["total_tokens"]["mean"] == 20.0 and report["total_tokens"]["samples"] == 1


def test_single_sample_has_zero_standard_deviation():
    metrics = summarize_records([record("one", "task-one")])["levels"]["L3"]["total_tokens"]
    assert metrics["stddev"] == 0.0 and metrics["mean"] == 10.0


def test_repetitions_are_not_counted_as_distinct_tasks():
    first = record("one", "task-one")
    second = record("two", "task-one")
    second.repetition = 2
    level = summarize_records([first, second])["levels"]["L3"]
    assert level["tasks"] == 1 and level["runs"] == 2
