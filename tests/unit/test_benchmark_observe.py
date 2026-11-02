import asyncio
import json
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import urlopen

import pytest

from raac.benchmark.observe import BenchmarkObserver
from raac.common.trace_web import TraceRuns


def trace(tmp_path, character):
    path = tmp_path / ("benchmark-" + character * 32) / "llm.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"event": "run_started", "run_id": path.parent.name}) + "\n")
    return path


def test_web_reuses_one_server_and_only_registered_runs(tmp_path, monkeypatch):
    opened = []
    monkeypatch.setattr("raac.benchmark.observe.webbrowser.open", lambda url: opened.append(url) or True)
    observer = BenchmarkObserver("web")
    first, second = trace(tmp_path, "a"), trace(tmp_path, "b")
    try:
        observer.ready(first)
        observer.ready(second)
        assert len(opened) == 1
        url = opened[0]
        with urlopen(url + "api/runs") as response:
            assert json.load(response) == {"runs": [first.parent.name, second.parent.name], "active": second.parent.name}
        with urlopen(url + "api/events?cursor=0&run=" + first.parent.name) as response:
            assert json.load(response)["log_name"].startswith(first.parent.name)
        with urlopen(url + "api/events?cursor=0") as response:
            assert json.load(response)["log_name"].startswith(second.parent.name)
        with pytest.raises(HTTPError) as error:
            urlopen(url + "api/events?run=../outside")
        assert error.value.code == 404
    finally:
        observer.close()
    assert observer.server is None and observer.thread is None


def test_viewer_failure_does_not_break_benchmark(tmp_path, monkeypatch, capsys):
    def fail(*args, **kwargs):
        raise OSError("fixture")
    monkeypatch.setattr("raac.benchmark.observe.create_trace_server", fail)
    observer = BenchmarkObserver("web")
    observer.ready(trace(tmp_path, "a"))
    assert observer.mode == "none"
    assert "benchmark continues" in capsys.readouterr().err


def test_none_does_not_start_threads_or_browser(tmp_path, monkeypatch):
    monkeypatch.setattr("raac.benchmark.observe.webbrowser.open", lambda _: pytest.fail("browser opened"))
    observer = BenchmarkObserver("none")
    observer.ready(trace(tmp_path, "a"))
    observer.close()
    assert observer.thread is None


def test_missing_browser_keeps_http_viewer_available(tmp_path, monkeypatch, capsys):
    def fail(url):
        raise OSError("no browser")
    monkeypatch.setattr("raac.benchmark.observe.webbrowser.open", fail)
    observer = BenchmarkObserver("web")
    try:
        observer.ready(trace(tmp_path, "a"))
        assert observer.server is not None
        assert "mở URL" in capsys.readouterr().err
    finally:
        observer.close()


@pytest.mark.asyncio
async def test_terminal_streams_final_cleanup_and_stops(tmp_path, capsys):
    path = trace(tmp_path, "a")
    observer = BenchmarkObserver("terminal")
    try:
        observer.ready(path)
        with path.open("a") as stream:
            stream.write(json.dumps({"event": "benchmark_phase_completed", "phase": "cleanup", "status": "completed"}) + "\n")
        await observer.finish()
        assert not observer.thread.is_alive()
        assert "cleanup" in capsys.readouterr().err
    finally:
        observer.close()


@pytest.mark.asyncio
async def test_ctrl_c_after_completion_preserves_exit_code(tmp_path, monkeypatch):
    from raac.benchmark import runner

    path = trace(tmp_path, "a")
    monkeypatch.setattr("raac.benchmark.observe.webbrowser.open", lambda _: True)
    finished = asyncio.Event()
    async def execute(args, ready):
        ready(path)
        finished.set()
        return 7
    monkeypatch.setattr(runner, "_execute", execute)
    task = asyncio.create_task(runner.execute(SimpleNamespace(observe="web")))
    await finished.wait()
    await asyncio.sleep(0)
    task.cancel()
    assert await task == 7


@pytest.mark.asyncio
async def test_running_cancellation_is_not_swallowed(monkeypatch):
    from raac.benchmark import runner

    started = asyncio.Event()
    async def execute(args, ready):
        started.set()
        await asyncio.Event().wait()
    monkeypatch.setattr(runner, "_execute", execute)
    task = asyncio.create_task(runner.execute(SimpleNamespace(observe="none")))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


def test_trace_registry_rejects_other_roots_and_non_run_paths(tmp_path):
    runs = TraceRuns()
    runs.register(trace(tmp_path, "a"))
    with pytest.raises(ValueError):
        runs.register(trace(tmp_path / "elsewhere", "b"))


@pytest.mark.parametrize("mode", [None, "web", "terminal", "none"])
def test_cli_observe_flag_default_and_modes(mode, monkeypatch):
    import raac.cli
    from raac.benchmark import runner

    seen = []
    async def execute(args):
        seen.append(args.observe)
        return 0
    monkeypatch.setattr(runner, "execute", execute)
    argv = ["benchmark", "run", "--task-id", "fixture"]
    if mode is not None:
        argv += ["--observe", mode]
    assert raac.cli.main(argv) == 0
    assert seen == [mode or "web"]
