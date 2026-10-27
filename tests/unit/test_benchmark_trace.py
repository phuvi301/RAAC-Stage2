import json
from types import SimpleNamespace

from raac.common.trace_viewer import TraceViewer, show_trace
from raac.common.trace_web import present_event
from raac.common.tracing import JSONLTrace
from raac.harness.layer3_execution.docker_execution import (
    DockerExecutionPolicy,
    DockerExecutor,
)


def test_container_trace_redacts_stream_and_preserves_identity(tmp_path):
    trace = JSONLTrace(tmp_path / "trace.jsonl", secrets=("private-value",))
    executor = DockerExecutor(SimpleNamespace(), DockerExecutionPolicy())
    executor.bind_trace(trace, {"run_id": "run", "agent_id": "coder", "tool_call_id": "tool"}, content_enabled=True)
    executor.trace("command_output", {"stream": "stdout", "content": "private-value"})
    data = json.loads(trace.path.read_text())
    assert data["content"] == "[REDACTED]" and data["tool_call_id"] == "tool"


def test_trace_without_content_keeps_activity_without_raw_output(tmp_path):
    trace = JSONLTrace(tmp_path / "trace.jsonl")
    executor = DockerExecutor(SimpleNamespace(), DockerExecutionPolicy())
    executor.bind_trace(trace, {"run_id": "run"}, content_enabled=False)
    executor.trace("command_output", {"stream": "stderr", "content": "private output"})
    data = json.loads(trace.path.read_text())
    assert data["stream"] == "stderr" and data["bytes"] > 0
    assert "private output" not in trace.path.read_text()


def test_existing_viewers_show_live_container_events():
    event = {"event": "command_output", "agent_id": "coder", "stream": "stdout", "content": "building source"}
    assert "building source" in " ".join(TraceViewer(details=True).render(event))
    row = present_event(event, 1)
    assert not row["technical"]
    assert "building source" in str(row["payloads"])


def test_follow_waits_for_benchmark_evaluation_after_agent_completes(tmp_path):
    path = tmp_path / "trace.jsonl"
    events = [
        {"event": "benchmark_phase_started", "phase": "agent"},
        {"event": "run_completed", "status": "completed"},
        {"event": "benchmark_phase_started", "phase": "evaluation"},
        {"event": "benchmark_phase_failed", "phase": "evaluation", "status": "error"},
    ]
    path.write_text("".join(json.dumps(event) + "\n" for event in events))
    lines = []
    show_trace(path, follow=True, emit=lines.append)
    assert any("evaluation" in line for line in lines)
