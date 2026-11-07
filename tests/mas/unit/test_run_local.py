import json
from dataclasses import replace

import pytest
from test_lifecycle_loop import FINAL, make_runner, request, tool

from raac.harness.layer1_lifecycle import ApprovalDecision
from raac.harness.layer1_lifecycle.checkpoint import LifecycleError
from raac.harness.layer3_execution.tools.workspace import WorkspaceTools
from raac.mas_cli import main


def test_direct_workspace(tmp_path):
    root = tmp_path / 'thư mục code'
    root.mkdir()
    ws = WorkspaceTools(root, None, requirement='Add a module')
    ws.edit_file(path='app.py', content='VALUE = 1\n', role='Coder')
    assert (root / 'app.py').read_text() == 'VALUE = 1\n'
    assert 'app.py' in ws.current_patch()
    with pytest.raises(ValueError):
        ws.read_file('../outside')
    with pytest.raises(ValueError):
        WorkspaceTools(root, None, requirement=' ')


@pytest.mark.parametrize('argv', [
    ['run-local'], ['run-local', '--workspace', '.', '--prompt', ' '],
    ['run-local', '--workspace', '.', '--prompt', 'a', '--prompt-file', 'b'],
])
def test_invalid_cli(argv):
    with pytest.raises(SystemExit) as exc:
        main(argv)
    assert exc.value.code == 2


@pytest.mark.asyncio
@pytest.mark.parametrize('decision', ['approve', 'deny', 'mismatch'])
async def test_inline_approval(tmp_path, decision):
    calls = []
    async def handler(pending):
        calls.append(pending)
        # Approval is requested before the operation is executed.
        assert runner.tools.workspace.read_file('value.py')['content'] == 'VALUE = 1\n'
        approved = ApprovalDecision(**{key: pending[key] for key in
            ('invocation_id', 'revision', 'operation_id', 'arguments_hash')}, approve=decision != 'deny')
        return replace(approved, arguments_hash='wrong') if decision == 'mismatch' else approved
    runner, client, seen = make_runner(tmp_path,
        [{'tool_calls': [tool()]}, {'content': json.dumps(FINAL)}],
        auto_approve=False, approval_handler=handler, approval_tools=frozenset({'read_file'}))
    try:
        if decision == 'approve':
            assert (await runner('Analyst', request()))['summary'] == FINAL['summary']
            assert len(seen) == 2
        else:
            with pytest.raises(LifecycleError, match='approval_rejected' if decision == 'deny' else 'approval_mismatch'):
                await runner('Analyst', request())
            assert len(seen) == 1
        assert len(calls) == 1
    finally:
        await client.aclose()

@pytest.mark.asyncio
async def test_terminal_eof_denies(monkeypatch):
    from raac.mas_cli import terminal_approval
    def eof(*args):
        raise EOFError
    monkeypatch.setattr('builtins.input', eof)
    decision = await terminal_approval({'invocation_id': 'i', 'revision': 1,
        'operation_id': 'o', 'arguments_hash': 'h', 'role': 'Coder', 'tool': 'run_tests'})
    assert decision.approve is False


@pytest.mark.asyncio
async def test_api_validates_before_loading_config(tmp_path, monkeypatch):
    from raac.mas_cli import DemoError, run_local
    def unexpected(*args, **kwargs):
        pytest.fail('Invalid input must not load model configuration')
    monkeypatch.setattr('raac.mas_cli.load_llm_config', unexpected)
    with pytest.raises(DemoError, match='nonempty'):
        await run_local(tmp_path, '')


@pytest.mark.asyncio
async def test_file_reads_do_not_require_process_approval(tmp_path):
    async def unexpected(pending):
        pytest.fail('Read-only file tools must not prompt')
    runner, client, _ = make_runner(tmp_path,
        [{'tool_calls': [tool()]}, {'content': json.dumps(FINAL)}], auto_approve=False,
        approval_handler=unexpected, approval_tools=frozenset({'run_command', 'run_tests'}))
    try:
        assert (await runner('Analyst', request()))['summary'] == FINAL['summary']
    finally:
        await client.aclose()
