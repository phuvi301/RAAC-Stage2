"""Canonical role prompts and fixture outputs share the current wire contract; no LLM is called."""

import json
from pathlib import Path

import pytest

from raac.registry import (
    REQUIRED_CORE_ROLES,
    OutputFormat,
    OutputFormatError,
    load_registry,
    parse_role_output,
    render_prompt,
)

ROOT = Path(__file__).resolve().parents[3]
TASK = json.loads((ROOT / "tests" / "fixtures" / "sample_task.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def registry():
    return load_registry(ROOT / "registry")


def user_message(task):
    return f"Repository: {task['repo']}\n\nIssue:\n{task['issue']}"


OUTPUTS = {
    "analyst": json.dumps({"summary": "Fix task", "requirements": [{"id": "R1", "description": "Fix behavior"}],
                          "acceptance_criteria": ["Behavior is correct"], "constraints": [], "open_questions": []}),
    "planner": json.dumps({"steps": [{"id": 1, "description": "Fix behavior", "files": ["app.py"], "depends_on": []}]}),
    "coder": "--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n-old\n+new\n",
    "tester": json.dumps({"tests_to_run": ["test_behavior"], "results": [
        {"test": "test_behavior", "status": "passed", "details": ""}], "verdict": "pass"}),
    "reviewer": json.dumps({"decision": "approve", "summary": "Reviewed", "comments": []}),
}


@pytest.mark.parametrize("role_id", REQUIRED_CORE_ROLES)
def test_prompt_and_fixture_output_share_contract(registry, role_id):
    role = registry.get_role(role_id)
    parsed = parse_role_output(role, OUTPUTS[role_id])
    loaded = registry.get(role_id)
    assert loaded.spec is role
    assert loaded.system_prompt == render_prompt(registry, role_id)
    assert parsed
    if role.output_format is OutputFormat.UNIFIED_DIFF:
        assert isinstance(parsed, str) and parsed.startswith("--- ")
    else:
        assert hasattr(parsed, "model_dump")


@pytest.mark.parametrize("role_id", REQUIRED_CORE_ROLES)
def test_malformed_answer_is_rejected_for_every_role(registry, role_id):
    role = registry.get_role(role_id)
    with pytest.raises(OutputFormatError):
        parse_role_output(role, "I fixed it, here is my explanation.")


@pytest.mark.parametrize("role_id", REQUIRED_CORE_ROLES)
def test_prompt_is_english_and_states_the_output_contract(registry, role_id):
    prompt = render_prompt(registry, role_id)
    assert prompt.startswith(f"You are the **{registry.get_role(role_id).role}**")
    assert "## Output format" in prompt
    assert prompt.isascii()  # English only; no stray Vietnamese
    if registry.get_role(role_id).output_format is OutputFormat.UNIFIED_DIFF:
        assert "unified diff" in prompt and "@@" in prompt
    else:
        assert "exactly one JSON object" in prompt


# keys the prompt documents must be exactly the keys the validator accepts
JSON_KEYS = {
    "analyst": ["summary", "requirements", "constraints", "acceptance_criteria", "open_questions"],
    "planner": ["steps", "depends_on", "files"],
    "tester": ["tests_to_run", "results", "verdict", "details"],
    "reviewer": ["decision", "summary", "comments", "severity", "message"],
}


@pytest.mark.parametrize("role_id", JSON_KEYS)
def test_prompt_documents_every_output_key(registry, role_id):
    prompt = render_prompt(registry, role_id)
    for key in JSON_KEYS[role_id]:
        assert f'"{key}"' in prompt, f"{role_id} prompt does not mention '{key}'"


def test_permissions_match_current_mas_roles(registry):
    can_run = sorted(role.id for role in registry.roles.values() if "run_tests" in role.tools)
    assert can_run == ["coder", "tester"]
    for role_id in ("analyst", "planner", "reviewer"):
        role = registry.get_role(role_id)
        assert all(registry.tools[name].read_only for name in role.tools)
    assert "apply_patch" not in registry.get_role("tester").tools
