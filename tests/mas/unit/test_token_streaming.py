"""Streaming uses the same lifecycle gates; HTTP mocks never call a paid model."""
import asyncio
import json
from pathlib import Path

import httpx
import pytest
from test_lifecycle_loop import FINAL, make_runner, request, tool
from test_model_profiles import call_context

from raac.common.errors import RunGuardError
from raac.common.tracing import JSONLTrace
from raac.llm.client import LLMClient, LLMError
from raac.llm.config import LLMConfig
from raac.llm.streaming import PublicText
from raac.tui.state import RunState

USAGE = {'prompt_tokens': 10, 'completion_tokens': 5}


def event(delta=None, finish=None, **extra):
    return {'choices': [{'index': 0, 'delta': delta or {}, 'finish_reason': finish}], **extra}


def wire(*events, done=True):
    return (': heartbeat\r\n\r\n' + ''.join('data: ' + json.dumps(e, ensure_ascii=False) + '\r\n\r\n' for e in events)
            + ('data: [DONE]\r\n\r\n' if done else '')).encode('utf8')


def sse_response(data):
    message = data['choices'][0]['message']
    delta = {**message}
    if 'tool_calls' in delta:
        delta['tool_calls'] = [{'index': i, **call} for i, call in enumerate(delta['tool_calls'])]
    return httpx.Response(200, headers={'content-type': 'text/event-stream'}, content=wire(
        None, event(delta, model=data.get('model', 'mock')),
        event(finish=data['choices'][0]['finish_reason'], usage=data['usage']), done=False))


class Bytes(httpx.AsyncByteStream):
    def __init__(self, body, *, error=None, pause=None):
        self.body, self.error, self.pause = body, error, pause
        self.closed = False

    async def __aiter__(self):
        for byte in self.body:
            yield bytes([byte])
        if self.pause:
            await self.pause.wait()
        if self.error:
            raise self.error

    async def aclose(self):
        self.closed = True


def client_for(tmp_path, body, *, trace=True, error=None, pause=None):
    stream = Bytes(body, error=error, pause=pause)
    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, stream=stream)

    client = LLMClient(LLMConfig(model='fixture'), log_path=tmp_path / 'llm.jsonl',
                       stream=True, trace_content=trace, transport=httpx.MockTransport(respond))
    return client, stream, requests


