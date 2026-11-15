"""TUI của pre_executor: clarify chỉ hỏi và trả lời, mỗi câu một cửa sổ, tự hỏi tiếp và tự chốt."""

import asyncio

import pytest

pytest.importorskip("textual")

from textual.widgets import Input, TextArea  # noqa: E402

from raac.pre_executor import gate  # noqa: E402
from pe_helpers import with_coverage  # noqa: E402
from raac.pre_executor.artifacts import Artifacts  # noqa: E402
from raac.pre_executor.render import parse_answers  # noqa: E402
from raac.pre_executor.tui import MessageScreen, PreExecutorApp, QuestionScreen, RequestScreen  # noqa: E402


def question(qid, text, default=""):
    return {"id": qid, "text": text, "default_answer": default, "why": "vì sao"}


class FakeRuntime:
    def __init__(self, *outputs):
        self.outputs, self.requests = list(outputs), []
        self.run_id, self.task_id = "chain-fake", "task-fake"

    async def run(self, role_name, request):
        self.requests.append(request)
        output = with_coverage(self.outputs.pop(0))
        if isinstance(output, Exception):
            raise output
        return output

    async def aclose(self, *, status="completed"):
        pass


def opener(runtime):
    async def open_runtime(workspace, request_text, flow, model=None, **kwargs):
        return runtime
    return open_runtime


async def wait_for(pilot, screen_type, tries=50):
    for _ in range(tries):
        await pilot.pause()
        if isinstance(pilot.app.screen, screen_type):
            return pilot.app.screen
    raise AssertionError(f"{screen_type.__name__} did not appear (current: {type(pilot.app.screen).__name__})")


def test_each_question_gets_a_window_and_the_next_round_starts_by_itself(tmp_path):
    artifacts = Artifacts(tmp_path)
    artifacts.save_request("Làm phần mềm bán hàng")
    runtime = FakeRuntime(
        {"complete": False, "questions": [question("a", "Bán gì?", "quần áo"), question("b", "Thanh toán?", "VNPay")]},
        {"complete": True, "summary": "đủ", "questions": [question("a", "Giao hàng ở đâu?", "toàn quốc")]},
    )
    app = PreExecutorApp(artifacts, open_runtime=opener(runtime))

    async def scenario():
        async with app.run_test() as pilot:
            first = await wait_for(pilot, QuestionScreen)
            assert first.query_one(Input).value == "quần áo"  # đáp án gợi ý điền sẵn
            first.query_one(Input).value = "giày dép"
            await pilot.press("enter")
            await pilot.pause()
            second = await wait_for(pilot, QuestionScreen)
            assert second.question["text"] == "Thanh toán?"
            await pilot.click("#default")
            third = await wait_for(pilot, QuestionScreen)  # vòng 2 tự bắt đầu, không cần bấm gì
            assert third.question["id"] == "Q3" and third.round_number == 2
            await pilot.press("enter")
            done = await wait_for(pilot, MessageScreen)
            assert "xong sau 2 vòng" in done.message
            await pilot.click("#close")
            await pilot.pause()

    asyncio.run(scenario())
    assert parse_answers(artifacts.read_text("answers.md")) == {"Q1": "giày dép", "Q2": "VNPay", "Q3": "toàn quốc"}
    assert len(runtime.requests) == 2 and "user_feedback" not in runtime.requests[1]["artifacts"]
    assert gate.state(artifacts, "clarify") == "approved"
    assert [item["assumed"] for item in app.clarification] == [False, True, True]


def test_the_request_is_typed_in_the_tui_and_an_error_can_be_retried(tmp_path):
    artifacts = Artifacts(tmp_path)
    runtime = FakeRuntime(RuntimeError("provider down"), {"complete": True, "summary": "đủ", "questions": []})
    app = PreExecutorApp(artifacts, open_runtime=opener(runtime))

    async def scenario():
        async with app.run_test() as pilot:
            screen = await wait_for(pilot, RequestScreen)
            screen.query_one(TextArea).text = "Website đặt lịch nha khoa"
            await pilot.click("#start")
            error = await wait_for(pilot, MessageScreen)
            assert "provider down" in error.message
            await pilot.click("#retry")
            await pilot.pause()
            done = await wait_for(pilot, MessageScreen)
            assert "xong sau 1 vòng" in done.message
            await pilot.click("#close")
            await pilot.pause()

    asyncio.run(scenario())
    assert artifacts.request() == "Website đặt lịch nha khoa"
    assert runtime.requests[0]["artifacts"]["request"] == "Website đặt lịch nha khoa"
    assert gate.state(artifacts, "clarify") == "approved"


def test_multiple_choice_preselects_the_recommendation_and_accepts_another_option_or_free_text(tmp_path):
    from textual.widgets import OptionList

    artifacts = Artifacts(tmp_path)
    artifacts.save_request("Làm phần mềm bán hàng")
    options = ["COD", "VNPay", "Cả hai"]
    runtime = FakeRuntime(
        {"complete": True, "questions": [
            {"id": "a", "category": "integrations", "text": "Thanh toán?", "options": options,
             "default_answer": "Cả hai", "why": "w"},
            {"id": "b", "category": "ux_flow", "text": "Nền tảng?", "options": ["Web", "App"],
             "default_answer": "Web", "why": "w"}]})
    app = PreExecutorApp(artifacts, open_runtime=opener(runtime))

    async def scenario():
        async with app.run_test(size=(120, 40)) as pilot:
            first = await wait_for(pilot, QuestionScreen)
            choices = first.query_one(OptionList)
            assert choices.highlighted == 2  # khuyến nghị được chọn sẵn
            await pilot.press("up", "enter")  # chọn "VNPay"
            second = await wait_for(pilot, QuestionScreen)
            assert second.question["text"] == "Nền tảng?"
            second.query_one(Input).value = "Web và Zalo mini app"  # tự nhập ở ô "Khác"
            second.query_one(Input).focus()
            await pilot.press("enter")
            await wait_for(pilot, MessageScreen)
            await pilot.click("#close")
            await pilot.pause()

    asyncio.run(scenario())
    assert parse_answers(artifacts.read_text("answers.md")) == {"Q1": "VNPay", "Q2": "Web và Zalo mini app"}
    assert [item["assumed"] for item in artifacts.read_json("clarification.json")] == [False, False]
