"""Runtime adapter, streaming boundary and Textual interaction regressions."""
import asyncio
import json
import sys

import pytest

from raac.common.tracing import JSONLTrace
from raac.harness.layer3_execution.local_executor import (
    LocalExecutionPolicy,
    LocalExecutor,
    execution_context,
)
from raac.tui.bridge import IDENTITY, RuntimeBridge
from raac.tui.state import RunState, literal, read_page


def test_history_uses_only_codebase_results(monkeypatch):
    from pathlib import Path
    from raac.tui import app

    monkeypatch.setenv('LOCALAPPDATA', '/unused-history')
    screens = []
    dashboard = app.MASApp()
    monkeypatch.setattr(dashboard, 'push_screen', lambda screen, callback: screens.append(screen))
    dashboard.action_history()
    assert screens[0].roots == [Path(app.__file__).resolve().parents[3] / 'results']


def event(sequence, activation='one', **fields):
    return dict(run_id='run', agent_call_id=activation, role='Coder', sequence=sequence,
                event='lifecycle_transition', to_state='PLANNING', **fields)


def test_adapter_dynamic_replay_usage_and_bad_events():
    state = RunState()
    records = [event(1), event(2, 'two'), event(3)]
    for item in records + records:
        state.apply(item)
    assert len(state.agents) == 2
    assert len(state.agents[('run', 'one')].entries) == 2
    state.apply(dict(event(4), event='llm_call', usage={'prompt_tokens': 20, 'completion_tokens': 3}))
    state.apply(dict(event(5), event='llm_call', usage=None))
    agent = state.agents[('run', 'one')]
    assert (agent.input_tokens, agent.output_tokens, agent.usage_missing) == (20, 3, True)
    for bad in [None, [], {}, {'event': 3}, {'event': 'llm_call', 'run_id': []}]:
        assert state.apply(bad) is None
    state.apply(dict(event(6), timestamp=None, phase=['bad']))
    state.apply({'run_id': 'run', 'sequence': 7, 'event': 'run_cancelled'})
    assert all(a.status == 'cancelled' for a in state.agents.values())
    replay = RunState()
    for item in records:
        replay.apply(item)
    assert list(replay.agents) == list(state.agents)


def test_state_stress_is_bounded():
    state = RunState()
    for seq in range(1, 12001):
        state.apply(event(seq, str(seq % 8)))
    assert len(state.agents) == 8
    assert all(len(a.entries) == 1000 for a in state.agents.values())


def test_literal_and_partial_journal(tmp_path):
    assert literal('ok\x1b[31mred\x1b[0m\x1b]52;c;evil\x07\r\x00') == 'okred'
    path = tmp_path / 'log'
    path.write_bytes(b'bad\n' + json.dumps(event(1)).encode() + b'\n{"event":')
    records, offset = read_page(path)
    assert len(records) == 1
    with path.open('ab') as stream:
        stream.write(b'"run_completed","run_id":"run"}\n')
    records, end = read_page(path, offset)
    assert records[0]['event'] == 'run_completed'
    assert end == path.stat().st_size


def test_subscriber_redacted_isolated_and_persisted(tmp_path):
    seen = []
    def subscriber(record):
        seen.append(record.copy())
        record['text'] = 'mutated'
        raise RuntimeError('unsafe-body')
    path = tmp_path / 'log'
    trace = JSONLTrace(path, secrets=('split-secret',), subscriber=subscriber)
    trace.emit({'run_id': 'r'}, event='output', text='split-secret', password='value')
    assert seen[0]['text'] == '[REDACTED]'
    assert json.loads(path.read_text())['text'] == '[REDACTED]'


@pytest.mark.asyncio
@pytest.mark.parametrize('approved', [True, False])
async def test_bridge_identity_single_resolution_and_cancel(approved):
    bridge = RuntimeBridge()
    request = {'invocation_id': 'i', 'revision': 2, 'operation_id': 'o', 'arguments_hash': 'h', 'arguments': {'password': 'private'}}
    task = asyncio.create_task(bridge.approval(request))
    await asyncio.sleep(0)
    token, shown = bridge.approvals()[0]
    assert shown['arguments']['password'] == '[REDACTED]'
    for key in IDENTITY:
        assert not bridge.resolve(token, dict(request, **{key: 'wrong'}), True)
    assert bridge.resolve(token, request, approved)
    assert not bridge.resolve(token, request, approved)
    assert (await task).approve is approved
    task = asyncio.create_task(bridge.approval(request))
    await asyncio.sleep(0)
    token, _ = bridge.approvals()[0]
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not bridge.resolve(token, request, True)