@pytest.mark.asyncio
async def test_utf8_sse_content_usage_and_no_reasoning(tmp_path):
    body = wire(event({'content': 'Xin chào ', 'reasoning_content': 'HIDDEN', 'reasoning': 'HIDDEN',
                       'reasoning_summary': 'UNVERIFIED'}), event({'content': 'bạn'}, 'stop'),
                {'choices': [], 'usage': USAGE})
    client, stream, requests = client_for(tmp_path, body)
    seen = []
    client.trace.subscriber = seen.append
    try:
        result = await client.complete([], context=call_context())
        assert result.message == {'role': 'assistant', 'content': 'Xin chào bạn'}
        assert result.model == 'fixture'
        assert result.usage == USAGE and stream.closed
        assert requests[0]['stream'] is True and requests[0]['stream_options'] == {'include_usage': True}
        assert ''.join(e['text'] for e in seen if e['event'] == 'llm_output_delta') == 'Xin chào bạn'
        assert any(e['event'] == 'llm_public_summary' and e['supported'] is False for e in seen)
        assert 'HIDDEN' not in client.log_path.read_text() and 'UNVERIFIED' not in str(seen)
        deltas = [e for e in seen if e['event'] == 'llm_output_delta']
        stream_events = [e for e in seen if e['event'] in {'llm_stream_started', 'llm_public_summary', 'llm_output_delta', 'llm_stream_ended'}]
        assert [e['chunk'] for e in stream_events] == list(range(1, len(stream_events) + 1))
        assert all(e['attempt'] == 1 and e['llm_call_id'] == result.call_id for e in deltas)
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_fragmented_tools_and_empty_content(tmp_path):
    body = wire(event({'content': None, 'tool_calls': [{'index': 0, 'id': 'call-', 'type': 'function',
        'function': {'name': 'read_', 'arguments': '{"path":'}}]}),
        event({'tool_calls': [{'index': 0, 'id': '1', 'function': {'name': 'file', 'arguments': '"value.py"}'}}]}, 'tool_calls'),
        {'choices': [], 'usage': USAGE})
    client, _, _ = client_for(tmp_path, body)
    try:
        result = await client.complete([], context=call_context(), tools=[{'type': 'function', 'function': {
            'name': 'read_file', 'parameters': {'type': 'object'}}}])
        call = result.message['tool_calls'][0]
        assert call['id'] == 'call-1' and call['function']['name'] == 'read_file'
        assert json.loads(call['function']['arguments']) == {'path': 'value.py'}
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_9router_codex_inline_usage_and_repeated_tool_id(tmp_path):
    # 9router 0.5.112: Responses->Chat mapper (4845.v), tool ID normalization (9024.RW).
    body = wire(event({'tool_calls': [{'index': 0, 'id': 'call_fixture', 'type': 'function',
        'function': {'name': 'read_file', 'arguments': ''}}]}),
        event({'tool_calls': [{'index': 0, 'id': 'call_fixture', 'function': {'arguments': '{"path":'}}]}),
        event({'tool_calls': [{'index': 0, 'id': 'call_fixture', 'function': {'arguments': '"value.py"}'}}]}),
        event(finish='tool_calls', usage=USAGE))
    client, stream, requests = client_for(tmp_path, body)
    try:
        result = await client.complete([], context=call_context(), tools=[{'type': 'function', 'function': {'name': 'read_file'}}])
        assert result.message['tool_calls'][0]['id'] == 'call_fixture'
        assert json.loads(result.message['tool_calls'][0]['function']['arguments']) == {'path': 'value.py'}
        assert result.usage == USAGE and stream.closed and len(requests) == 1
        assert client.usage.snapshot(run_id='profiles-run')['total_tokens'] == 15
    finally:
        await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize('filename', ['9router-0.5.112-codex.sse', '9router-0.5.112-codex-transform.sse'])
async def test_installed_9router_generated_sse_fixture(tmp_path, filename):
    body = (Path(__file__).parent / 'fixtures' / filename).read_bytes()
    client, _, _ = client_for(tmp_path, body)
    try:
        result = await client.complete([], context=call_context(), tools=[{'type': 'function', 'function': {'name': 'read_file'}}])
        call = result.message['tool_calls'][0]
        assert call['id'] == 'call_fixture' and json.loads(call['function']['arguments']) == {'path': 'value.py'}
        assert result.usage == {**USAGE, 'total_tokens': 15}
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_9router_terminal_content_usage_and_null_noop(tmp_path):
    # 9router 0.5.112: cached JSON->SSE converter (8895), null serialization (9024.v8).
    client, _, _ = client_for(tmp_path, wire(None, event({'role': 'assistant', 'content': 'ok'}),
        event(finish='stop', usage=USAGE)))
    try:
        result = await client.complete([], context=call_context())
        assert result.message['content'] == 'ok' and result.usage == USAGE
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_nullable_optional_delta_fields(tmp_path):
    client, _, _ = client_for(tmp_path, wire(event({'role': None, 'content': 'ok', 'tool_calls': None}),
        event(finish='stop', usage=USAGE)))
    try:
        assert (await client.complete([], context=call_context())).message['content'] == 'ok'
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_sse_error_diagnostics_are_safe_and_specific(tmp_path):
    client, _, _ = client_for(tmp_path, wire({'error': {'message': 'PRIVATE CREDENTIAL', 'type': 'internal_error'}}))
    try:
        with pytest.raises(LLMError):
            await client.complete([], context=call_context())
        error = client.upstream_errors['profiles-run']
        assert error['response_error'] == 'upstream_stream_error'
        assert 'PRIVATE' not in client.log_path.read_text() and 'PRIVATE' not in str(error)
    finally:
        await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize('trailer', [False, True])
