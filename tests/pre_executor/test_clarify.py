"""Flow User -> clarify của raac.pre_executor: answers.md, các vòng hỏi, luật, duyệt, CLI và chạy qua AgentRunner thật."""

import asyncio
import json
import shutil
from pathlib import Path

import httpx
import pytest

from raac.pre_executor import cli, gate
from raac.pre_executor.artifacts import Artifacts
from raac.pre_executor.config import PreExecutorConfig
from raac.pre_executor.flows import clarify
from raac.pre_executor.flows.base import FlowError
from raac.pre_executor.render import parse_answers, render_answers

ROOT = Path(__file__).resolve().parents[2]
CONFIG = PreExecutorConfig(max_flow_retries=1, max_clarify_rounds=2)


def question(qid, text, default="", why=""):
    return {"id": qid, "text": text, "default_answer": default, "why": why}


class FakeRuntime:
    """Thay AgentRunner: trả lần lượt các output đã định, ghi lại request nhận được."""

    def __init__(self, *outputs):
        self.outputs, self.requests = list(outputs), []
        self.run_id, self.task_id = "chain-fake", "task-fake"

    async def run(self, role_name, request):
        self.requests.append((role_name, request))
        return self.outputs.pop(0)

    async def aclose(self, *, status="completed"):
        self.closed = status


def workspace(tmp_path, request="Làm phần mềm bán hàng"):
    artifacts = Artifacts(tmp_path)
    artifacts.save_request(request)
    return artifacts


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------- answers.md

def test_answers_roundtrip_keeps_user_edits_and_multiline_answers():
    rounds = [{"summary": "Bán hàng online", "questions": [question("Q1", "Bán gì?", "quần áo", "đổi danh mục"),
                                                           question("Q2", "Thanh toán?", "VNPay")]}]
    text = render_answers(rounds, {})
    assert parse_answers(text) == {"Q1": "quần áo", "Q2": "VNPay"}
    edited = text.replace("A: VNPay", "A: MoMo\nvà chuyển khoản ngân hàng")
    assert parse_answers(edited)["Q2"] == "MoMo\nvà chuyển khoản ngân hàng"
    assert parse_answers(render_answers(rounds, {"Q1": "giày"}))["Q1"] == "giày"


# ---------------------------------------------------------------- flow

def test_rounds_are_numbered_across_rounds_and_keep_the_user_answers(tmp_path):
    artifacts = workspace(tmp_path)
    first = FakeRuntime({"complete": False, "summary": "s",
                         "questions": [question("a", "Bán gì?", "quần áo"), question("b", "Bao nhiêu khách?", "100")]})
    result = run(clarify.run(artifacts, first, CONFIG))
    assert result["status"] == "questions" and result["questions"] == 2
    role, request = first.requests[0]
    assert role == "Clarifier" and request["artifacts"]["request"] == "Làm phần mềm bán hàng"
    assert request["output_schema"]["title"] == "ClarifyOutput" and request["level"] == "PRE"
    assert "user_feedback" not in request["artifacts"]

    clarify.save_answers(artifacts, {"Q1": "giày dép"})
    second = FakeRuntime({"complete": False, "questions": [question("a", "Cổng thanh toán?", "VNPay")]})
    run(clarify.run(artifacts, second, CONFIG))
    sent = second.requests[0][1]["artifacts"]["answers"]
    assert sent[0] == {"id": "Q1", "question": "Bán gì?", "answer": "giày dép", "assumed": False}
    assert sent[1]["assumed"] is True
    assert parse_answers(artifacts.read_text("answers.md")) == {"Q1": "giày dép", "Q2": "100", "Q3": "VNPay"}


def test_a_repeated_question_is_sent_back_with_the_violation(tmp_path):
    artifacts = workspace(tmp_path)
    run(clarify.run(artifacts, FakeRuntime({"complete": False, "questions": [question("a", "Bán gì?")]}), CONFIG))
    runtime = FakeRuntime({"complete": False, "questions": [question("a", "Bán   GÌ ?")]},
                          {"complete": False, "questions": [question("a", "Giao hàng?")]})
    result = run(clarify.run(artifacts, runtime, CONFIG))
    assert runtime.requests[1][1]["artifacts"]["rule_violations"] == ["question already asked: Bán   GÌ ?"]
    assert result["status"] == "questions"