@pytest.mark.asyncio
async def test_stream_utf8_split_secret_and_agent_context(tmp_path):
    trace = JSONLTrace(tmp_path / 'log', secrets=('known-secret', 'multi\nline-secret'))
    seen = []
    trace.subscriber = seen.append
    executor = LocalExecutor(LocalExecutionPolicy())
    executor.trace = trace
    context = {'run_id': 'r', 'agent_call_id': 'a', 'tool_call_id': 't'}
    token = execution_context.set(context)
    script = "import os,time; os.write(1,b'\\xe1'); time.sleep(.03); os.write(1,b'\\xbb\\x87 known-sec'); time.sleep(.03); os.write(1,b'ret\\nmulti\\nline-secret\\n'); os.write(2,b'error\\n')"
    try:
        result = await executor.run_command(tmp_path, command=[sys.executable, '-c', script])
    finally:
        execution_context.reset(token)
    assert result['exit_code'] == 0
    output = ''.join(e['text'] for e in seen if e['stream'] == 'stdout')
    assert 'ệ' in output and 'known' not in output and 'line-secret' not in output
    assert all(e['agent_call_id'] == 'a' and e['tool_call_id'] == 't' for e in seen)
    assert any(e['stream'] == 'stderr' and 'error' in e['text'] for e in seen)


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['timeout', 'limit', 'cancel'])
async def test_stream_cleanup(tmp_path, kind):
    executor = LocalExecutor(LocalExecutionPolicy(timeout_seconds=.1 if kind == 'timeout' else 10., max_output_bytes=1024))
    executor.trace = JSONLTrace(tmp_path / 'log')
    script = "import time; print('start',flush=True); " + ("print('x'*3000,flush=True)" if kind == 'limit' else "time.sleep(20)")
    task = asyncio.create_task(executor.run_command(tmp_path, command=[sys.executable, '-c', script]))
    if kind == 'cancel':
        await asyncio.sleep(.15)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 5)
    else:
        result = await asyncio.wait_for(task, 5)
        assert result['error_kind'] == ('TimeoutError' if kind == 'timeout' else 'OutputLimitError')


@pytest.mark.asyncio
async def test_pilot_resize_detail_history_and_approval(tmp_path):
    pytest.importorskip('textual')
    from raac.tui.app import AgentPanel, ApprovalDialog, MASApp
    log = tmp_path / 'run' / 'llm.jsonl'
    trace = JSONLTrace(log)
    for i in range(6):
        trace.emit({'run_id': 'run', 'agent_call_id': str(i), 'role': 'Coder'}, event='lifecycle_transition', to_state='PLANNING')
    app = MASApp(log=log)
    async with app.run_test(size=(200, 55)) as pilot:
        for _ in range(50):
            await pilot.pause(.05)
            if len(app.panels) == 6:
                break
        assert len(app.panels) == 6
        key = next(iter(app.panels))
        app.select_agent(key)
        await pilot.press('enter')
        await pilot.pause()
        assert len(app.screen_stack) == 2
        await pilot.press('escape')
        panel = app.panels[key]
        await pilot.press('k')
        assert not panel.follow
        await pilot.press('ctrl+l')
        assert panel.follow
        for width, height, columns in [(200,55,4),(160,45,4),(120,35,3),(100,30,2),(80,24,2),(60,20,1),(40,15,1)]:
            await pilot.resize_terminal(width, height)
            await pilot.pause()
            assert app.dashboard.query_one('#grid').styles.grid_size_columns == columns
            assert app.selected == key
            assert all(p.region.right <= width for p in app.panels.values() if p.display)
        await pilot.resize_terminal(39,14)
        await pilot.pause()
        assert app.dashboard.query_one('#resize-hint').display
        await pilot.resize_terminal(100,30)
        await pilot.pause()
        request = {'invocation_id': 'i', 'revision': 1, 'operation_id': 'o', 'arguments_hash': 'h', 'role': 'Coder', 'tool': 'run_command'}
        pending = asyncio.create_task(app.bridge.approval(request))
        await asyncio.sleep(0)
        app.action_approval()
        await pilot.pause()
        assert isinstance(app.screen, ApprovalDialog)
        await pilot.click('#approve')
        assert (await pending).approve
        app.action_history()
        await pilot.pause()
        await pilot.press('escape')
        assert app.selected == key
        assert app.dashboard.query(AgentPanel)

