"""Small report views for prompts; never modify execution/audit reports."""

from collections import Counter
from copy import deepcopy
import json


def encoded_size(value):
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")))


def clip_text(value, limit):
    if not isinstance(value, str) or len(value) <= limit:
        return value
    marker = "\n[Shortened; full value remains in the observed report.]\n"
    if limit <= len(marker):
        return value[:limit]
    available = limit - len(marker)
    return value[:available // 2] + marker + value[-(available - available // 2):]


def compact_test_report(report, maximum=8000):
    """Keep counters, verdict, blockers, AC and revision; sample bulky case data."""
    result = deepcopy(report)
    if not isinstance(result, dict) or encoded_size(result) <= maximum:
        return result
    cases = result.get("results", [])
    if not isinstance(cases, list):
        cases = []
    failures = [item for item in cases if isinstance(item, dict) and item.get("status") in {"failed", "error"}]
    # One example per observed failure signature.
    ordered, seen = [], set()
    for item in failures:
        signature = (item.get("status"), str(item.get("details", "")).split("\n", 1)[0])
        if signature not in seen:
            ordered.append(item)
            seen.add(signature)
    candidates = ordered or cases
    samples = [{**item, "test": clip_text(item.get("test", ""), 300),
                "details": clip_text(item.get("details", ""), 600)}
               for item in candidates[:8] if isinstance(item, dict)]
    result["results"] = samples
    context = dict(result.get("report_context") or {})
    context.setdefault("total_case_results", len(cases))
    context.setdefault("observed_failure_results", len(failures))
    context.setdefault("result_status_counts", dict(Counter(item.get("status", "unknown") for item in cases if isinstance(item, dict))))
    for field in ("tests_to_run", "test_cases"):
        if isinstance(result.get(field), list):
            context.setdefault(field + "_count", len(result[field]))
            result[field] = [clip_text(item, 300) for item in result[field][:5]]
    context["note"] = "Representative cases only; absence from samples does not mean passed. Full report is in the run audit. Use observation_ref with read_tool_result for detail."
    result["report_context"] = context
    if isinstance(result.get("summary"), str):
        result["summary"] = clip_text(result["summary"], min(1200, maximum // 5))
    # Reduce optional examples, not authoritative status/count/AC/blocker fields.
    while encoded_size(result) > maximum and len(result["results"]) > 1:
        result["results"].pop()
    for field in ("tests_to_run", "test_cases"):
        if encoded_size(result) > maximum and field in result:
            result[field] = []
    if encoded_size(result) > maximum:
        result["summary"] = clip_text(result.get("summary", ""), 200)
        for item in result["results"]:
            item["details"] = clip_text(item.get("details", ""), 200)
    return result


def compact_environment_report(report, maximum=8000):
    result = deepcopy(report)
    if not isinstance(result, dict):
        return result
    checks = result.get("verification")
    if isinstance(checks, list):
        per_stream = max(80, min(600, maximum // max(8, len(checks) * 4)))
        for check in checks:
            if not isinstance(check, dict):
                continue
            for field in ("stdout", "stderr"):
                if isinstance(check.get(field), str) and len(check[field]) > per_stream:
                    check.setdefault(field + "_chars", len(check[field]))
                    check[field] = clip_text(check[field], per_stream)
    smoke = result.get("smoke_report")
    if isinstance(smoke, dict):
        # Environment stores representative failures, not the entire suite.
        copied = {**smoke, "results": smoke.get("failure_samples", [])}
        copied.pop("failure_samples", None)
        copied = compact_test_report(copied, max(1000, maximum // 2))
        copied["failure_samples"] = copied.pop("results", [])
        result["smoke_report"] = copied
    if isinstance(result.get("summary"), str):
        result["summary"] = clip_text(result["summary"], 600)
    return result