def test_rules_still_broken_after_the_retries_raise_without_writing(tmp_path):
    artifacts = workspace(tmp_path)
    run(clarify.run(artifacts, FakeRuntime({"complete": False, "questions": [question("a", "Bán gì?")]}), CONFIG))
    same = {"complete": False, "questions": [question("a", "Bán gì?")]}
    with pytest.raises(FlowError):
        run(clarify.run(artifacts, FakeRuntime(same, same), CONFIG))
    assert len(clarify.rounds(artifacts)) == 1


def test_wrong_schema_is_reported(tmp_path):
    with pytest.raises(FlowError):
        run(clarify.run(workspace(tmp_path), FakeRuntime({"complete": False, "questions": []}), CONFIG))


def test_clarify_has_no_feedback(tmp_path):
    with pytest.raises(ValueError):
        run(clarify.run(workspace(tmp_path), FakeRuntime(), CONFIG, note="hỏi thêm"))


# ---------------------------------------------------------------- tự chốt

def test_enough_information_at_once_finishes_without_questions(tmp_path):
    artifacts = workspace(tmp_path)
    result = run(clarify.run(artifacts, FakeRuntime({"complete": True, "summary": "đủ", "questions": []}), CONFIG))
    assert result["status"] == "done" and result["answered"] == 0
    assert artifacts.read_json("clarification.json") == [] and gate.state(artifacts, "clarify") == "approved"
    assert "Không cần hỏi thêm" in artifacts.read_text("answers.md")


def test_finishes_after_the_last_answers_when_the_agent_said_enough(tmp_path):
    artifacts = workspace(tmp_path)
    run(clarify.run(artifacts, FakeRuntime({"complete": True, "questions": [
        question("a", "Bán gì?", "quần áo"), question("b", "Thanh toán?", "VNPay")]}), CONFIG))
    assert gate.state(artifacts, "clarify") == "draft"  # còn câu hỏi chưa trả lời
    with pytest.raises(gate.GateError):
        gate.require_runnable(artifacts, "requirements")

    clarify.save_answers(artifacts, {"Q2": "MoMo"})
    idle = FakeRuntime()
    result = run(clarify.run(artifacts, idle, CONFIG))
    assert idle.requests == [] and result["status"] == "done" and result["assumed"] == 1
    items = artifacts.read_json("clarification.json")
    assert [(item["answer"], item["assumed"]) for item in items] == [("quần áo", True), ("MoMo", False)]
    assert gate.can_run(artifacts, "requirements")

    clarify.save_answers(artifacts, {"Q2": "ZaloPay"})  # sửa sau khi chốt -> chạy lại để chốt lại
    assert gate.state(artifacts, "clarify") == "stale" and not gate.can_run(artifacts, "requirements")
    run(clarify.run(artifacts, FakeRuntime(), CONFIG))
    assert artifacts.read_json("clarification.json")[1]["answer"] == "ZaloPay" and gate.can_run(artifacts, "requirements")


def test_finishes_at_the_round_limit(tmp_path):
    artifacts = workspace(tmp_path)
    for text in ("Bán gì?", "Thanh toán?"):
        run(clarify.run(artifacts, FakeRuntime({"complete": False, "questions": [question("a", text)]}), CONFIG))
    result = run(clarify.run(artifacts, FakeRuntime(), CONFIG))
    assert result["status"] == "done" and result["reason"] == "đã hết 2 vòng"


# ---------------------------------------------------------------- CLI

def opener(*outputs):
    runtime = FakeRuntime(*outputs)

    async def open_runtime(workspace, request_text, flow, model=None):
        runtime.opened = (Path(workspace), request_text, flow)
        runtime.model = model
        return runtime
    return runtime, open_runtime


