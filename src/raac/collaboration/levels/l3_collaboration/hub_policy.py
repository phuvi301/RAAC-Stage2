"""Routing rules of the star Hub. Pure functions over ChainState; no model call lives here.

``hub_options`` is the safety net for the LLM Hub: it computes which roles may run next from
what the state already contains, and which one the rules would pick. A model may only choose
among the options, so it cannot skip a prerequisite or dispatch a role whose input is missing.
"""

from typing import Any

SPOKES = ("analyst", "planner", "coder", "tester", "reviewer")


ORDER = ("analyst", "planner", "coder", "tester", "reviewer", "finish")


def hub_options(state: dict[str, Any], tests_passed: bool) -> tuple[list[str], str]:
    """(allowed next nodes, rule-based default); ``finish`` ends the run.

    The Hub may pick any role whose inputs exist, in any order and as often as the budget allows:
    - analyst needs only the requirement, so it can be consulted again at any point;
    - planner needs a spec, coder a plan, tester a patch, reviewer a test report for the current patch;
    - coder is withdrawn once its rounds are used up; nothing can change the code after that, so only a
      final review (if none yet) or ``finish`` remains;
    - ``finish`` needs a review of the current patch (or no coder rounds left); an accepted run (PASS) can
      only finish.
    Totals stay bounded by max_rounds, max_review_rounds and max_dispatches.
    """
    limits = state["limits"]
    coder_left = state.get("round_number", 0) < limits["max_rounds"]
    review_left = state.get("review_rounds", 0) < limits.get("max_review_rounds", 0)
    spec, plan, patch = state.get("spec"), state.get("plan"), state.get("patch")
    report, review = state.get("test_report"), state.get("review_report")
    if spec is None:
        return ["analyst"], "analyst"
    available = {"analyst", "planner"}
    if plan is not None and coder_left:
        available.add("coder")
    if patch is not None:
        available.add("tester")
    if report is not None:
        available.add("reviewer")
    if plan is None or state.get("spec_changed"):
        default = "planner"  # a new spec makes the old plan stale
    elif patch is None:
        default = "coder"
    elif report is None:
        # After a re-plan the patch is rebuilt by default; re-testing it is a choice the Hub may make.
        default = "coder" if state.get("plan_changed") and "coder" in available else "tester"
    elif not tests_passed:
        if not coder_left:
            return (["reviewer"], "reviewer") if review is None else (["finish"], "finish")
        default = "coder"
        if review is not None:
            available.add("finish")
    elif review is None:
        default = "reviewer"
    elif state.get("verdict") == "PASS":
        return ["finish"], "finish"
    elif coder_left and review_left:
        uncovered = set(state["ac_catalog"]) - set(review.get("covered_ac_ids", []))
        default = "planner" if uncovered else "coder"
        available.add("finish")
    else:
        return ["finish"], "finish"
    return [name for name in ORDER if name in available], default


def hub_summary(state: dict[str, Any], tests_passed: bool) -> dict[str, Any]:
    """Compact account of the run for the Hub; the Hub never receives raw artifacts."""
    limits = state["limits"]
    report, review = state.get("test_report"), state.get("review_report")
    return {
        "rounds": {"completed": state["rounds_completed"], "max": limits["max_rounds"]},
        "review_rounds": {"used": state.get("review_rounds", 0), "max": limits.get("max_review_rounds", 0)},
        "dispatches": {"used": state.get("hub_dispatches", 0), "max": limits.get("max_dispatches", 0)},
        "plan_changed_since_patch": bool(state.get("plan_changed")),
        "spec_changed_since_plan": bool(state.get("spec_changed")),
        "tests": None if report is None else {
            "passed": tests_passed, "status": report.get("status"), "tests_run": report.get("tests_run"),
            "failed": report.get("failed"), "blocking_issues": list(report.get("blocking_issues", []))[:5],
        },
        "review": None if review is None else {
            "verdict": review.get("verdict"), "blocking_issues": list(review.get("blocking_issues", []))[:5],
            "uncovered_ac_ids": sorted(set(state["ac_catalog"]) - set(review.get("covered_ac_ids", []))),
        },
    }
