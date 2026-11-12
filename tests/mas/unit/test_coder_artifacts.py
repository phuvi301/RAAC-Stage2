"""Derived diff errors retain diagnostics without trusting model reports."""
import json

import pytest
from test_lifecycle_loop import make_runner, request

from raac.common.errors import AgentExecutionError


@pytest.mark.asyncio
@pytest.mark.parametrize('stage', ['workspace_diff', 'diff_validation'])
async def test_coder_patch_failure_preserves_cause_and_trace(tmp_path, monkeypatch, stage):
    runner, client, _ = make_runner(tmp_path, [{'content': json.dumps({'summary': 'No edits'})}])
    def broken_patch():
        if stage == 'workspace_diff':
            raise ValueError('Cannot read workspace')
        return 'invalid diff'
    monkeypatch.setattr(runner.tools.workspace, 'current_patch', broken_patch)
    try:
        with pytest.raises(AgentExecutionError) as caught:
            await runner('Coder', request())
        assert caught.value.code == 'patch_generation_failed'
        assert isinstance(caught.value.__cause__, ValueError)
        events = [json.loads(line) for line in client.log_path.read_text(encoding='utf-8').splitlines()]
        event = next(e for e in events if e['event'] == 'coder_patch_failed')
        assert event['stage'] == stage
        assert event['error_kind'] == type(caught.value.__cause__).__name__
        assert event['detail'] == str(caught.value.__cause__)
    finally:
        await client.aclose()

@pytest.mark.parametrize('wire', [{'summary': 'No changes required'}, '{"summary": "No changes required"}'])
def test_coder_report_accepts_json_object_and_wire_text(wire):
    from raac.common.agent_contracts import parse_role_output
    assert parse_role_output('Coder', wire).summary == 'No changes required'


@pytest.mark.asyncio
async def test_coder_noop_has_empty_derived_patch(tmp_path):
    runner, client, _ = make_runner(tmp_path, [{'content': '{"summary": "No changes required"}'}])
    try:
        handoff = await runner('Coder', request())
        assert handoff.output == ''
        assert handoff.runtime['repository_changes'] == []
    finally:
        await client.aclose()
