import json

import pytest
from test_lifecycle_loop import make_runner, request, tool

from raac.common.errors import AgentExecutionError
from raac.harness.layer3_execution.tool_orchestrator import RunCommand, RunTests


def report(covered):
    return {"tests_to_run": ["suite"], "results": [{"test": "suite", "status": "passed", "details": ""}],
            "verdict": "pass", "covered_ac_ids": covered, "blocking_issues": []}


def handoff_request():
    return request() | {"artifacts": {"patch_hash": "fixture", "ac_catalog": {"AC1": "first", "AC2": "second"}}}


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["passed", "failed"])
async def test_missing_coverage_stays_with_tester_but_assertion_failure_hands_off(tmp_path, status):
    replies = [{"content": json.dumps(report(["AC1"]))}, {"content": json.dumps(report(["AC1", "AC2"]))}]
    runner, client, seen = make_runner(tmp_path, replies)
    workspace = runner.tools.workspace
    workspace.test_spec = {"command": ["python", "-m", "unittest"], "cwd": ".", "env": {}}
    executions = []

    async def execute(*args, **kwargs):
        executions.append(kwargs)
        return {"status": status, "tests_run": 1, "failed": int(status == "failed"),
                "test_cases": ["suite"], "summary": "observed"}

    workspace.executor.run_tests = execute
    try:
        result = await runner("Tester", handoff_request())
        assert result.runtime["status"] == status
        assert len(seen) == len(executions) == (2 if status == "passed" else 1)
        if status == "passed":
            assert "Missing IDs: AC2" in seen[1]["messages"][-1]["content"]
            assert result.output["covered_ac_ids"] == ["AC1", "AC2"]
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_missing_command_can_be_selected_without_guessing(tmp_path):
    runner, client, seen = make_runner(tmp_path, [
        {"content": json.dumps(report(["AC1", "AC2"]))},
        {"tool_calls": [tool("run_tests", {"command": ["node", "--test"]})]},
        {"content": json.dumps(report(["AC1", "AC2"]))},
    ])
    async def execute(*args, **kwargs):
        assert kwargs["command"] == ["node", "--test"]
        return {"status": "passed", "tests_run": 1, "failed": 0, "test_cases": ["suite"], "summary": "observed"}
    runner.tools.workspace.executor.run_tests = execute
    try:
        assert (await runner("Tester", handoff_request())).runtime["status"] == "passed"
        assert len(seen) == 3
        assert "No suite has been selected" in seen[1]["messages"][-1]["content"]
    finally:
        await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("selected", [False, True])
async def test_unfinished_tester_stops_at_existing_call_cap(tmp_path, selected):
    runner, client, seen = make_runner(tmp_path, [{"content": json.dumps(report([]))}] * 10)
    if selected:
        runner.tools.workspace.test_spec = {"command": ["node", "--test"], "cwd": ".", "env": {}}
        async def execute(*args, **kwargs):
            return {"status": "passed", "tests_run": 1, "failed": 0, "test_cases": ["suite"], "summary": "observed"}
        runner.tools.workspace.executor.run_tests = execute
    try:
        with pytest.raises(AgentExecutionError, match="incomplete_test_coverage" if selected else "missing_test_command"):
            await runner("Tester", handoff_request())
        assert len(seen) == 10
        assert (runner.tools.workspace.test_spec is not None) == selected
    finally:
        await client.aclose()


@pytest.mark.parametrize("model,args", [(RunCommand, {"command": ["echo"], "cwd": "/workspace"}),
    (RunTests, {"report_path": "../report.xml"})])
def test_path_errors_explain_repository_relative_contract(model, args):
    with pytest.raises(ValueError, match="repository-relative"):
        model.model_validate(args)
