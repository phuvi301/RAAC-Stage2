"""Literal display, bounded activation state and paged JSONL reading."""
import json
import re
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

from raac.common.tracing import JSONLTrace

# Strip whole terminal strings first, then remaining controls (including C1).
TERMINAL = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[P^_].*?\x1b\\|\x1b\[[0-?]*[ -/]*[@-~]|\x1b.|[\x00-\x08\x0b-\x1f\x7f-\x9f]", re.DOTALL)


def literal(value):
    return TERMINAL.sub('', str(value))


def display_event(event):
    return literal(json.dumps(event, ensure_ascii=False, indent=2))


@dataclass
class AgentState:
    key: tuple
    role: str
    parent: str | None = None
    status: str = 'observed'
    phase: str = '—'
    started: str = ''
    ended: str = ''
    input_tokens: int | None = None
    output_tokens: int | None = None
    usage_missing: bool = False
    entries: deque = field(default_factory=lambda: deque(maxlen=1000))
    version: int = 0
    rewrite: int = 0
    streamed_calls: deque = field(default_factory=lambda: deque(maxlen=32))
    waiting_since: str = ''
    public_summary: str = ''


class RunState:
    def __init__(self):
        self.agents = {}
        self.sequences = {}
        self.run_id = ''
        self.status = 'Ready'
        self.workspace = ''
        self.topology = 'chain'
        self.level = ''

    def apply(self, event):
        if not isinstance(event, dict) or not isinstance(event.get('event'), str):
            return None
        run = event.get('run_id')
        if not isinstance(run, str):
            return None
        sequence = event.get('sequence')
        if type(sequence) is int:
            if sequence <= self.sequences.get(run, 0):
                return None
            self.sequences[run] = sequence
        self.run_id = run
        self.level = str(event.get('level') or self.level)
        kind = event['event']
        if kind.startswith('run_'):
            self.status = str(event.get('status') or kind.removeprefix('run_'))
            decision = event.get('decision')
            if isinstance(decision, dict):
                self.topology = str(decision.get('topology', self.topology))
            workspace = event.get('workspace')
            if isinstance(workspace, dict):
                self.workspace = str(workspace.get('repo_dir', ''))
            if kind in {'run_completed', 'run_failed', 'run_cancelled'}:
                for agent in self.agents.values():
                    agent.ended = agent.ended or str(event.get('timestamp', ''))
                    if agent.status not in {'COMPLETED', 'FAILED', 'cancelled', 'MOCK', 'SKIPPED'}:
                        agent.status = 'cancelled' if kind == 'run_cancelled' else 'ended'
                        agent.ended = str(event.get('timestamp', ''))
                        agent.version += 1
        activation = event.get('agent_call_id') or event.get('agent_id')
        if not isinstance(activation, str):
            return None
        key = (run, activation)
        if key not in self.agents:
            self.agents[key] = AgentState(key, literal(event.get('role') or event.get('agent_id') or activation),
                                          event.get('parent_agent_call_id') or event.get('parent_agent_id'), started=str(event.get('timestamp', '')))
        agent = self.agents[key]
        if kind == 'lifecycle_transition' and isinstance(event.get('to_state'), str):
            agent.status = event['to_state']
            if agent.status in {'COMPLETED', 'FAILED'}:
                agent.ended = str(event.get('timestamp', ''))
        elif kind == 'lifecycle_paused':
            agent.status = 'WAITING_HUMAN_APPROVAL'
        elif kind == 'agent_failed':
            agent.status = 'FAILED'
            agent.ended = str(event.get('timestamp', ''))
        elif kind in {'mock_output', 'agent_skipped'}:
            agent.status = 'MOCK' if kind == 'mock_output' else 'SKIPPED'
            agent.ended = str(event.get('timestamp', ''))
        agent.phase = literal(event.get('tool_name') or event.get('phase') or kind)
        call = event.get('llm_call_id')
        if kind == 'llm_attempt_started':
            agent.waiting_since = str(event.get('timestamp', ''))
        elif kind in {'llm_call', 'llm_stream_ended', 'agent_failed'}:
            agent.waiting_since = ''
        if kind == 'llm_public_summary':
            agent.public_summary = literal(event.get('text', 'Summary unavailable'))[:65536]
            agent.version += 1
            return key
        if kind in {'llm_output_delta', 'llm_tool_call_delta'}:
            agent.waiting_since = ''
            tag = f"{'Output' if kind == 'llm_output_delta' else 'Tool call'} [{call}]\n"
            delta = literal(event.get('text', ''))
            for index, (seq, entry) in enumerate(agent.entries):
                if entry.startswith(tag):
                    agent.entries[index] = (seq, tag + (entry[len(tag):] + delta)[-65536:])
                    agent.rewrite += 1
                    break
            else:
                agent.entries.append((agent.version + 1, tag + delta[-65536:]))
                if kind == 'llm_output_delta' and call not in agent.streamed_calls:
                    agent.streamed_calls.append(call)
            agent.version += 1
            return key
        if kind == 'llm_response' and call in agent.streamed_calls:
            # Final snapshot stays in JSONL for inspection; never echo streamed output.
            agent.version += 1
            return key
        if kind == 'llm_call':
            usage = event.get('usage')
            if isinstance(usage, dict) and all(type(usage.get(k)) is int and usage[k] >= 0 for k in ('prompt_tokens', 'completion_tokens')):
                agent.input_tokens = (agent.input_tokens or 0) + usage['prompt_tokens']
                agent.output_tokens = (agent.output_tokens or 0) + usage['completion_tokens']
            else:
                agent.usage_missing = True
        # Full payloads remain in JSONL and are loaded by detail pages only.
        message = event.get('message')
        output = message.get('content') if isinstance(message, dict) else None
        summary = event.get('text') or event.get('summary') or output or event.get('error') or event.get('reason') or event.get('tool_name') or ''
        agent.entries.append((agent.version + 1, literal(f"{str(event.get('timestamp', ''))[11:19]} {kind} {summary}")[:2048]))
        agent.version += 1
        return key


def read_page(path, offset=0, limit=200, agent=None):
    """Byte cursor stops before an incomplete append; malformed lines are skipped."""
    records = []
    sanitizer = redactor()
    with Path(path).open('rb') as stream:
        stream.seek(offset)
        for _ in range(limit):
            if stream.tell() - offset >= 4 * 1024 * 1024:
                break
            start = stream.tell()
            line = stream.readline()
            if not line or not line.endswith(b'\n'):
                stream.seek(start)
                break
            try:
                event = sanitizer.redact(json.loads(line))
            except (ValueError, UnicodeError, RecursionError):
                continue
            if isinstance(event, dict) and (agent is None or (event.get('run_id'), event.get('agent_call_id') or event.get('agent_id')) == agent):
                records.append(event)
        return records, stream.tell()


def history(roots, page=0, size=20):
    paths = {p.resolve() for root in roots if Path(root).is_dir() for p in Path(root).glob('*/llm.jsonl')}
    paths = sorted(paths, key=lambda p: p.stat().st_mtime, reverse=True)
    return paths[page * size:(page + 1) * size], len(paths)


def redactor():
    # Environment is loaded by run_local before an approval can be requested.
    import os
    secrets = [v for k, v in os.environ.items() if any(word in k.upper() for word in ('KEY', 'TOKEN', 'SECRET', 'PASSWORD')) and v]
    return JSONLTrace(Path('unused'), secrets=secrets)
