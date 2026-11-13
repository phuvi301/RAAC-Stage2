"""Local level selection uses existing engines; L4 never constructs a runtime."""
import json
import sys
from pathlib import Path

import httpx
import pytest
from test_lifecycle_loop import FINAL
from test_token_streaming import sse_response

from raac.harness.layer1_lifecycle import ApprovalDecision
from raac.llm.client import LLMClient
from raac.llm.config import LLMConfig
from raac.mas_cli import DemoError, run_local


@pytest.mark.asyncio
@pytest.mark.parametrize('level', ['L1', 'L2'])
@pytest.mark.parametrize('approved', [True, False])
async def test_local_engine_levels_with_streamed_http_and_real_approved_tests(tmp_path, monkeypatch, level, approved):
    workspace = tmp_path / 'workspace'
    (workspace / 'tests').mkdir(parents=True)
    (workspace / 'value.py').write_text('VALUE = 1\n')
    (workspace / 'tests/test_value.py').write_text('import unittest\nfrom pathlib import Path\nfrom value import VALUE\nclass TestValue(unittest.TestCase):\n def test_value(self):\n  Path("ran").touch()\n  self.assertEqual(VALUE, 2)\n')
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path / 'artifacts'))
    called, approvals, events = [], [], []
    edited = tested = False

    def respond(request):
        nonlocal edited, tested
        payload = json.loads(request.content)
        assert payload['stream'] is True
        system = payload['messages'][0]['content'].replace('**', '')
        role = next(name for name in ('Router', 'Analyst', 'Planner', 'Coder', 'Tester', 'Reviewer') if system.startswith('You are the ' + name))
        called.append(role)
        calls = None
        if role == 'Router':
            output = {'selected_roles': ['Coder', 'Tester'], 'skipped': [
                {'role': r, 'reason': 'Small focused change'} for r in ('Analyst', 'Planner', 'Reviewer')], 'reason': 'Run code and tests'}
        elif role == 'Analyst':
            output = FINAL
        elif role == 'Planner':
            output = {'steps': [{'id': 1, 'description': 'Set VALUE to 2', 'files': ['value.py']}], 'covered_ac_ids': ['AC1']}
        elif role == 'Coder' and not edited:
            edited = True
            output = None
            calls = [{'id': 'edit', 'type': 'function', 'function': {'name': 'edit_file',
                'arguments': json.dumps({'path': 'value.py', 'content': 'VALUE = 2\n'})}}]
        elif role == 'Coder':
            output = 'Implementation saved'
        elif role == 'Tester' and not tested:
            tested = True
            output = None
            calls = [{'id': 'test', 'type': 'function', 'function': {'name': 'run_tests',
                'arguments': json.dumps({'command': [sys.executable, '-m', 'unittest', 'discover', '-s', 'tests']})}}]
        elif role == 'Tester':
            output = {'tests_to_run': ['test_value'], 'results': [{'test': 'test_value', 'status': 'passed'}],
                      'verdict': 'pass', 'covered_ac_ids': ['AC1']}
        else:
            output = {'decision': 'approve', 'summary': 'Observed tests pass', 'comments': []}
        return sse_response({'model': 'fixture', 'choices': [{'message': {'role': 'assistant',
            'content': output if isinstance(output, str) else json.dumps(output) if output is not None else None,
            **({'tool_calls': calls} if calls else {})}, 'finish_reason': 'tool_calls' if calls else 'stop'}],
            'usage': {'prompt_tokens': 10, 'completion_tokens': 5}})

    async def approve(request):
        if not approvals:
            assert not (workspace / 'ran').exists()
        approvals.append(request['tool'])
        return ApprovalDecision(**{k: request[k] for k in ('invocation_id', 'revision', 'operation_id', 'arguments_hash')}, approve=approved)

    monkeypatch.setattr('raac.mas_cli.load_llm_config', lambda *a, **kw: LLMConfig(model='fixture'))
    monkeypatch.setattr('raac.mas_cli.LLMClient', lambda config, *, log_path: LLMClient(config, log_path=log_path, transport=httpx.MockTransport(respond)))
    result = await run_local(workspace, 'Set VALUE to 2', level=level, stream=True, approval_handler=approve, event_handler=events.append)
    from raac import mas_cli
    assert (Path(mas_cli.__file__).resolve().parents[2] / 'results' / result['run_id'] / 'result.json').is_file()
    if not approved:
        assert result['status'] == 'aborted' and result['verdict'] != 'PASS'
        assert approvals == ['run_tests'] and not (workspace / 'ran').exists()
        return
    assert result['level'] == level and result['verdict'] == 'PASS', result.get('error')
    assert (workspace / 'value.py').read_text() == 'VALUE = 2\n'
    assert approvals.count('run_tests') == 2 and result['usage']['usage_complete']
    assert result['provenance']['effective_config']['level'] == level
    assert any(e['event'] == 'llm_stream_ended' and e['completed'] for e in events)
    assert not any(e.get('level') == 'L3' for e in events)
    if level == 'L1':
        assert set(called) == {'Analyst', 'Planner', 'Coder', 'Tester', 'Reviewer'}
    else:
        assert set(called) == {'Router', 'Coder', 'Tester'}
        assert sum(e['event'] == 'agent_skipped' for e in events) == 3


