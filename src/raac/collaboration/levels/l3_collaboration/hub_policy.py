"""Routing rules of the star Hub. Pure functions over ChainState; no model call lives here.

``hub_options`` is the safety net for the LLM Hub: it computes which roles may run next from
what the state already contains, and which one the rules would pick. A model may only choose
among the options, so it cannot skip a prerequisite or dispatch a role whose input is missing.
"""

from typing import Any

SPOKES = ("analyst", "planner", "coder", "tester", "reviewer")


def hub_options(state: dict[str, Any], tests_passed: bool) -> tuple[list[str], str]:
    """(allowed next nodes, rule-based default); ``finish`` ends the run."""
    limits = state["limits"]
    rounds_left = state["rounds_completed"] < limits["max_rounds"]
    review_left = state.get("review_rounds", 0) < limits.get("max_review_rounds", 0)
    if state.get("spec") is None:
        return ["analyst"], "analyst"
    if state.get("plan") is None:
        return ["planner"], "planner"
    if state.get("patch") is None or state.get("plan_changed"):
        return ["coder"], "coder"
    if state.get("test_report") is None:
        return ["tester"], "tester"
    review = state.get("review_report")
    if not tests_passed:
        if review is None:
            # No rounds left still gets a final review, as the Chain does; the run then ends rejected.
            return (["coder", "planner"], "coder") if rounds_left else (["reviewer"], "reviewer")
        return ["finish"], "finish"
    if review is None:
        return ["reviewer"], "reviewer"
    if state.get("verdict") == "PASS":
        return ["finish"], "finish"
    if rounds_left and review_left:
        uncovered = set(state["ac_catalog"]) - set(review.get("covered_ac_ids", []))
        return ["coder", "planner", "finish"], "planner" if uncovered else "coder"
    return ["finish"], "finish"


def hub_summary(state: dict[str, Any], tests_passed: bool) -> dict[str, Any]:
    """Compact account of the run for the Hub; the Hub never receives raw artifacts."""
    limits = state["limits"]
    report, review = state.get("test_report"), state.get("review_report")
    return {
        "rounds": {"completed": state["rounds_completed"], "max": limits["max_rounds"]},
        "review_rounds": {"used": state.get("review_rounds", 0), "max": limits.get("max_review_rounds", 0)},
        "dispatches": {"used": state.get("hub_dispatches", 0), "max": limits.get("max_dispatches", 0)},
        "plan_changed_since_patch": bool(state.get("plan_changed")),
        "tests": None if report is None else {
            "passed": tests_passed, "status": report.get("status"), "tests_run": report.get("tests_run"),
            "failed": report.get("failed"), "blocking_issues": list(report.get("blocking_issues", []))[:5],
        },
        "review": None if review is None else {
            "verdict": review.get("verdict"), "blocking_issues": list(review.get("blocking_issues", []))[:5],
            "uncovered_ac_ids": sorted(set(state["ac_catalog"]) - set(review.get("covered_ac_ids", []))),
        },
    }
