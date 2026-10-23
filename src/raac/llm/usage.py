"""Aggregate API-observed tokens; absent usage is unknown, not zero."""

from collections import defaultdict


def cached_tokens(usage: dict | None) -> int | None:
    """Observed cached input, never guessed; prefer Chat Completions details."""
    if not isinstance(usage, dict):
        return None
    details = usage.get("prompt_tokens_details")
    if isinstance(details, dict) and "cached_tokens" in details:
        value = details["cached_tokens"]
    else:
        value = usage.get("cached_tokens")  # Some routers normalize this field.
    prompt = usage.get("prompt_tokens")
    if type(value) is int and type(prompt) is int and 0 <= value <= prompt:
        return value
    return None


def logged_usage(usage: dict | None) -> dict | None:
    """Whitelist public usage counters; do not log arbitrary provider fields."""
    if not isinstance(usage, dict):
        return None
    return {"prompt_tokens": usage.get("prompt_tokens"),
            "completion_tokens": usage.get("completion_tokens"),
            "cached_tokens": cached_tokens(usage)}


class UsageTracker:
    def export_run(self, run_id: str) -> list[dict]:
        return [{"agent_id": agent, "level": level, **values}
                for (run, agent, level), values in self._totals.items() if run == run_id]

    def restore_run(self, run_id: str, entries: list[dict]) -> None:
        restored = {}
        counts = {"prompt_tokens", "completion_tokens", "calls", "cached_tokens", "cache_reports"}
        for entry in entries:
            if (set(entry) != counts | {"agent_id", "level", "usage_complete"}
                    or any(type(entry[key]) is not int or entry[key] < 0 for key in counts)
                    or type(entry["usage_complete"]) is not bool
                    or not isinstance(entry["agent_id"], str) or not isinstance(entry["level"], str)
                    or entry["cached_tokens"] > entry["prompt_tokens"] or entry["cache_reports"] > entry["calls"]):
                raise ValueError("Invalid persisted usage")
            key = (run_id, entry["agent_id"], entry["level"])
            if key in restored:
                raise ValueError("Duplicate persisted usage")
            restored[key] = {name: entry[name] for name in counts | {"usage_complete"}}
        for key in list(self._totals):
            if key[0] == run_id:
                del self._totals[key]
        self._totals.update(restored)
    def __init__(self):
        self._totals = defaultdict(lambda: {"prompt_tokens": 0, "completion_tokens": 0,
                                           "calls": 0, "usage_complete": True,
                                           "cached_tokens": 0, "cache_reports": 0})

    def record_attempt(self, context):
        key = (context["run_id"], context["agent_id"], context["level"])
        self._totals[key]["calls"] += 1

    def add(self, context, usage: dict | None, *, count_call=True):
        key = (context["run_id"], context["agent_id"], context["level"])
        total = self._totals[key]
        if count_call:
            total["calls"] += 1
        if not isinstance(usage, dict) or not usage or any(
            type(usage.get(name)) is not int or usage[name] < 0
            for name in ("prompt_tokens", "completion_tokens")
        ):
            total["usage_complete"] = False
            return
        for name in ("prompt_tokens", "completion_tokens"):
            total[name] += usage[name]
        cached = cached_tokens(usage)
        if cached is not None:
            total["cached_tokens"] += cached
            total["cache_reports"] += 1

    def snapshot(self, *, run_id: str, agent_id=None, level=None) -> dict:
        result = {"prompt_tokens": 0, "completion_tokens": 0,
                  "calls": 0, "usage_complete": True}
        cached, cache_reports = 0, 0
        for (run, agent, group), total in self._totals.items():
            if run != run_id or (agent_id is not None and agent != agent_id):
                continue
            if level is not None and group != level:
                continue
            for key in ("prompt_tokens", "completion_tokens", "calls"):
                result[key] += total[key]
            result["usage_complete"] &= total["usage_complete"]
            cached += total["cached_tokens"]
            cache_reports += total["cache_reports"]
        result["total_tokens"] = result["prompt_tokens"] + result["completion_tokens"]
        result["cache_usage_complete"] = result["usage_complete"] and cache_reports == result["calls"]
        result["cached_tokens"] = cached if result["cache_usage_complete"] else None
        return result
