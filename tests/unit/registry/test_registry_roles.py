"""Run each core role on its own against a sample task, using the mock LLM.

This checks the plumbing for the 'test each role in isolation' checklist item: the role is
loaded from the registry, its system prompt is rendered and sent to the LLM together with the
task, and the answer is validated against the role's output contract. Because the mock returns
canned text, it does NOT measure how well a real model follows the prompt; that needs a real
pilot run.
"""

import json
from pathlib import Path

import pytest

from raac.llm.mock_llm import MockLLM
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


@pytest.mark.parametrize("role_id", REQUIRED_CORE_ROLES)
def test_role_runs_alone_and_output_is_well_formed(registry, role_id):
    role = registry.get_role(role_id)
    llm = MockLLM()
    system = render_prompt(registry, role_id)

    response = llm.complete(system=system, user=user_message(TASK), role=role_id)
    parsed = parse_role_output(role, response.text)

    call = llm.calls[0]
    assert call.system == system and TASK["issue"] in call.user
    assert parsed  # non-empty model / diff
    if role.output_format is OutputFormat.UNIFIED_DIFF:
        assert isinstance(parsed, str) and parsed.startswith("--- ")
    else:
        assert hasattr(parsed, "model_dump")


@pytest.mark.parametrize("role_id", REQUIRED_CORE_ROLES)
def test_malformed_answer_is_rejected_for_every_role(registry, role_id):
    role = registry.get_role(role_id)
    llm = MockLLM(responses={role_id: "I fixed it, here is my explanation."})
    text = llm.complete(system="s", user="u", role=role_id).text
    with pytest.raises(OutputFormatError):
        parse_role_output(role, text)


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


def test_tester_is_the_only_core_role_that_can_run_tests(registry):
    can_run = [r.id for r in registry.roles.values() if "run_tests" in r.tools]
    assert can_run == ["tester"]
    assert not any("edit_file" in r.tools for r in registry.roles.values())
