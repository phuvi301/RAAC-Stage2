"""Flow User -> clarify của raac.pre_executor: answers.md, các vòng hỏi, luật, duyệt, CLI và chạy qua AgentRunner thật."""

import asyncio
import json
import shutil
from pathlib import Path

import httpx
import pytest

from raac.pre_executor import cli, gate
from raac.pre_executor.artifacts import Artifacts
from pe_helpers import with_coverage
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
        return with_coverage(self.outputs.pop(0))

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
    assert sent[0] == {"id": "Q1", "category": "functional_scope", "question": "Bán gì?", "answer": "giày dép", "assumed": False}
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
        gate.require_runnable(artifacts, "business_analyst")

    clarify.save_answers(artifacts, {"Q2": "MoMo"})
    idle = FakeRuntime()
    result = run(clarify.run(artifacts, idle, CONFIG))
    assert idle.requests == [] and result["status"] == "done" and result["assumed"] == 1
    items = artifacts.read_json("clarification.json")
    assert [(item["answer"], item["assumed"]) for item in items] == [("quần áo", True), ("MoMo", False)]
    assert gate.can_run(artifacts, "business_analyst")

    clarify.save_answers(artifacts, {"Q2": "ZaloPay"})  # sửa sau khi chốt -> chạy lại để chốt lại
    assert gate.state(artifacts, "clarify") == "stale" and not gate.can_run(artifacts, "business_analyst")
    run(clarify.run(artifacts, FakeRuntime(), CONFIG))
    assert artifacts.read_json("clarification.json")[1]["answer"] == "ZaloPay" and gate.can_run(artifacts, "business_analyst")


def test_finishes_at_the_round_limit(tmp_path):
    artifacts = workspace(tmp_path)
    for text in ("Bán gì?", "Thanh toán?"):
        run(clarify.run(artifacts, FakeRuntime({"complete": False, "questions": [question("a", text)]}), CONFIG))
    result = run(clarify.run(artifacts, FakeRuntime(), CONFIG))
    assert result["status"] == "done" and result["reason"] == "đã hết 2 vòng"


# ---------------------------------------------------------------- CLI

def opener(*outputs):
    runtime = FakeRuntime(*outputs)

    async def open_runtime(workspace, request_text, flow, model=None, **kwargs):
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
    assert cli.main(["business_analyst", "--workspace", str(repo)]) == 2  # clarify chưa xong
    for command in (["approve", "clarify"], ["review", "clarify"], ["revise", "clarify", "--note", "x"]):
        assert cli.main([*command, "--workspace", str(repo)]) == 2  # clarify không có góp ý/approve

    assert cli.main(["clarify", "--workspace", str(repo)], open_runtime=open_runtime) == 0  # tự chốt
    capsys.readouterr()
    assert cli.main(["status", "--workspace", str(repo)]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].split()[:2] == ["clarify", "approved"] and lines[1].split()[:2] == ["business_analyst", "missing"]
    assert [line.split()[0] for line in lines] == ["clarify", "business_analyst", "solution_architect",
                                                  "project_manager"]
    assert not any("chưa triển khai" in line for line in lines)  # cả 4 flow đã triển khai


# ---------------------------------------------------------------- workspace_mode, --auto-assume

def test_the_payload_tells_the_clarifier_whether_the_workspace_has_code(tmp_path):
    empty = tmp_path / "new"
    empty.mkdir()
    runtime = FakeRuntime({"complete": True, "questions": []})
    run(clarify.run(workspace(empty), runtime, CONFIG))
    assert runtime.requests[0][1]["artifacts"]["workspace_mode"] == "greenfield"

    existing = tmp_path / "old"
    existing.mkdir()
    (existing / "package.json").write_text("{}", encoding="utf-8")
    runtime = FakeRuntime({"complete": True, "questions": []})
    run(clarify.run(workspace(existing), runtime, CONFIG))
    assert runtime.requests[0][1]["artifacts"]["workspace_mode"] == "brownfield"


def test_auto_assume_runs_every_round_with_the_recommendations_and_finishes(tmp_path):
    artifacts = workspace(tmp_path)
    runtime = FakeRuntime({"complete": False, "questions": [question("a", "Bán gì?", "quần áo")]},
                          {"complete": False, "questions": [question("a", "Thanh toán?", "COD")]})
    result = run(clarify.run(artifacts, runtime, PreExecutorConfig(max_flow_retries=1, max_clarify_rounds=2),
                             auto_assume=True))
    assert len(runtime.requests) == 2 and result["status"] == "done" and result["auto_assumed"] is True
    assert result["reason"] == "đã hết 2 vòng" and result["assumed"] == 2
    assert runtime.requests[1][1]["artifacts"]["answers"] == [
        {"id": "Q1", "category": "functional_scope", "question": "Bán gì?", "answer": "quần áo", "assumed": True}]
    items = artifacts.read_json("clarification.json")
    assert [(item["answer"], item["assumed"]) for item in items] == [("quần áo", True), ("COD", True)]
    assert gate.state(artifacts, "clarify") == "approved" and artifacts.read_json("coverage.json")


