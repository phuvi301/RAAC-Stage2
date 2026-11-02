import json
from unittest.mock import AsyncMock

import httpx
import pytest
from test_completion_recovery import response

from raac.common.trace_viewer import TraceViewer
from raac.common.trace_web import present_event
from raac.harness.layer3_execution.local_executor import (
    LocalExecutionPolicy,
    LocalExecutor,
)
from raac.harness.layer3_execution.local_test_runner import normalize_pytest_report
from raac.harness.layer3_execution.tool_orchestrator import RunTests
from raac.llm.client import LLMClient, has_dsml_tool_call
from raac.llm.config import LLMConfig


def test_probe_cli_does_not_require_a_project_directory(monkeypatch):
    from raac import mas_cli

    execute = AsyncMock(return_value=0)
    monkeypatch.setattr(mas_cli, "execute", execute)
    assert mas_cli.main(["probe-tools"]) == 0
    assert execute.call_args.args[0].command == "probe-tools"


@pytest.mark.asyncio
async def test_local_pytest_cannot_overwrite_selected_repository_report(tmp_path):
    (tmp_path / "report.xml").write_text("preserve existing")
    (tmp_path / "test_safe.py").write_text("def test_safe(): assert True\n")
    executor = LocalExecutor(LocalExecutionPolicy())
    result = await executor.run_tests(tmp_path, command=["python", "-m", "pytest", "test_safe.py", "--junitxml=report.xml"],
                                      report_path="/tmp/report.xml")
    assert result["status"] == "passed" and result["tests_run"] == 1, result
    assert (tmp_path / "report.xml").read_text() == "preserve existing"
    assert "temporary" in result["report_note"]


@pytest.mark.parametrize("flag", [["--junitxml", "source.py"], ["--junit-xml=source.py"]])
def test_pytest_report_is_managed_before_path_validation(flag):
    command = ["python", "-m", "pytest", "tests", *flag]
    values = RunTests.model_validate({"command": command, "report_path": "/tmp/report.xml"})
    assert values.command == ["python", "-m", "pytest", "tests"]
    assert values.report_path is None
    assert command[-len(flag):] == flag
    assert normalize_pytest_report(["node", "--test"], "suite.xml") == (["node", "--test"], "suite.xml")


@pytest.mark.parametrize("marker", ["<｜DSML｜ invoke>", "<｜｜DSML｜｜ invoke>", "<DSML invoke>", "<|DSML| invoke>"])
def test_dsml_dialects_are_diagnostic_only(marker):
    assert has_dsml_tool_call(marker)
    assert not has_dsml_tool_call("DSML is a protocol, not an invocation")


def test_error_categories_and_terminal_response_pairing():
    event = {"event": "llm_call", "status": 200, "error": "InvalidResponse", "response_error": "truncated_completion",
             "llm_call_id": "call", "run_id": "run", "attempt": 1}
    assert present_event(event, 0)["title"] == "Output bị cắt"
    assert present_event(event | {"error": "ReadTimeout", "response_error": None}, 1)["title"] == "Lỗi HTTP/kết nối LLM"
    viewer = TraceViewer()
    assert viewer.render(event)
    response = event | {"event": "llm_response", "message": {"content": "partial"}}
    assert viewer.render(response) == []
    assert TraceViewer().render(response)  # Pasted standalone responses remain visible.
    integrity = present_event({"event": "tool_call_completed", "tool_name": "run_tests", "observation": {
        "ok": False, "error_kind": "WorkspaceIntegrityError", "path": "value.py", "error": "Tester modified source"}}, 2)
    assert integrity["title"] == "Vi phạm integrity workspace" and "value.py" in integrity["summary"]


def call(arguments='{"value":"raac-probe"}'):
    return {"id": "probe-call", "type": "function", "function": {"name": "echo_probe", "arguments": arguments}}


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,reason,count", [
    ("success", None, 2), ("dsml", "invalid_tool_call_format", 1),
    ("truncated", "truncated_completion", 1), ("arguments", "invalid_tool_arguments", 1),
    ("wrong_value", "invalid_tool_call", 1), ("http", "api_error", 1),
    ("followup_dsml", "invalid_tool_call_format", 2), ("followup_bad", "invalid_tool_result_response", 2),
])
async def test_protocol_probe_classifies_failures_without_executing_tools(tmp_path, kind, reason, count):
    seen = []
    def handler(req):
        payload = json.loads(req.content)
        seen.append(payload)
        if kind == "http":
            return httpx.Response(400, json={"error": "private provider body"})
        if len(seen) == 1:
            if kind == "dsml":
                data = response("<｜DSML｜ invoke name='echo_probe'>")
            elif kind == "truncated":
                data = response(None, "length")
            else:
                data = response(None, "tool_calls", tool_calls=[call("{" if kind == "arguments" else
                    '{"value":"wrong"}' if kind == "wrong_value" else '{"value":"raac-probe"}')])
        else:
            assert "tools" not in payload
            assert payload["messages"][-1] == {"role": "tool", "tool_call_id": "probe-call", "content": "raac-probe"}
            data = response("<DSML invoke>" if kind == "followup_dsml" else "wrong" if kind == "followup_bad" else "RAAC_PROBE_OK")
        return httpx.Response(200, json=data)
    client = LLMClient(LLMConfig(model="fixture", max_retries=0), log_path=tmp_path / "probe.jsonl", transport=httpx.MockTransport(handler))
    try:
        result = await client.probe_tools()
        assert result["status"] == ("passed" if reason is None else "failed")
        assert result["reason"] == reason and len(seen) == count
        assert "private" not in json.dumps(result)
        assert "private reasoning" not in client.log_path.read_text()
    finally:
        await client.aclose()
