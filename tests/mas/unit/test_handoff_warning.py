import json

import pytest
from test_lifecycle_loop import FINAL, make_runner, request, tool
from test_tester_handoff import handoff_request, report

from raac.common.errors import AgentExecutionError


@pytest.mark.asyncio
async def test_analyst_warns_before_tools_disabled_without_extending_cap(tmp_path):
    replies = [{"tool_calls": [tool(identifier=str(i))]} for i in range(4)]
    replies.append({"content": json.dumps(FINAL)})
    runner, client, seen = make_runner(tmp_path, replies)
    try:
        await runner("Analyst", request())
        assert len(seen) == 5
        assert "Prepare to hand off" not in seen[1]["messages"][0]["content"]
        for index, left in [(2, 2), (3, 1)]:
            prompt = seen[index]["messages"][0]["content"]
            assert f"{left} tool-enabled model call(s)" in prompt
            assert "unavailable starting at call 5" in prompt
            assert prompt.count("Prepare to hand off") == 1
            assert seen[index].get("tools")
        assert not seen[4].get("tools")
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_planner_warning_respects_existing_final_call_boundary(tmp_path):
    replies = [{"tool_calls": [tool(identifier=str(i))]} for i in range(5)]
    replies.append({"content": "invalid handoff"})
    runner, client, seen = make_runner(tmp_path, replies)
    try:
        with pytest.raises(AgentExecutionError):
            await runner("Planner", request())
        assert len(seen) == 6
        assert "Prepare to hand off" not in seen[2]["messages"][0]["content"]
        for index, left in [(3, 2), (4, 1)]:
            prompt = seen[index]["messages"][0]["content"]
            assert f"{left} tool-enabled model call(s)" in prompt
            assert "unavailable starting at call 6" in prompt
            assert seen[index].get("tools")
        assert not seen[5].get("tools")
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_tester_prioritizes_explicit_suite_and_warns_before_handoff(tmp_path):
    replies = [{"tool_calls": [tool(identifier=str(i))]} for i in range(6)]
    replies += [{"tool_calls": [tool("run_tests", {"command": ["node", "--test"]})]},
                {"tool_calls": [tool(identifier="last-read")]},
                {"content": json.dumps(report(["AC1", "AC2"]))}]
    runner, client, seen = make_runner(tmp_path, replies)
    async def execute(*args, **kwargs):
        return {"status": "passed", "tests_run": 1, "failed": 0,
                "test_cases": ["suite"], "summary": "observed"}
    runner.tools.workspace.executor.run_tests = execute
    try:
        result = await runner("Tester", handoff_request())
        assert result.runtime["status"] == "passed"
        assert len(seen) == 9
        assert "No test suite has been selected" in seen[0]["messages"][0]["content"]
        assert "run_command/run_commands probes" in seen[0]["messages"][0]["content"]
        assert "unavailable starting at call 9" in seen[6]["messages"][0]["content"]
        assert "A test suite is selected" in seen[7]["messages"][0]["content"]
        assert "No test suite has been selected" not in seen[7]["messages"][0]["content"]
        assert all(item.get("tools") for item in seen[:8])
        assert not seen[8].get("tools")
    finally:
        await client.aclose()