def test_auto_assume_stops_as_soon_as_the_agent_says_enough(tmp_path):
    artifacts = workspace(tmp_path)
    runtime = FakeRuntime({"complete": True, "questions": [question("a", "Bán gì?", "quần áo")]})
    result = run(clarify.run(artifacts, runtime, PreExecutorConfig(max_clarify_rounds=5), auto_assume=True))
    assert len(runtime.requests) == 1 and result["reason"] == "agent thấy đã đủ thông tin"
    assert gate.can_run(artifacts, "business_analyst")


def test_cli_auto_assume_finishes_clarify_in_one_command(tmp_path, capsys):
    repo = tmp_path / "shop"
    repo.mkdir()
    runtime, open_runtime = opener({"complete": False, "questions": [question("a", "Bán gì?", "quần áo")]},
                                   {"complete": True, "questions": []})
    assert cli.main(["clarify", "--workspace", str(repo), "--prompt", "Làm phần mềm bán hàng", "--auto-assume",
                     "--observe", "none"], open_runtime=open_runtime) == 0
    out = capsys.readouterr().out
    assert "clarify: xong sau 2 vòng" in out and "--auto-assume" in out
    folder = repo / ".raac/pre_executor"
    assert json.loads((folder / "clarification.json").read_text(encoding="utf-8"))[0]["assumed"] is True
    assert (folder / "coverage.json").is_file()
    with pytest.raises(SystemExit):  # cờ chỉ có ở clarify
        cli.main(["business_analyst", "--workspace", str(repo), "--auto-assume"])


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
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(with_coverage(reply),
                                                                                          ensure_ascii=False)}}],
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
    assert events[:2] == ["run_started", "flow_started"] and "llm_request" in events and events[-1] == "flow_completed"

    # Lần chạy thứ hai của cùng workspace: cùng run_id, nối tiếp cùng log, token tính riêng cho lần này.
    async def again():
        second = await open_runtime(repo, artifacts.request(), "clarify", stream=False, registry_dir=registry,
                                    results_dir=tmp_path / "results", llm_config=LLMConfig(model="fixture"),
                                    transport=httpx.MockTransport(respond), run_id=runtime.run_id,
                                    usage_baseline={"prompt_tokens": 50, "completion_tokens": 20, "calls": 1})
        try:
            await clarify.run(artifacts, second, CONFIG)
            return second, second.usage()
        finally:
            await second.aclose()

    reply["questions"] = [question("q2", "Giao hàng ở đâu?", "toàn quốc", "phí ship")]
    second, usage = run(again())
    assert second.run_id == runtime.run_id and second.log_path == runtime.log_path
    records = [json.loads(line) for line in runtime.log_path.read_text(encoding="utf-8").splitlines()]
    assert [r["event"] for r in records].count("run_started") == 1
    assert [r["event"] for r in records].count("flow_started") == 2
    sequences = [r["sequence"] for r in records]
    assert sequences == sorted(sequences) and len(set(sequences)) == len(sequences)  # số thứ tự nối tiếp, không trùng
    assert usage["calls"] == 1 and usage["prompt_tokens"] == 50 and usage["completion_tokens"] == 20


# ---------------------------------------------------------------- độ phủ và câu trắc nghiệm (kiểu Spec Kit)

def coverage(**status):
    from raac.pre_executor.contracts import COVERAGE_CATEGORIES

    return [{"category": name, "status": status.get(name, "clear"), "note": "lý do" if status.get(name) else ""}
            for name in COVERAGE_CATEGORIES]


def mc(qid, text, options, default, category="functional_scope"):
    return {"id": qid, "category": category, "text": text, "options": options, "default_answer": default, "why": "w"}


GOOD = {"complete": False, "coverage": coverage(integrations="missing"),
        "questions": [mc("a", "Thanh toán?", ["COD", "VNPay", "Cả hai"], "Cả hai", "integrations")]}


@pytest.mark.parametrize("bad, problem", [
    ({**GOOD, "coverage": coverage(integrations="missing")[:9]}, "coverage is missing category open_items"),
    ({"complete": True, "coverage": coverage(functional_scope="missing"), "questions": []}, "complete is true while categories are missing"),
    ({**GOOD, "questions": [mc("a", "Nền tảng?", ["web", "app"], "web", "ux_flow")]}, "only ask about partial or missing"),
    ({**GOOD, "questions": [mc("a", "Thanh toán?", ["COD"], "COD", "integrations")]}, "must have 2-5 options"),
    ({**GOOD, "questions": [mc("a", "Thanh toán?", ["COD", "VNPay"], "MoMo", "integrations")]}, "default_answer must be one of"),
    ({**GOOD, "coverage": coverage(integrations="missing", ux_flow="not_applicable") and
      [*coverage(integrations="missing")[:2], {"category": "ux_flow", "status": "not_applicable", "note": ""},
       *coverage(integrations="missing")[3:]]}, "ux_flow is not_applicable without a reason"),
])
def test_coverage_and_question_rules_are_sent_back(tmp_path, bad, problem):
    artifacts = workspace(tmp_path)
    runtime = FakeRuntime(bad, GOOD)
    run(clarify.run(artifacts, runtime, CONFIG))
    assert any(problem in item for item in runtime.requests[1][1]["artifacts"]["rule_violations"])
    assert runtime.requests[0][1]["artifacts"]["coverage_categories"]["integrations"].startswith("Tích hợp")


