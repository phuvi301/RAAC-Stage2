import pytest
from test_lifecycle_loop import make_runner


def test_workspace_and_tool_runtime_roundtrip_preserves_original_baseline(tmp_path):
    runner, client, _ = make_runner(tmp_path, [])
    ws = runner.tools.workspace
    assert hasattr(ws, "snapshot_runtime"), "Workspace resume contract missing"
    snap = ws.snapshot_runtime()
    (ws.root / "value.py").write_text("VALUE = 2\n")
    ws.coder_paths.add("value.py")
    ws.code_edit_count = 1
    ws.environment.revision = 4
    ws.environment.ready = True
    ws.environment.configuration = {"python_executable": "python"}
    saved = ws.snapshot_runtime()
    other, other_client, _ = make_runner(tmp_path, [])
    other.tools.workspace.restore_runtime(saved)
    assert other.tools.workspace.baseline_files["value.py"] == b"VALUE = 1\n"
    assert other.tools.workspace.coder_paths == {"value.py"}
    assert other.tools.workspace.environment.revision == 4
    assert other.tools.workspace.current_patch().count("+VALUE = 2") == 1
    from raac.llm.usage import UsageTracker

    usage = UsageTracker()
    ctx = {"run_id": "a", "agent_id": "coder", "level": "L3"}
    usage.add(ctx, {"prompt_tokens": 3, "completion_tokens": 2})
    restored = UsageTracker()
    restored.restore_run("a", usage.export_run("a"))
    restored.restore_run("a", usage.export_run("a"))
    assert restored.snapshot(run_id="a")["total_tokens"] == 5
    assert restored.snapshot(run_id="a")["calls"] == 1
    runner.tools._observations[("a", "ref")] = {"result": {"status": "passed"}}
    runtime = runner.tools.snapshot_runtime()
    other.tools.restore_runtime(runtime)
    assert other.tools._observations[("a", "ref")]["result"]["status"] == "passed"
    import asyncio

    asyncio.run(client.aclose())
    asyncio.run(other_client.aclose())