async def test_9router_clean_eof_after_finish_and_usage(tmp_path, trailer):
    events = ([event({'content': 'ok'}, 'stop'), {'choices': [], 'usage': USAGE}] if trailer else
              [event({'content': 'ok'}, 'stop', usage=USAGE)])
    client, stream, _ = client_for(tmp_path, wire(*events, done=False))
    try:
        result = await client.complete([], context=call_context())
        assert result.message['content'] == 'ok' and result.usage == USAGE and stream.closed
    finally:
        await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize('finish,usage', [(None, USAGE), ('stop', None),
    ('stop', {'prompt_tokens': 10}), ('stop', {'prompt_tokens': True, 'completion_tokens': 5}),
    ('stop', {'prompt_tokens': 10, 'completion_tokens': -1})])
async def test_clean_eof_cannot_bypass_terminal_usage_guard(tmp_path, finish, usage):
    client, _, requests = client_for(tmp_path, wire(event({'content': 'partial'}, finish, usage=usage), done=False))
    try:
        with pytest.raises(LLMError):
            await client.complete([], context=call_context())
        assert len(requests) == 1 and not client.usage.snapshot(run_id='profiles-run')['usage_complete']
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_terminal_tool_call_waits_for_clean_eof_before_execution(tmp_path, monkeypatch):
    runner, client, _ = make_runner(tmp_path, [])
    await client.aclose()
    gate, executed = asyncio.Event(), []
    first = Bytes(wire(event({'tool_calls': [{'index': 0, **tool()}]}, 'tool_calls', usage=USAGE), done=False), pause=gate)
    replies = [httpx.Response(200, stream=first),
               httpx.Response(200, content=wire(event({'content': json.dumps(FINAL)}, 'stop', usage=USAGE), done=False))]
    client.stream = True
    client._http = httpx.AsyncClient(base_url='http://fixture/', transport=httpx.MockTransport(lambda _: replies.pop(0)))
    execute = runner.tools.execute
    async def observed(*args, **kwargs):
        assert gate.is_set() and first.closed
        executed.append(True)
        return await execute(*args, **kwargs)
    monkeypatch.setattr(runner.tools, 'execute', observed)
    task = asyncio.create_task(runner('Analyst', request()))
    try:
        await asyncio.sleep(.02)
        assert not task.done() and not executed
        gate.set()
        assert (await task)['summary'] == FINAL['summary'] and len(executed) == 1
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['missing_usage', 'malformed', 'incomplete', 'disconnect', 'timeout'])
async def test_failed_stream_never_retries_or_spends_again(tmp_path, kind):
    body = wire(event({'content': 'public'}, 'stop'), *([] if kind == 'missing_usage' else [{'choices': [], 'usage': USAGE}]),
                done=kind not in {'incomplete', 'disconnect', 'timeout'})
    if kind == 'malformed':
        body = b'data: not-json\n\n'
    elif kind == 'incomplete':
        body = wire(event({'content': 'partial'}), done=False)
    error = httpx.ReadError('private body') if kind == 'disconnect' else httpx.ReadTimeout('private body') if kind == 'timeout' else None
    client, stream, requests = client_for(tmp_path, body, error=error)
    try:
        with pytest.raises((LLMError, RunGuardError)):
            await client.complete([], context=call_context())
        with pytest.raises(RunGuardError, match='usage_unavailable'):
            await client.complete([], context=call_context())
        assert len(requests) == 1 and stream.closed
        assert not client.usage.snapshot(run_id='profiles-run')['usage_complete']
        assert 'private body' not in client.log_path.read_text()
    finally:
        await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize('trace', [True, False])
async def test_live_delta_before_completion_and_cancel_closes_http(tmp_path, trace):
    gate = asyncio.Event()
    client, stream, requests = client_for(tmp_path, wire(event({'content': 'Hello world\n'}), done=False), trace=trace, pause=gate)
    seen = []
    client.trace.subscriber = seen.append
    task = asyncio.create_task(client.complete([], context=call_context()))
    try:
        for _ in range(100):
            await asyncio.sleep(.001)
            if any(e['event'] == 'llm_output_delta' for e in seen) or not trace and requests:
                break
        assert not task.done()
        if trace:
            assert any(e['event'] == 'llm_output_delta' for e in seen)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert stream.closed
        assert any(e['event'] == 'llm_stream_ended' and not e['completed'] for e in seen)
        if not trace:
            assert 'Hello' not in str(seen) and not any(e['event'] == 'llm_public_summary' for e in seen)
    finally:
        await client.aclose()