@pytest.mark.asyncio
async def test_stream_arrives_before_command_finishes(tmp_path):
    seen = asyncio.Event()
    records = []
    def subscriber(record):
        records.append(record)
        seen.set()
    executor = LocalExecutor(LocalExecutionPolicy())
    executor.trace = JSONLTrace(tmp_path / 'log', secrets=('a-very-long-api-credential',), subscriber=subscriber)
    task = asyncio.create_task(executor.run_command(tmp_path, command=[sys.executable, '-c', "import time; print('live',flush=True); time.sleep(20)"]))
    try:
        await asyncio.wait_for(seen.wait(), 5)
        assert not task.done()
        assert records[0]['text'].replace('\r\n', '\n') == 'live\n'
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


def test_cli_tui_dispatch_is_lazy(tmp_path, monkeypatch):
    import raac.mas_cli as cli
    calls = []
    monkeypatch.setattr(cli, 'launch_tui', lambda **kw: calls.append(kw) or 0)
    assert cli.main(['tui', '--workspace', str(tmp_path)]) == 0
    assert calls[-1]['topology'] == 'chain' and calls[-1]['prompt'] == ''
    prompt = tmp_path / 'prompt.txt'
    prompt.write_text('Original language', encoding='utf8')
    assert cli.main(['tui', '--workspace', str(tmp_path), '--prompt-file', str(prompt)]) == 0
    assert calls[-1]['prompt'] == 'Original language'
    assert cli.main(['show-log', '--log', str(tmp_path / 'log'), '--tui']) == 0
    assert calls[-1]['log'] == tmp_path / 'log'

@pytest.mark.asyncio
async def test_queue_overflow_replays_journal_without_losing_approval(tmp_path):
    pytest.importorskip('textual')
    from raac.tui.app import MASApp
    log = tmp_path / 'llm.jsonl'
    app = MASApp(log=log)
    trace = JSONLTrace(log, subscriber=app.bridge.event)
    for seq in range(1300):
        trace.emit({'run_id': 'run', 'agent_call_id': str(seq % 4), 'role': 'Worker'}, event='lifecycle_transition', to_state='PLANNING')
    assert app.bridge.notifications.qsize() == 256
    async with app.run_test(size=(100,30)) as pilot:
        pending = asyncio.create_task(app.bridge.approval({'invocation_id': 'i', 'revision': 1, 'operation_id': 'o', 'arguments_hash': 'h'}))
        try:
            for _ in range(20):
                await pilot.pause(.05)
                if app.state.sequences.get('run') == 1300:
                    break
            assert app.state.sequences['run'] == 1300
            assert len(app.state.agents) == 4
            assert sum(len(a.entries) for a in app.state.agents.values()) == 1300
            assert app.bridge.approvals()
        finally:
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending

@pytest.mark.asyncio
async def test_history_during_run_keeps_approvals_and_panel_scroll(tmp_path):
    pytest.importorskip('textual')
    from textual.widgets import RichLog

    from raac.tui.app import MASApp
    log = tmp_path / 'current.jsonl'
    old = tmp_path / 'old.jsonl'
    trace = JSONLTrace(log)
    for _ in range(100):
        trace.emit({'run_id': 'current', 'agent_call_id': 'a', 'role': 'Coder'}, event='command_output', text='line\n')
    JSONLTrace(old).emit({'run_id': 'old', 'agent_call_id': 'b', 'role': 'Tester'}, event='agent_started')
    app = MASApp(log=log)
    async with app.run_test(size=(100,30)) as pilot:
        key = ('current','a')
        for _ in range(50):
            await pilot.pause(.02)
            if key in app.panels:
                break
        app.select_agent(key)
        await pilot.pause()
        await pilot.press('pageup')
        panel = app.panels[key]
        assert not panel.follow
        y = panel.query_one(RichLog).scroll_y
        request = {'invocation_id': 'i', 'revision': 1, 'operation_id': 'o', 'arguments_hash': 'h'}
        task = asyncio.create_task(app.bridge.approval(request))
        await asyncio.sleep(0)
        try:
            await app.switch_view(old)
            await pilot.pause(.1)
            assert app.bridge.approvals()
            assert not task.done()
            trace.emit({'run_id': 'current', 'agent_call_id': 'a'}, event='command_output', text='new\n')
            await app.action_current()
            await pilot.pause(.1)
            assert app.selected == key
            assert not app.panels[key].follow
            assert app.panels[key].query_one(RichLog).scroll_y == y
            assert app.bridge.approvals()
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
