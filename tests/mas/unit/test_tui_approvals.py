"""Approval visibility and session-scoped auto approval use the same identity checks."""
import asyncio

import pytest
from textual.widgets import Button, Select

from raac.tui.app import MASApp
from raac.tui.bridge import IDENTITY, RuntimeBridge

REQUEST = {'invocation_id': 'i', 'revision': 1, 'operation_id': 'o', 'arguments_hash': 'h',
           'role': 'Coder', 'tool': 'run_command'}


@pytest.mark.asyncio
async def test_auto_approve_pending_future_and_back_to_manual():
    bridge = RuntimeBridge()
    assert bridge.approval_mode == 'manual'
    pending = asyncio.create_task(bridge.approval(REQUEST))
    await asyncio.sleep(0)
    token, _ = bridge.approvals()[0]
    bridge.set_approval_mode('auto')
    decision = await pending
    assert decision.approve and all(getattr(decision, k) == REQUEST[k] for k in IDENTITY)
    assert not bridge.resolve(token, REQUEST, True)
    assert not bridge.approvals()
    assert (await bridge.approval(dict(REQUEST, operation_id='next'))).approve
    bridge.set_approval_mode('manual')
    pending = asyncio.create_task(bridge.approval(REQUEST))
    await asyncio.sleep(0)
    assert not pending.done()
    token, _ = bridge.approvals()[0]
    assert bridge.resolve(token, REQUEST, False)
    assert not (await pending).approve
    with pytest.raises(ValueError):
        bridge.set_approval_mode('unknown')
    assert RuntimeBridge().approval_mode == 'manual'


@pytest.mark.asyncio
async def test_pending_button_notification_and_dropdown(tmp_path, monkeypatch):
    app = MASApp(workspace=tmp_path, prompt='Test')
    notices = []
    monkeypatch.setattr(app, 'notify', lambda *a, **kw: notices.append((a, kw)))
    async with app.run_test(size=(120, 35)) as pilot:
        pending = asyncio.create_task(app.bridge.approval(REQUEST))
        try:
            await pilot.pause(.2)
            button = app.dashboard.query_one('#approvals', Button)
            assert button.has_class('pending') and '1' in str(button.label)
            assert button.styles.background.hex == '#FFE066'
            assert len(notices) == 1
            await pilot.pause(.2)
            assert len(notices) == 1
            mode = app.dashboard.query_one('#approval-mode', Select)
            await pilot.click('#approval-mode')
            await pilot.press('down', 'enter')
            await pilot.pause(.2)
            assert mode.value == 'auto' and (await pending).approve
            assert not button.has_class('pending')
            assert button.region.right <= mode.region.x
            assert mode.region.right <= 120
            await pilot.resize_terminal(80, 24)
            await pilot.pause()
            assert mode.region.right <= 80
            # A fresh runtime for the next run retains the session's selection.
            monkeypatch.setattr(app, 'runtime', lambda *a: None)
            app.action_start()
            assert app.bridge.approval_mode == 'auto'
            app.bridge.busy = False
            mode.value = 'manual'
            await pilot.pause()
            assert app.bridge.approval_mode == 'manual'
        finally:
            if not pending.done():
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)


@pytest.mark.asyncio
async def test_read_only_history_cannot_enable_auto_approve(tmp_path):
    app = MASApp(log=tmp_path / 'llm.jsonl')
    async with app.run_test() as pilot:
        assert app.dashboard.query_one('#approval-mode', Select).disabled
        app.set_approval_mode('auto')
        await pilot.pause()
        assert app.bridge.approval_mode == 'manual'

@pytest.mark.asyncio
async def test_auto_mode_resolves_worker_thread_approvals(tmp_path, monkeypatch):
    decisions = []
    async def run_local(workspace, prompt, topology, approval_handler, **kwargs):
        for operation in ['batch', 'tests']:
            decisions.append(await approval_handler(dict(REQUEST, operation_id=operation)))
        return {'mock': True}
    monkeypatch.setattr('raac.mas_cli.run_local', run_local)
    bridge = RuntimeBridge()
    bridge.busy = True
    worker = asyncio.create_task(asyncio.to_thread(bridge.run, tmp_path, 'Test', 'chain'))
    try:
        for _ in range(100):
            if bridge.approvals():
                break
            await asyncio.sleep(.01)
        assert bridge.approvals()
        bridge.set_approval_mode('auto')
        await asyncio.wait_for(worker, 5)
        assert [d.operation_id for d in decisions] == ['batch', 'tests']
        assert all(d.approve for d in decisions)
        assert not bridge.busy and not bridge.approvals()
    finally:
        if not worker.done():
            bridge.cancel()
            await asyncio.wait_for(worker, 5)
