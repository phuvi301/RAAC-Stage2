"""scripts/rerun_role.py dựng lại đầu vào của role và nạp lại lượt trước của chính role đó."""

import importlib.util
import json
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "rerun_role.py"
spec = importlib.util.spec_from_file_location("rerun_role", SCRIPT)
rerun = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rerun)


def write_run(tmp_path):
    run = tmp_path / "chain-old"
    run.mkdir()
    task = {"run_id": "chain-old", "work_id": "w", "round": 1, "incoming_message": {"from": "Planner"},
            "artifacts": {}, "repository_files": ["a.py"], "execution_environment": {}}
    rows = [{"event": "llm_request", "agent_id": "coder", "round": 1, "turn": turn,
             "request": {"messages": [{"role": "system", "content": "s"},
                                      {"role": "user", "content": json.dumps({**task, "turn": turn})}]}}
            for turn in (2, 1)]
    (run / "llm.jsonl").write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
    memory = run / "memory"
    memory.mkdir()
    (memory / "coder-r1-abc.transcript.json").write_text(json.dumps(
        {"version": 1, "run_id": "chain-old", "work_id": "w", "role": "Coder", "round": 1, "status": "FAILED",
         "turns": 3, "agent_call_id": "abc", "messages": []}), encoding="utf-8")
    return run


def test_request_is_rebuilt_from_the_first_turn_without_runner_added_keys(tmp_path):
    request = rerun.rebuild_request(write_run(tmp_path) / "llm.jsonl", "coder")
    assert request["turn"] == 1 and request["incoming_message"] == {"from": "Planner"}
    assert "repository_files" not in request and "execution_environment" not in request


def test_previous_attempt_is_copied_into_the_new_run_under_its_ids(tmp_path):
    old = write_run(tmp_path) / "llm.jsonl"
    new = tmp_path / "chain-new"
    assert rerun.seed_previous_attempt(old, new, "Coder", "chain-new", "w", 1)
    data = json.loads(next((new / "memory").glob("coder-r1-*.json")).read_text(encoding="utf-8"))
    assert data["run_id"] == "chain-new" and data["status"] == "FAILED"
    assert not rerun.seed_previous_attempt(old, tmp_path / "x", "Tester", "chain-new", "w", 1)
