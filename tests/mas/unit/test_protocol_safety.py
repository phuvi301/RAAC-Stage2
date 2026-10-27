
import pytest

from raac.common.trace_viewer import TraceViewer
from raac.common.trace_web import present_event
from raac.harness.layer3_execution.local_executor import (
    LocalExecutionPolicy,
    LocalExecutor,
)
from raac.harness.layer3_execution.local_test_runner import normalize_pytest_report
from raac.harness.layer3_execution.tool_orchestrator import RunTests


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