def test_cli_takes_the_request_like_the_tui_and_drives_clarify(tmp_path, capsys):
    request = tmp_path / "request.md"
    request.write_text("Làm phần mềm bán hàng", encoding="utf-8")
    repo = tmp_path / "shop"
    repo.mkdir()
    runtime, open_runtime = opener({"complete": True, "questions": [question("a", "Bán gì?", "quần áo")]})
    assert cli.main(["clarify", "--workspace", str(repo), "--prompt-file", str(request), "--model", "m-1"],
                    open_runtime=open_runtime) == 0
    assert runtime.opened == (repo.resolve(), "Làm phần mềm bán hàng", "clarify") and runtime.model == "m-1"
    assert runtime.closed == "completed"
    assert (repo / ".raac/pre_executor/answers.md").is_file() and (repo / ".raac/.gitignore").read_text() == "*\n"

    assert cli.main(["clarify", "--workspace", str(repo), "--prompt", "Việc khác"], open_runtime=open_runtime) == 2
    assert cli.main(["requirements", "--workspace", str(repo)]) == 2  # chưa triển khai / clarify chưa xong
    for command in (["approve", "clarify"], ["review", "clarify"], ["revise", "clarify", "--note", "x"]):
        assert cli.main([*command, "--workspace", str(repo)]) == 2  # clarify không có góp ý/approve

    assert cli.main(["clarify", "--workspace", str(repo)], open_runtime=open_runtime) == 0  # tự chốt
    capsys.readouterr()
    assert cli.main(["status", "--workspace", str(repo)]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].split()[:2] == ["clarify", "approved"] and lines[1].split()[:2] == ["requirements", "missing"]
    assert "chưa triển khai" not in lines[2] and "chưa triển khai" in lines[3]  # profile đã có, tech_select chưa


def test_cli_without_any_request_is_refused(tmp_path):
    assert cli.main(["clarify", "--workspace", str(tmp_path)]) == 2


# ---------------------------------------------------------------- hạ tầng MAS thật

def test_clarify_runs_through_the_real_agent_runner_and_writes_a_viewable_log(tmp_path):
    from raac.llm.config import LLMConfig
    from raac.pre_executor.runtime import open_runtime

    registry = tmp_path / "registry"
    shutil.copytree(ROOT / "registry", registry)
    # Working tree hiện có thể thiếu prompt của architect (không liên quan tới test này): bản sao luôn nạp được.
    if not (registry / "prompts/architect.md").is_file():
        (registry / "prompts/architect.md").write_text("Architect placeholder for this test.", encoding="utf-8")
    seen = []
    reply = {"complete": False, "summary": "Cửa hàng online",
             "questions": [question("q1", "Bán mặt hàng gì?", "quần áo", "quyết định danh mục")]}

    def respond(http_request):
        seen.append(json.loads(http_request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(reply, ensure_ascii=False)}}],
                                         "usage": {"prompt_tokens": 50, "completion_tokens": 20}})

    repo = tmp_path / "shop"
    repo.mkdir()
    artifacts = workspace(repo)

    async def scenario():
        runtime = await open_runtime(repo, artifacts.request(), "clarify", stream=False, registry_dir=registry,
                                     results_dir=tmp_path / "results", llm_config=LLMConfig(model="fixture"),
                                     transport=httpx.MockTransport(respond))
        try:
            result = await clarify.run(artifacts, runtime, CONFIG)
        finally:
            await runtime.aclose()
        return runtime, result

    runtime, result = run(scenario())
    assert result["questions"] == 1 and parse_answers(artifacts.read_text("answers.md")) == {"Q1": "quần áo"}
    system = seen[0]["messages"][0]["content"]
    assert "Clarification Manager" in system and "Làm phần mềm bán hàng" in json.dumps(seen[0], ensure_ascii=False)
    events = [json.loads(line)["event"] for line in runtime.log_path.read_text(encoding="utf-8").splitlines()]
    assert events[0] == "run_started" and "llm_request" in events and events[-1] == "run_completed"