def test_a_spec_as_input_is_passed_whole_and_the_final_coverage_is_reported(tmp_path):
    spec = "# Spec: shop\n## Chức năng\n- giỏ hàng\n- thanh toán COD\n## Ràng buộc\n- 1 tháng"
    artifacts = workspace(tmp_path, request=spec)
    runtime = FakeRuntime(GOOD)
    run(clarify.run(artifacts, runtime, CONFIG))
    assert runtime.requests[0][1]["artifacts"]["request"] == spec.strip()
    text = artifacts.read_text("answers.md")
    assert "| Tích hợp ngoài | thiếu |" in text and "**Cả hai** (khuyến nghị)" in text
    assert "_Nhóm: Tích hợp ngoài · Vì sao hỏi: w_" in text

    second = FakeRuntime({"complete": True, "coverage": coverage(integrations="partial"), "questions": []})
    clarify.save_answers(artifacts, {"Q1": "VNPay"})
    result = run(clarify.run(artifacts, second, CONFIG))
    assert second.requests[0][1]["artifacts"]["previous_coverage"][4] == {
        "category": "integrations", "status": "missing", "note": "lý do"}
    assert result["status"] == "done" and result["coverage"] == {"clear": 9, "outstanding": 1}
    final = {item["category"]: item["status"] for item in artifacts.read_json("coverage.json")}
    assert final["integrations"] == "outstanding" and final["functional_scope"] == "clear"


def test_answered_categories_become_resolved_in_the_final_coverage(tmp_path):
    artifacts = workspace(tmp_path)
    run(clarify.run(artifacts, FakeRuntime({**GOOD, "complete": True, "coverage": coverage(integrations="partial")}),
                    CONFIG))
    run(clarify.run(artifacts, FakeRuntime(), CONFIG))  # trả lời xong -> tự chốt
    final = {item["category"]: item["status"] for item in artifacts.read_json("coverage.json")}
    assert final["integrations"] == "resolved"
    assert artifacts.read_json("clarification.json")[0]["category"] == "integrations"


def test_cli_reports_an_agent_failure_without_a_traceback(tmp_path, capsys):
    from raac.common.errors import RunGuardError

    class Down(FakeRuntime):
        async def run(self, role_name, request):
            raise RunGuardError("usage_unavailable")

    async def open_runtime(workspace, request_text, flow, model=None, **kwargs):
        return Down()

    assert cli.main(["clarify", "--workspace", str(tmp_path), "--prompt", "x"], open_runtime=open_runtime) == 3
    assert "Kiểm tra 9router" in capsys.readouterr().err


def test_token_usage_is_shown_and_accumulated_like_mas(tmp_path, capsys):
    class Counted(FakeRuntime):
        def usage(self):
            return {"prompt_tokens": 1200, "completion_tokens": 300, "calls": 2, "cached_tokens": None}

    runtime = Counted({"complete": False, "questions": [question("a", "Bán gì?", "quần áo")]},
                      {"complete": False, "questions": [question("a", "Giao hàng?", "toàn quốc")]})

    async def open_runtime(workspace, request_text, flow, model=None, **kwargs):
        return runtime

    assert cli.main(["clarify", "--workspace", str(tmp_path), "--prompt", "x"], open_runtime=open_runtime) == 0
    assert cli.main(["clarify", "--workspace", str(tmp_path)], open_runtime=open_runtime) == 0
    out = capsys.readouterr().out
    assert "Token lần này: token vào 1,200 / ra 300, 2 lượt gọi model" in out
    assert "cộng dồn workspace: token vào 2,400 / ra 600, 4 lượt gọi model" in out
    usage = Artifacts(tmp_path).read_json("usage.json")
    assert usage["flows"]["clarify"] == {"prompt_tokens": 2400, "completion_tokens": 600, "calls": 4, "runs": 2}
    assert [run["run_id"] for run in usage["runs"]] == ["chain-fake", "chain-fake"]
    assert cli.main(["status", "--workspace", str(tmp_path)]) == 0
    status = capsys.readouterr().out
    assert "clarify" in status and "token vào 2,400 / ra 600" in status and "Tổng: token vào 2,400" in status


def test_the_clarifier_prompt_lists_exactly_the_coverage_categories_checked_in_code():
    import re

    from raac.pre_executor.contracts import COVERAGE_CATEGORIES

    prompt = (ROOT / "registry/prompts/pre_executor/clarifier.md").read_text(encoding="utf-8")
    listed = re.findall(r"^- `([a-z_]+)`:", prompt.split("Each round:")[0].split("The ten coverage categories")[1],
                        re.MULTILINE)
    assert listed == list(COVERAGE_CATEGORIES)  # sửa nhóm ở contracts.py thì phải sửa cả prompt
