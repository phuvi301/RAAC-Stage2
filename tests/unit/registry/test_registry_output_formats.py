import json

import pytest

from raac.registry import OutputFormatError, parse_role_output, validate_unified_diff

GOOD_DIFF = (
    "--- a/app/text.py\n"
    "+++ b/app/text.py\n"
    "@@ -10,3 +10,3 @@\n"
    " def slugify(value):\n"
    "-    return value\n"
    "+    return value.strip()\n"
    " \n"
)


# ------------------------------------------------------------------ diff
def test_valid_diff_passes_and_is_normalised():
    assert validate_unified_diff(GOOD_DIFF) == GOOD_DIFF


def test_diff_wrapped_in_a_fence_is_tolerated():
    assert validate_unified_diff("```diff\n" + GOOD_DIFF + "```") == GOOD_DIFF


def test_multi_file_diff_with_git_headers():
    diff = (
        "diff --git a/a.py b/a.py\nindex 111..222 100644\n--- a/a.py\n+++ b/a.py\n"
        "@@ -1 +1 @@\n-x = 1\n+x = 2\n"
        "diff --git a/b.py b/b.py\nnew file mode 100644\n--- /dev/null\n+++ b/b.py\n"
        "@@ -0,0 +1,2 @@\n+a = 1\n+b = 2\n"
    )
    assert validate_unified_diff(diff).endswith("+b = 2\n")


def test_no_newline_marker_is_allowed():
    diff = "--- a/f\n+++ b/f\n@@ -1 +1 @@\n-a\n\\ No newline at end of file\n+b\n\\ No newline at end of file\n"
    validate_unified_diff(diff)


@pytest.mark.parametrize(
    "bad, message",
    [
        ("", "empty diff"),
        ("Here is the fix:\n" + GOOD_DIFF, "must start with"),
        (GOOD_DIFF + "\nThat should do it.\n", "unexpected text outside a hunk"),
        ("--- a/f\n+++ b/f\n", "no hunk"),
        ("--- a/f\n@@ -1 +1 @@\n-a\n+b\n", "must be followed by"),
        ("--- a/f\n+++ b/f\n@@ nonsense @@\n-a\n+b\n", "malformed hunk header"),
        ("--- a/f\n+++ b/f\n@@ -1,3 +1,3 @@\n-a\n+b\n", "truncated"),
        ("--- a/f\n+++ b/f\n@@ -1,1 +1,1 @@\n-a\n-b\n+c\n", "more '-' lines"),
        ("--- a/f\n+++ b/f\n@@ -1,1 +1,1 @@\n-a\n+b\n+c\n", "unexpected text outside a hunk"),
        ("--- a/f\n+++ b/f\n@@ -1,1 +1,1 @@\n*a\n", "unexpected line inside hunk"),
    ],
)
def test_invalid_diffs_are_rejected(bad, message):
    with pytest.raises(OutputFormatError, match=message):
        validate_unified_diff(bad)


# ------------------------------------------------------------------ JSON roles
def dump(obj):
    return json.dumps(obj)


ANALYST = {
    "summary": "s",
    "requirements": [{"id": "R1", "description": "d"}],
    "acceptance_criteria": ["c"],
}
PLANNER = {"steps": [{"id": 1, "description": "a"}, {"id": 2, "description": "b", "depends_on": [1]}]}
TESTER = {
    "tests_to_run": ["t"],
    "results": [{"test": "t", "status": "passed"}],
    "verdict": "pass",
}
REVIEWER = {"decision": "reject", "summary": "s", "comments": [{"severity": "major", "message": "m"}]}


@pytest.mark.parametrize(
    "role, payload",
    [("analyst", ANALYST), ("planner", PLANNER), ("tester", TESTER), ("reviewer", REVIEWER)],
)
def test_valid_json_outputs(role, payload):
    parsed = parse_role_output(role, dump(payload))
    assert parsed.model_dump(exclude_none=False) is not None
    # a single wrapping fence is tolerated
    parse_role_output(role, "```json\n" + dump(payload) + "\n```")


def test_coder_goes_through_diff_validator():
    assert parse_role_output("coder", GOOD_DIFF) == GOOD_DIFF
    with pytest.raises(OutputFormatError):
        parse_role_output("coder", dump({"patch": "x"}))


@pytest.mark.parametrize("role", ["analyst", "planner", "tester", "reviewer"])
def test_non_json_and_non_object_are_rejected(role):
    with pytest.raises(OutputFormatError, match="not valid JSON"):
        parse_role_output(role, "Sure! Here you go")
    with pytest.raises(OutputFormatError, match="must be a JSON object"):
        parse_role_output(role, "[1, 2]")


def test_unknown_role_has_no_contract():
    with pytest.raises(OutputFormatError, match="no output contract"):
        parse_role_output("security", "{}")


def test_analyst_requires_requirements_and_criteria():
    bad = {**ANALYST, "requirements": []}
    with pytest.raises(OutputFormatError, match="requirements"):
        parse_role_output("analyst", dump(bad))
    dup = {**ANALYST, "requirements": [{"id": "R1", "description": "a"}, {"id": "R1", "description": "b"}]}
    with pytest.raises(OutputFormatError, match="unique"):
        parse_role_output("analyst", dump(dup))


def test_unknown_keys_are_rejected():
    with pytest.raises(OutputFormatError, match="Extra inputs"):
        parse_role_output("analyst", dump({**ANALYST, "notes": "x"}))


def test_planner_ids_and_dependencies():
    with pytest.raises(OutputFormatError, match="consecutive"):
        parse_role_output("planner", dump({"steps": [{"id": 2, "description": "a"}]}))
    forward = {"steps": [{"id": 1, "description": "a", "depends_on": [2]}, {"id": 2, "description": "b"}]}
    with pytest.raises(OutputFormatError, match="earlier steps"):
        parse_role_output("planner", dump(forward))
    with pytest.raises(OutputFormatError):
        parse_role_output("planner", dump({"steps": []}))


def test_tester_verdict_must_match_results():
    lying = {**TESTER, "results": [{"test": "t", "status": "failed", "details": "boom"}]}
    with pytest.raises(OutputFormatError, match="verdict.*results"):
        parse_role_output("tester", dump(lying))
    inverse = {**TESTER, "verdict": "fail"}
    with pytest.raises(OutputFormatError, match="verdict.*results"):
        parse_role_output("tester", dump(inverse))
    bad_status = {**TESTER, "results": [{"test": "t", "status": "ok"}]}
    with pytest.raises(OutputFormatError):
        parse_role_output("tester", dump(bad_status))


def test_reviewer_decision_must_match_comments():
    with pytest.raises(OutputFormatError, match="at least one comment"):
        parse_role_output("reviewer", dump({"decision": "reject", "summary": "s", "comments": []}))
    blocker = {"decision": "approve", "summary": "s", "comments": [{"severity": "blocker", "message": "m"}]}
    with pytest.raises(OutputFormatError, match="blocker"):
        parse_role_output("reviewer", dump(blocker))
    assert parse_role_output("reviewer", dump({"decision": "approve", "summary": "s"})).comments == []
    with pytest.raises(OutputFormatError):
        parse_role_output(
            "reviewer",
            dump({"decision": "reject", "summary": "s", "comments": [{"severity": "huge", "message": "m"}]}),
        )
    with pytest.raises(OutputFormatError):
        parse_role_output("reviewer", dump({"decision": "maybe", "summary": "s"}))