@pytest.mark.parametrize('text', ['api_key = "SENSITIVE-credential"\n', 'Bearer SENSITIVEtoken\n',
    'sk-SENSITIVEkey123\n', '-----BEGIN PRIVATE KEY-----\nSENSITIVE\n-----END PRIVATE KEY-----',
    'configured-SENSITIVE-secret', '\x1b]52;c;SENSITIVE\x07hello\x1b[31m'])
def test_redaction_and_terminal_sequences_across_every_character(tmp_path, text):
    redactor = PublicText(JSONLTrace(tmp_path / 'unused', secrets=('configured-SENSITIVE-secret',)))
    output = ''.join(redactor.feed(char) for char in text) + redactor.feed(final=True)
    assert 'SENSITIVE' not in output and '\x1b' not in output


@pytest.mark.asyncio
async def test_partial_tools_cannot_reach_orchestrator(tmp_path, monkeypatch):
    runner, client, _ = make_runner(tmp_path, [])
    await client.aclose()
    body = wire(event({'tool_calls': [{'index': 0, **tool()}]}), done=False)
    stream = Bytes(body)
    client.stream = True
    client._http = httpx.AsyncClient(base_url='http://fixture/', transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=stream)))
    executed = []
    async def execute(*args, **kwargs):
        executed.append(args)
        raise AssertionError('Partial stream reached tool')
    monkeypatch.setattr(runner.tools, 'execute', execute)
    try:
        with pytest.raises((LLMError, RunGuardError)):
            await runner('Analyst', request())
        assert not executed and stream.closed
    finally:
        await client.aclose()


def test_replay_merges_deltas_without_echoing_snapshot():
    state = RunState()
    base = {'run_id': 'run', 'agent_call_id': 'activation', 'llm_call_id': 'call', 'agent_id': 'coder'}
    rows = [{**base, 'sequence': i + 1, 'event': 'llm_output_delta', 'text': text} for i, text in enumerate(['Hello ', 'world'])]
    for row in rows + [{**base, 'sequence': 3, 'event': 'llm_response', 'message': {'content': 'Hello world'}}] + rows:
        state.apply(row)
    agent = state.agents[('run', 'activation')]
    assert len(agent.entries) == 1 and agent.entries[0][1].endswith('Hello world')
    assert agent.input_tokens is None and agent.output_tokens is None


@pytest.mark.asyncio
async def test_http_failure_before_stream_keeps_existing_retry_policy(tmp_path):
    requests = []
    def respond(request):
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(429)
        return httpx.Response(200, content=wire(event({'content': 'ok'}, 'stop'), {'choices': [], 'usage': USAGE}))
    client = LLMClient(LLMConfig(model='fixture', backoff_seconds=0), log_path=tmp_path / 'log', stream=True,
                       transport=httpx.MockTransport(respond))
    try:
        assert (await client.complete([], context=call_context())).message['content'] == 'ok'
        assert len(requests) == 2
    finally:
        await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize('body', [b'data: \xff\n\n', wire(event({'content': 'x'})),
    wire(event({'tool_calls': [{'index': 3, **tool()}]}, 'tool_calls'), {'choices': [], 'usage': USAGE}),
    wire(event({'content': 123}, 'stop')), wire(event({'content': 'x'}, 'stop'), event({'content': 'late'}))])
async def test_invalid_stream_envelopes(tmp_path, body):
    client, stream, requests = client_for(tmp_path, body)
    try:
        with pytest.raises(LLMError):
            await client.complete([], context=call_context())
        assert stream.closed and len(requests) == 1
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_local_api_defaults_non_streaming_and_accepts_opt_in(monkeypatch):
    from raac import mas_cli
    seen = []
    async def execute(args, **kwargs):
        seen.append(args.stream)
    monkeypatch.setattr(mas_cli, 'execute', execute)
    await mas_cli.run_local('.', 'test')
    await mas_cli.run_local('.', 'test', stream=True)
    assert seen == [False, True]


