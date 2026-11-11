"""A role's previous turn: kept in full on disk, handed to its next turn within a token budget."""

import json
from types import SimpleNamespace

from raac.common.tracing import JSONLTrace
from raac.harness.layer1_lifecycle.agent import AgentRunner
from raac.harness.layer2_core.memory.short_term import ShortTermMemoryPolicy


def runner(tmp_path, *, budget=15000, content=True):
    agent = object.__new__(AgentRunner)
    agent.memory_policy = ShortTermMemoryPolicy(previous_attempt_tokens=budget)
    agent._last_transcripts = {}
    agent.client = SimpleNamespace(trace=JSONLTrace(tmp_path / "llm.jsonl"), trace_content=content,
                                   log_path=tmp_path / "llm.jsonl")
    return agent


def state(messages, *, role="Coder", round_number=1):
    return {"context": {"role": role, "agent_call_id": "abcdef0123456789"},
            "request": {"run_id": "chain-1", "work_id": "task-1", "round": round_number},
            "status": "COMPLETED", "turn": 3, "messages": messages}


def conversation(n, size=40):
    head = [{"role": "system", "content": "rules"}, {"role": "user", "content": "task"}]
    return head + [{"role": "assistant" if i % 2 else "tool", "content": f"{i}:" + "x" * size} for i in range(n)]


def test_full_turn_is_written_to_disk_and_the_next_turn_gets_the_newest_messages(tmp_path):
    agent = runner(tmp_path)
    agent._keep_transcript(state(conversation(6)))
    [saved] = (tmp_path / "memory").glob("coder-r1-*.transcript.json")
    on_disk = json.loads(saved.read_text(encoding="utf-8"))
    assert len(on_disk["messages"]) == 8 and on_disk["round"] == 1  # full, including system and task
    previous = agent._previous_attempt({"run_id": "chain-1", "work_id": "task-1"}, "Coder")
    assert previous["omitted_earlier_messages"] == 0
    assert [m["content"][:2] for m in previous["messages"]] == ["0:", "1:", "2:", "3:", "4:", "5:"]


def test_budget_keeps_the_newest_messages_and_counts_the_rest(tmp_path):
    agent = runner(tmp_path, budget=60)  # each message is ~25 estimated tokens
    agent._keep_transcript(state(conversation(6, size=60)))
    previous = agent._previous_attempt({"run_id": "chain-1", "work_id": "task-1"}, "Coder")
    assert [m["content"][:2] for m in previous["messages"]] == ["4:", "5:"]
    assert previous["omitted_earlier_messages"] == 4 and previous["estimated_tokens"] <= 60


def test_other_roles_runs_and_a_zero_budget_get_nothing(tmp_path):
    agent = runner(tmp_path, budget=0)
    agent._keep_transcript(state(conversation(2)))
    assert agent._previous_attempt({"run_id": "chain-1", "work_id": "task-1"}, "Coder") is None
    agent = runner(tmp_path)
    agent._keep_transcript(state(conversation(2)))
    assert agent._previous_attempt({"run_id": "chain-1", "work_id": "task-1"}, "Tester") is None
    assert agent._previous_attempt({"run_id": "chain-2", "work_id": "task-1"}, "Coder") is None


def test_without_content_tracing_nothing_is_written_but_the_next_turn_still_gets_it(tmp_path):
    agent = runner(tmp_path, content=False)
    agent._keep_transcript(state(conversation(2)))
    assert not (tmp_path / "memory").exists()
    assert agent._previous_attempt({"run_id": "chain-1", "work_id": "task-1"}, "Coder")["messages"]
