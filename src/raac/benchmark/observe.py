"""CLI-owned observation; benchmark execution never depends on its availability."""

import asyncio
import sys
import threading
import webbrowser

from raac.common.trace_viewer import show_trace
from raac.common.trace_web import TraceRuns, create_trace_server


class BenchmarkObserver:
    def __init__(self, mode):
        self.mode = mode
        self.runs = TraceRuns()
        self.server = None
        self.thread = None
        self.stop = threading.Event()

    @staticmethod
    def emit(message):
        print(message, file=sys.stderr, flush=True)

    def ready(self, path):
        self.emit(f"Benchmark log: {path}")
        if self.mode == "none":
            return
        try:
            self.runs.register(path)
            if self.mode == "web":
                if self.server is not None:
                    return
                self.server = create_trace_server(path, runs=self.runs)
                self.thread = threading.Thread(target=self.server.serve_forever,
                                               kwargs={"poll_interval": 0.1}, daemon=True)
                self.thread.start()
                url = f"http://127.0.0.1:{self.server.server_address[1]}/"
                self.emit(f"RAAC observe: {url}")
                try:
                    opened = webbrowser.open(url)
                except Exception:  # noqa: BLE001 -- a missing browser does not make the HTTP viewer unusable.
                    opened = False
                if not opened:
                    self.emit("Không tự mở được browser; mở URL trên để theo dõi.")
            else:
                if self.thread is not None:
                    self.thread.join(timeout=1)
                self.thread = threading.Thread(target=self._terminal, args=(path,), daemon=True)
                self.thread.start()
        except Exception as exc:  # noqa: BLE001 -- observation must never stop a benchmark.
            self.emit(f"Observe unavailable ({type(exc).__name__}); benchmark continues. Log: {path}")
            self.close()
            self.mode = "none"

    def _terminal(self, path):
        try:
            show_trace(path, follow=True, emit=self.emit, stop=self.stop)
        except Exception as exc:  # noqa: BLE001 -- report observer failure without touching the run.
            self.emit(f"Terminal observe stopped ({type(exc).__name__}); log: {path}")

    async def finish(self):
        if self.server is not None:
            self.emit("Benchmark đã kết thúc. Web vẫn mở; Ctrl+C để đóng viewer.")
            try:
                while True:
                    await asyncio.sleep(0.25)
            except asyncio.CancelledError:
                pass  # The completed benchmark's exit code remains authoritative.
        elif self.thread is not None:
            await asyncio.to_thread(self.thread.join, 1)

    def close(self):
        self.stop.set()
        if self.server is not None:
            if self.thread is not None and self.thread.is_alive():
                self.server.shutdown()
            self.server.server_close()
            self.server = None
        if self.thread is not None and self.thread is not threading.current_thread():
            self.thread.join(timeout=1)
            self.thread = None