@pytest.mark.asyncio
async def test_default_client_keeps_nonstream_payload(tmp_path):
    requests = []
    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={'choices': [{'message': {'content': 'ok'}, 'finish_reason': 'stop'}], 'usage': USAGE})
    client = LLMClient(LLMConfig(model='fixture'), log_path=tmp_path / 'log', transport=httpx.MockTransport(respond))
    try:
        await client.complete([], context=call_context())
        assert requests[0]['stream'] is False and 'stream_options' not in requests[0]
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_stream_preserves_dsml_normalization(tmp_path):
    from test_dsml_protocol import TOOLS, dsml

    content = dsml()
    client, _, _ = client_for(tmp_path, wire(event({'content': content[:20]}),
        event({'content': content[20:]}, 'stop'), {'choices': [], 'usage': USAGE}))
    try:
        result = await client.complete([], context=call_context(), tools=TOOLS)
        call = result.message['tool_calls'][0]
        assert call['function']['name'] == 'read_file'
        assert json.loads(call['function']['arguments']) == {'path': 'value.py'}
        assert 'tool_protocol_normalized' in client.log_path.read_text()
    finally:
        await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize('trace', [True, False])
async def test_secrets_never_reach_journal_or_subscriber(tmp_path, trace):
    content = 'Before api_key="SENSITIVE-token" after'
    body = wire(*(event({'content': char}) for char in content), event(finish='stop'), {'choices': [], 'usage': USAGE})
    client, _, _ = client_for(tmp_path, body, trace=trace)
    seen = []
    client.trace.subscriber = seen.append
    try:
        result = await client.complete([], context=call_context())
        assert result.message['content'] == content
        assert 'SENSITIVE' not in str(seen) and 'SENSITIVE' not in client.log_path.read_text()
        if not trace:
            assert 'Before' not in str(seen)
            assert not any(e['event'] in {'llm_output_delta', 'llm_response', 'llm_public_summary'} for e in seen)
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_pilot_sees_live_output_preserves_scroll_and_replays_once(tmp_path):
    pytest.importorskip('textual')
    from textual.widgets import RichLog, Static

    from raac.tui.app import MASApp

    gate = asyncio.Event()
    first = wire(event({'content': 'Visible before completion\n' * 100}), done=False)
    last = wire(event({'content': 'Final line\n'}, 'stop'), {'choices': [], 'usage': USAGE})
    class Delayed(Bytes):
        async def __aiter__(self):
            yield first
            await gate.wait()
            yield last
    stream = Delayed(b'')
    path = tmp_path / 'llm.jsonl'
    client = LLMClient(LLMConfig(model='fixture'), log_path=path, stream=True, trace_content=True,
                       transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=stream)))
    app = MASApp(log=path)
    task = None
    try:
        async with app.run_test(size=(100, 30)) as pilot:
            task = asyncio.create_task(client.complete([], context=call_context()))
            for _ in range(100):
                await pilot.pause(.02)
                if ('profiles-run', 'analyst') in app.panels and app.state.agents[('profiles-run', 'analyst')].streamed_calls:
                    break
            key = ('profiles-run', 'analyst')
            assert not task.done()
            assert 'Visible before completion' in app.state.agents[key].entries[-1][1]
            panel = app.panels[key]
            app.select_agent(key)
            await pilot.pause()
            await pilot.press('pageup')
            assert not panel.follow
            position = panel.query_one(RichLog).scroll_y
            await pilot.resize_terminal(80, 24)
            gate.set()
            await task
            await pilot.pause(.1)
            await app.poll()
            assert not panel.follow and panel.query_one(RichLog).scroll_y == position
            assert app.state.agents[key].public_summary == 'Summary unavailable'
            assert panel.query_one('.public-summary', Static)
            entries = app.state.agents[key].entries
            assert sum('Visible before completion' in entry for _, entry in entries) == 1
            assert app.state.agents[key].output_tokens == 5
            await app.switch_view(path)
            assert sum('Visible before completion' in entry for _, entry in app.state.agents[key].entries) == 1
    finally:
        if task and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await client.aclose()
