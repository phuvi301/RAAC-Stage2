"""Thread boundary for one runtime task and its identity-bound approvals."""
import asyncio
import queue
import threading
import uuid
from dataclasses import dataclass

from raac.harness.layer1_lifecycle import ApprovalDecision

from .state import redactor

IDENTITY = ('invocation_id', 'revision', 'operation_id', 'arguments_hash')


@dataclass
class Pending:
    token: str
    request: dict
    display: dict
    future: asyncio.Future


class RuntimeBridge:
    def __init__(self):
        self.lock = threading.Lock()
        self.notifications = queue.Queue(maxsize=256)
        self.pending = {}
        self.display_requests = {}
        self.metadata = None
        self.loop = None
        self.task = None
        self.busy = False
        self.cancel_requested = threading.Event()
        self.message = ''
        self.approval_mode = 'manual'

    def event(self, record):
        if record.get('event') == 'approval_requested':
            with self.lock:
                self.display_requests[record['agent_call_id']] = record['request']
        # JSONL is the durable overflow buffer; queue contains only wake notices.
        try:
            self.notifications.put_nowait(record.get('sequence'))
        except queue.Full:
            pass

    def initialized(self, metadata):
        with self.lock:
            self.metadata = metadata

    async def approval(self, request):
        future = asyncio.get_running_loop().create_future()
        token = uuid.uuid4().hex
        with self.lock:
            shown = self.display_requests.pop(request['invocation_id'], None)
            pending = Pending(token, dict(request), shown or redactor().redact(request), future)
            self.pending[token] = pending
            automatic = self.approval_mode == 'auto'
        try:
            if automatic:
                self.resolve(token, request, True)
            return await future
        finally:
            with self.lock:
                self.pending.pop(token, None)

    def set_approval_mode(self, mode):
        if mode not in {'manual', 'auto'}:
            raise ValueError('Unknown approval mode')
        with self.lock:
            self.approval_mode = mode
            pending = [(item.token, dict(item.request)) for item in self.pending.values()] if mode == 'auto' else []
        for token, request in pending:
            self.resolve(token, request, True)

    def approvals(self):
        with self.lock:
            return [(item.token, dict(item.display)) for item in self.pending.values()]

    def resolve(self, token, identity, approve):
        with self.lock:
            item = self.pending.get(token)
            if item is None or any(identity.get(k) != item.request[k] for k in IDENTITY):
                return False
            # Remove before scheduling: a second keypress cannot resolve it twice.
            self.pending.pop(token)
        decision = ApprovalDecision(**{k: item.request[k] for k in IDENTITY}, approve=approve)

        def finish():
            if not item.future.done():
                item.future.set_result(decision)
        item.future.get_loop().call_soon_threadsafe(finish)
        return True

    def cancel(self):
        if self.cancel_requested.is_set():
            return
        self.cancel_requested.set()
        if self.loop and self.task:
            try:
                self.loop.call_soon_threadsafe(self.task.cancel)
            except RuntimeError:
                pass  # The run already closed its loop.

    def run(self, workspace, prompt, topology, level='L3'):
        from raac.mas_cli import run_local

        async def invoke():
            self.loop = asyncio.get_running_loop()
            self.task = asyncio.current_task()
            if self.cancel_requested.is_set():
                return
            try:
                result = await run_local(workspace, prompt, topology, self.approval,
                                level=level, stream=True, event_handler=self.event, run_handler=self.initialized)
                self.message = 'L4 mock finished; no model or tools used' if result.get('mock') else 'Run finished'
            except asyncio.CancelledError:
                self.message = 'Run cancelled; workspace changes retained'
            except Exception:  # noqa: BLE001 -- keep provider/config bodies out of the terminal
                self.message = 'Run failed; inspect the trace and local configuration'
            finally:
                self.task = None
                self.loop = None
        try:
            asyncio.run(invoke())
        finally:
            self.busy = False
