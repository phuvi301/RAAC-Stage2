"""The Coder's handoff report: per plan step, with the reason for anything left undone."""

from raac.common.coder_report import looks_like_diff, parse_coder_report, render_coder_report

REPORT = """STEP 1 | done | added cast_to_ndarray to _validate_data
STEP 2 | blocked |
ISSUE test_set_output fails on None | unfixed | needs an API change outside the plan
ISSUE old warning | fixed | removed the stale import
NOTES: ran sklearn/feature_selection tests, 298 passed"""


def test_every_plan_step_gets_an_entry_and_missing_reasons_are_flagged():
    report = parse_coder_report(REPORT, [1, 2, 3])
    assert [(s["step"], s["status"]) for s in report["steps"]] == [(1, "done"), (2, "blocked"), (3, "not_reported")]
    assert [s["reason_missing"] for s in report["steps"]] == [False, True, False]
    assert [(i["issue"], i["status"], i["reason_missing"]) for i in report["issues"]] == [
        ("test_set_output fails on None", "unfixed", False), ("old warning", "fixed", False)]
    assert report["notes"].startswith("ran sklearn") and report["structured"]


def test_rendered_summary_names_each_step_the_reason_and_the_checks():
    text = render_coder_report(parse_coder_report(REPORT, [1, 2, 3]))
    assert "Step 1 done: added cast_to_ndarray" in text
    assert "Step 2 blocked (no reason given)" in text
    assert "Step 3 not reported" in text
    assert "Issue 'test_set_output fails on None' unfixed: needs an API change outside the plan" in text
    assert text.endswith("Notes: ran sklearn/feature_selection tests, 298 passed")


def test_steps_that_are_not_in_the_plan_are_ignored():
    report = parse_coder_report("STEP 9 | done | invented\nSTEP 1 | done | real", [1])
    assert [(s["step"], s["note"]) for s in report["steps"]] == [(1, "real")]


def test_plain_prose_becomes_notes_and_is_not_structured():
    report = parse_coder_report("The earlier edit already fixes it.\nNothing else to do.", [1, 2])
    assert not report["structured"] and report["notes"] == "The earlier edit already fixes it. Nothing else to do."
    assert render_coder_report(report).startswith("Step 1 not reported")


def test_lengths_are_bounded():
    report = parse_coder_report("STEP 1 | done | " + "x" * 5000 + "\nNOTES: " + "y" * 5000, [1])
    assert len(report["steps"][0]["note"]) == 300 and len(report["notes"]) <= 1200
    assert len(render_coder_report(report)) <= 2000


def test_a_patch_is_not_mistaken_for_a_report():
    assert looks_like_diff("diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-a\n+b\n")
    assert looks_like_diff("--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-a\n+b\n")
    assert not looks_like_diff("STEP 1 | done | changed x\nNOTES: all good")
    assert not looks_like_diff("")
