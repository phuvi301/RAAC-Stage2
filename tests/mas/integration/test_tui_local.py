"""Drive the existing HTTP mock scenario through the actual Textual worker."""
import json
import time
from pathlib import Path

import pytest
import test_demo_local as scenario
from test_token_streaming import sse_response


@pytest.mark.asyncio
async def test_tui_full_chain_http_mock(tmp_path, monkeypatch):
    pytest.importorskip('textual')
    from textual.widgets import TextArea

    from raac.tui.app import ApprovalDialog, MASApp

    case = scenario.LocalChainTests('test_cli_nested_src_project_selects_tests_without_solution_file')
    await case.asyncSetUp()
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path))
    approvals = []
    original_client = scenario.LLMClient

    def streaming_client(config, *, log_path, transport):
        handler = transport.handler

        def respond(request):
            assert json.loads(request.content)['stream'] is True
            return sse_response(handler(request).json())

        transport.handler = respond
        return original_client(config, log_path=log_path, transport=transport)

    monkeypatch.setattr(scenario, 'LLMClient', streaming_client)

    async def drive(args):
        workspace = Path(args.project_dir) / 'repo'
        app = MASApp(workspace=workspace)
        async with app.run_test(size=(120, 35)) as pilot:
            app.dashboard.query_one(TextArea).load_text((Path(args.project_dir) / 'requirement/user_chat.txt').read_text())
            await pilot.press('ctrl+enter')
            assert app.bridge.busy
            deadline = time.monotonic() + 45
            while app.bridge.busy and time.monotonic() < deadline:
                pending = app.bridge.approvals()
                if pending and not isinstance(app.screen, ApprovalDialog):
                    approvals.append(pending[0][1]['tool'])
                    app.action_approval()
                    await pilot.pause(.02)
                    await pilot.click('#approve')
                await pilot.pause(.03)
            assert not app.bridge.busy, app.bridge.message
            for _ in range(30):
                await app.poll()
                if app.current_path and app.sources[app.current_path][1] == app.current_path.stat().st_size:
                    break
                await pilot.pause(.02)
            assert app.current_path
            record = json.loads(app.current_path.with_name('result.json').read_text(encoding='utf8'))
            assert record['verdict'] == 'PASS', record.get('error')
            assert all(a.input_tokens is not None for a in app.state.agents.values())
            assert any('command_output' in entry for a in app.state.agents.values() for _, entry in a.entries)
            assert any(a.streamed_calls for a in app.state.agents.values())
            assert not app.bridge.approvals()
            assert 'run_commands' in approvals and 'run_tests' in approvals
            # Replay history while retaining a discoverable current run.
            saved = app.current_path
            await app.switch_view(saved)
            await app.action_current()
            assert app.view_path == saved
        print(json.dumps({'output': str(app.current_path.parent)}))
        return 0

    monkeypatch.setattr(scenario, 'execute', drive)
    try:
        await case.test_cli_nested_src_project_selects_tests_without_solution_file()
    finally:
        await case.asyncTearDown()
        case.doCleanups()

@pytest.mark.asyncio
@pytest.mark.parametrize('when', ['approval', 'command'])
async def test_tui_cancel_runtime_and_process_cleanup(tmp_path, monkeypatch, when):
    pytest.importorskip('textual')
    import sys

    import httpx

    from raac.llm.client import LLMClient
    from raac.llm.config import load_llm_config
    from raac.tui.app import MASApp

    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    (workspace / 'app.py').write_text('VALUE = 1\n')
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path / 'artifacts'))
    config = load_llm_config(scenario.PROJECT / 'configs/llm.yaml', model='mock', base_url='http://mock.invalid/v1')
    script = "from pathlib import Path; import time; Path('begun').write_text('yes'); print('started',flush=True); time.sleep(20); Path('finished').write_text('yes')"

    def handler(request):
        system = json.loads(request.content)['messages'][0]['content'].replace('**', '')
        calls = None
        if system.startswith('You are the Analyst'):
            output = scenario.commit_payload({'summary': 'Inspect runtime', 'constraints': [],
                'acceptance_criteria': [{'id': 'AC1', 'description': 'Inspect runtime'}]})
        elif system.startswith('You are the Planner'):
            output = scenario.commit_payload({'steps': [{'id': 'S1', 'ac_ids': ['AC1'], 'target': 'app.py',
                'approach': 'Inspect runtime'}], 'covered_ac_ids': ['AC1']})
        else:
            output = None
            calls = [{'id': 'inspect', 'type': 'function', 'function': {
                'name': 'run_command', 'arguments': json.dumps({'command': [sys.executable, '-c', script]})}}]
        assert json.loads(request.content)['stream'] is True
        return sse_response({'model': 'mock', 'choices': [{'finish_reason': 'tool_calls' if calls else 'stop',
            'message': {'role': 'assistant', 'content': json.dumps(output) if output else None,
                        **({'tool_calls': calls} if calls else {})}}],
            'usage': {'prompt_tokens': 10, 'completion_tokens': 5}})

    monkeypatch.setattr('raac.mas_cli.load_llm_config', lambda *a, **kw: config)
    monkeypatch.setattr('raac.mas_cli.LLMClient', lambda config, *, log_path: LLMClient(config, log_path=log_path, transport=httpx.MockTransport(handler)))
    app = MASApp(workspace=workspace, prompt='Inspect runtime')
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.press('ctrl+enter')
        deadline = time.monotonic() + 15
        while not app.bridge.approvals() and time.monotonic() < deadline:
            await pilot.pause(.02)
        assert app.bridge.approvals()
        assert not (workspace / 'begun').exists()
        if when == 'command':
            app.action_approval()
            await pilot.pause(.02)
            await pilot.click('#approve')
            while not (workspace / 'begun').exists() and time.monotonic() < deadline:
                await pilot.pause(.02)
            assert (workspace / 'begun').exists()
        await pilot.press('ctrl+c')
        await pilot.pause(.02)
        await pilot.click('#yes')
        while app.bridge.busy and time.monotonic() < deadline:
            await pilot.pause(.02)
        assert not app.bridge.busy
        assert not app.bridge.approvals()
        assert not (workspace / 'finished').exists()
        assert 'cancelled' in app.bridge.message
        await app.poll()
        trace = [json.loads(line) for line in app.current_path.read_text(encoding='utf8').splitlines()]
        assert trace[-1]['event'] == 'run_cancelled'
