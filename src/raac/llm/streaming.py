"""Strict Chat Completions SSE assembly; only public content leaves this module."""
import codecs
import json
import re


class StreamFormatError(ValueError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


class PublicText:
    """Hold ambiguous suffixes and credential-bearing text before publication."""

    def __init__(self, trace):
        self.trace = trace
        self.pending = ''

    def feed(self, text='', *, final=False):
        self.pending += text
        end = len(self.pending)
        if not final:
            # Once a credential marker appears, retain its value until completion.
            marker = re.search(r'-----BEGIN|\b(?:Bearer|sk-|api[_-]?key|authorization|password|passwd|access[_-]?token|refresh[_-]?token|client[_-]?secret)\b|\x1b|[\x80-\x9f]', self.pending, re.IGNORECASE)
            if marker:
                end = min(end, marker.start())
            markers = ('-----BEGIN', 'Bearer', 'sk-', 'api_key', 'api-key', 'apikey', 'authorization',
                       'password', 'passwd', 'access_token', 'access-token', 'accesstoken',
                       'refresh_token', 'refresh-token', 'refreshtoken', 'client_secret', 'client-secret', 'clientsecret')
            for token in (*self.trace.secrets, *markers):
                for length in range(1, min(len(token), len(self.pending)) + 1):
                    if self.pending[-length:].lower() == token[:length].lower():
                        end = min(end, len(self.pending) - length)
            for secret in self.trace.secrets:
                position = self.pending.find(secret)
                if position >= 0 and position < end < position + len(secret):
                    end = position
        value, self.pending = self.pending[:end], self.pending[end:]
        value = self.trace._text(value)
        # Strip complete terminal strings before remaining control characters.
        return re.sub(r'\x1b\][^\x07\x1b]*(?:\x07|\x1b\\|$)|\x1b[P^_].*?(?:\x1b\\|$)|\x1b\[[0-?]*[ -/]*[@-~]|\x1b.|[\x00-\x08\x0b-\x1f\x7f-\x9f]', '', value, flags=re.DOTALL)


async def sse_lines(response):
    decoder = codecs.getincrementaldecoder('utf-8')('strict')
    pending, size = '', 0
    try:
        async for chunk in response.aiter_bytes():
            size += len(chunk)
            if size > 16 * 1024 * 1024:
                raise StreamFormatError('stream_too_large')
            pending += decoder.decode(chunk)
            while match := re.search(r'\r\n|\r|\n', pending):
                if match.group() == '\r' and match.end() == len(pending):
                    break
                yield pending[:match.start()]
                pending = pending[match.end():]
        pending += decoder.decode(b'', final=True)
    except UnicodeError:
        raise StreamFormatError('invalid_stream_utf8') from None
    if pending.endswith('\r'):
        yield pending[:-1]
    elif pending:
        yield pending


async def assemble(response, publish):
    """Require a terminal completion; partial tool calls never become a Completion."""
    message = {'role': 'assistant', 'content': ''}
    calls, usage, model, finish = {}, None, None, None
    lines, done = [], False
    async for line in sse_lines(response):
        if line.startswith(':'):
            continue
        if line:
            if line.startswith('data:'):
                lines.append(line[5:].lstrip(' '))
            elif not line.startswith(('event:', 'id:', 'retry:')):
                raise StreamFormatError('invalid_sse_field')
            continue
        if not lines:
            continue
        raw, lines = '\n'.join(lines), []
        if raw == '[DONE]':
            done = True
            break
        try:
            data = json.loads(raw)
            # 9router's SSE serializer emits null for no-op translator results.
            if data is None:
                continue
            if not isinstance(data, dict):
                raise TypeError
            if data.get('error') is not None:
                raise StreamFormatError('upstream_stream_error')
            if data.get('model') is not None:
                if not isinstance(data['model'], str):
                    raise TypeError
                model = data['model']
            choices = data['choices']
            if not isinstance(choices, list) or len(choices) > 1:
                raise ValueError
            if not choices:
                if finish is None or data.get('usage') is None:
                    raise ValueError
                usage = data['usage']
                continue
            choice = choices[0]
            if choice.get('index', 0) != 0 or finish is not None:
                raise ValueError
            delta = choice['delta']
            if not isinstance(delta, dict):
                raise TypeError
            if delta.get('role') not in (None, 'assistant'):
                raise ValueError
            content = delta.get('content')
            if content is not None:
                if not isinstance(content, str):
                    raise ValueError
                message['content'] += content
                publish(content)
            parts = delta.get('tool_calls')
            for part in parts if parts is not None else []:
                index = part['index']
                if type(index) is not int or not 0 <= index < 128:
                    raise ValueError
                call = calls.setdefault(index, {'id': '', 'type': 'function', 'function': {'name': '', 'arguments': ''}})
                if part.get('type', 'function') != 'function':
                    raise ValueError
                if 'id' in part:
                    if not isinstance(part['id'], str):
                        raise TypeError
                    # 9router repeats the full ID on argument-only deltas.
                    if part['id'] != call['id']:
                        call['id'] += part['id']
                for key in ('name', 'arguments'):
                    value = part.get('function', {}).get(key, '')
                    if not isinstance(value, str):
                        raise TypeError
                    call['function'][key] += value
            finish = choice.get('finish_reason')
            if finish is not None and finish not in {'stop', 'tool_calls', 'length', 'content_filter'}:
                raise ValueError
            if finish is not None and data.get('usage') is not None:
                usage = data['usage']
        except StreamFormatError:
            raise
        except (ValueError, KeyError, TypeError, AttributeError):
            raise StreamFormatError('invalid_stream_chunk') from None
    # 9router's Codex transform consumes [DONE] but closes after finish + usage.
    terminal_usage = isinstance(usage, dict) and all(
        type(usage.get(name)) is int and usage[name] >= 0
        for name in ('prompt_tokens', 'completion_tokens'))
    if (not done and not terminal_usage) or finish is None or lines or sorted(calls) != list(range(len(calls))):
        raise StreamFormatError('incomplete_stream')
    if calls:
        message['tool_calls'] = [calls[index] for index in sorted(calls)]
    return {**({'model': model} if model is not None else {}), 'usage': usage,
            'choices': [{'message': message, 'finish_reason': finish}]}
