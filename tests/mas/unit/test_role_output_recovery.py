"""A role may narrate before its report; the report must still satisfy the schema."""

import json

import pytest

from raac.collaboration.levels.l3_collaboration.chain_state import OutputFormatError, parse_role_output

READY = {"status": "ready", "summary": "Runtime verified", "blocking_issues": []}


def test_plain_json_is_unchanged():
    assert parse_role_output("Environment", json.dumps(READY)).model_dump() == READY


def test_report_after_prose_and_markdown_is_accepted():
    text = ("I've set up the environment.\n\n**What's set up:**\n- Python 3.8 in `.venv`\n"
            "- A dict like {\"not\": \"a report\"} appears in the notes.\n\n" + json.dumps(READY))
    assert parse_role_output("Environment", text).model_dump() == READY


def test_fenced_json_after_prose_is_accepted():
    text = "Done.\n```json\n" + json.dumps(READY, indent=2) + "\n```\nThat is all."
    assert parse_role_output("Environment", text).model_dump() == READY


def test_last_object_that_satisfies_the_schema_wins():
    older = {**READY, "summary": "first attempt"}
    text = json.dumps(older) + "\nCorrection:\n" + json.dumps(READY)
    assert parse_role_output("Environment", text).summary == "Runtime verified"


def test_prose_without_a_report_is_still_an_error():
    with pytest.raises(OutputFormatError):
        parse_role_output("Environment", "Everything is ready, trust me.")


def test_object_that_breaks_the_schema_is_still_an_error():
    broken = {"status": "ready", "summary": "x", "blocking_issues": ["but a blocker"]}  # ready cannot have blockers
    with pytest.raises(OutputFormatError):
        parse_role_output("Environment", "Notes first.\n" + json.dumps(broken))


def test_other_roles_recover_too_and_extra_fields_stay_forbidden():
    review = {"decision": "approve", "summary": "ok", "comments": [], "covered_ac_ids": ["AC1"]}
    assert parse_role_output("Reviewer", "Looks fine.\n" + json.dumps(review)).decision == "approve"
    with pytest.raises(OutputFormatError):
        parse_role_output("Reviewer", "Looks fine.\n" + json.dumps({**review, "unexpected": 1}))


def test_coder_diff_path_is_not_affected():
    with pytest.raises(OutputFormatError):
        parse_role_output("Coder", "not a diff { \"a\": 1 }")