@pytest.mark.asyncio
async def test_l4_mock_has_no_client_executor_or_workspace_changes(tmp_path, monkeypatch):
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path / 'artifacts'))
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    (workspace / 'keep.txt').write_text('unchanged')
    def forbidden(*args, **kwargs):
        raise AssertionError('Mock constructed a real runtime')
    for name in ('LLMClient', 'LocalExecutor', 'load_llm_config'):
        monkeypatch.setattr('raac.mas_cli.' + name, forbidden)
    monkeypatch.setattr('raac.mas_cli.WorkspaceTools.__init__', forbidden)
    events = []
    result = await run_local(workspace, 'Mock only', level='L4', event_handler=events.append)
    from raac import mas_cli
    assert Path(result['log']).parent.parent == Path(mas_cli.__file__).resolve().parents[2] / 'results'
    assert result['status'] == 'mock' and result['mock'] and result['verdict'] is None
    assert result['usage']['calls'] == 0 and result['execution']['mode'] == 'mock'
    assert (workspace / 'keep.txt').read_text() == 'unchanged'
    assert list(workspace.iterdir()) == [workspace / 'keep.txt']
    assert any(e['event'] == 'mock_output' for e in events)


@pytest.mark.asyncio
async def test_invalid_level_fails_before_model_configuration(tmp_path, monkeypatch):
    monkeypatch.setattr('raac.mas_cli.load_llm_config', lambda *a, **kw: pytest.fail('Invalid level reached config'))
    with pytest.raises(DemoError, match='Local level'):
        await run_local(tmp_path, 'test', level='L5')


@pytest.mark.asyncio
async def test_pilot_selects_levels_and_mock_history(tmp_path, monkeypatch):
    pytest.importorskip('textual')
    from textual.widgets import Select

    from raac.tui.app import MASApp
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path / 'artifacts'))
    app = MASApp(workspace=tmp_path / 'workspace', prompt='Mock request')
    app.workspace.mkdir()
    async with app.run_test(size=(100, 30)) as pilot:
        assert app.dashboard.query_one('#level', Select).value == 'L3'
        for level in ('L1', 'L2', 'L3', 'L4'):
            app.set_level(level)
            await pilot.pause()
            assert app.dashboard.query_one('#topology', Select).disabled == (level != 'L3')
        app.set_level('L3')
        await pilot.pause()
        app.dashboard.query_one('#level', Select).focus()
        await pilot.press('enter', 'down', 'enter')
        assert app.dashboard.query_one('#level', Select).value == 'L4'
        await pilot.press('ctrl+enter')
        for _ in range(100):
            await pilot.pause(.02)
            if app.current_path and not app.bridge.busy and app.state.status == 'mock':
                break
        assert app.state.level == 'L4' and app.state.status == 'mock'
        assert all(a.status == 'MOCK' for a in app.state.agents.values())
        assert 'no model' in app.bridge.message
        await app.switch_view(app.current_path)
        await pilot.resize_terminal(60, 20)
        await pilot.pause()
        app.action_level()
        await pilot.pause()
        assert len(app.screen_stack) == 2
        await pilot.press('escape')
        app.bridge.busy = True
        app.update_level_controls()
        assert app.dashboard.query_one('#level', Select).disabled
        app.bridge.busy = False
