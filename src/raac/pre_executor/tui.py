"""TUI của pre_executor: clarify (hỏi và trả lời) -> requirements -> profile (bản nháp, góp ý, approve).

    python -m raac.pre_executor tui --workspace <repo> [--prompt "..." | --prompt-file request.md] [--model ...]

Luồng: (nhập yêu cầu nếu workspace chưa có) -> agent hỏi một vòng -> từng câu một cửa sổ, ô trả lời điền sẵn đáp án
gợi ý (Enter để trả lời) -> agent tự hỏi vòng tiếp dựa trên câu trả lời -> ... -> khi agent thấy đủ (hoặc hết số vòng)
clarify tự chốt (clarification.json). Không có góp ý hay approve ở bước này.
Tiếp đó requirements: agent viết tổng quan, chức năng cốt lõi, NFR -> TUI hiện bản nháp -> người dùng góp ý để agent
sửa, lặp tới khi vừa ý thì Approve. Rồi profile (user flow, quy tắc nghiệp vụ, FR + AC) cùng cách duyệt.
Mở lại TUI sẽ tiếp tục đúng bước đang dở.
Câu trả lời được ghi vào answers.md, nên dừng giữa chừng (Ctrl+Q) rồi mở lại vẫn tiếp tục được.
Độc lập với TUI của MAS (raac.tui); dùng chung flow, gate và runtime với CLI.
"""

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.widgets import Button, Footer, Header, Input, Label, Markdown, RichLog, Static, TextArea

from . import cli, gate
from .artifacts import Artifacts
from .flows import clarify

CSS = """
QuestionScreen, MessageScreen { align: center middle; }
DraftScreen #draft { height: 1fr; border: round $primary; padding: 0 1; }
DraftScreen #note { margin-top: 1; }
#dialog { width: 80%; max-width: 100; height: auto; border: thick $primary; padding: 1 2; background: $surface; }
#dialog Label { margin-bottom: 1; }
.why { color: $text-muted; margin-bottom: 1; }
.buttons { height: auto; margin-top: 1; }
.buttons Button { margin-right: 1; }
#request { height: 10; }
"""


class RequestScreen(Screen[str]):
    """Nhập yêu cầu khi workspace chưa có request.md."""

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="dialog"):
            yield Label("Bạn muốn xây dựng gì? Mô tả yêu cầu của bạn:")
            yield TextArea(id="request")
            with Horizontal(classes="buttons"):
                yield Button("Bắt đầu", id="start", variant="primary")
        yield Footer()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        text = self.query_one("#request", TextArea).text.strip()
        if text:
            self.dismiss(text)


class QuestionScreen(ModalScreen[str]):
    """Một câu hỏi: đọc, sửa câu trả lời gợi ý, Enter để trả lời."""

    def __init__(self, question: dict, index: int, total: int, round_number: int):
        super().__init__()
        self.question, self.index, self.total, self.round_number = question, index, total, round_number

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label(f"Vòng {self.round_number} · Câu {self.index}/{self.total}")
            yield Static(f"[b]{self.question['text']}[/b]")
            if self.question.get("why"):
                yield Static(f"Vì sao hỏi: {self.question['why']}", classes="why")
            yield Input(value=self.question.get("default_answer", ""), placeholder="Câu trả lời", id="answer")
            with Horizontal(classes="buttons"):
                yield Button("Trả lời (Enter)", id="submit", variant="primary")
                yield Button("Dùng gợi ý", id="default")

    def on_mount(self) -> None:
        self.query_one("#answer", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip())

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "default":
            self.dismiss(self.question.get("default_answer", ""))
        else:
            self.dismiss(self.query_one("#answer", Input).value.strip())


class MessageScreen(ModalScreen[str]):
    """Thông báo kết thúc hoặc lỗi; trả về id nút được bấm."""

    def __init__(self, message: str, buttons: list[tuple[str, str]]):
        super().__init__()
        self.message, self.buttons = message, buttons

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static(self.message)
            with Horizontal(classes="buttons"):
                for button_id, label in self.buttons:
                    yield Button(label, id=button_id, variant="primary" if button_id in {"close", "retry"} else "default")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id)


class DraftScreen(Screen[tuple[str, str]]):
    """Bản nháp của một flow: đọc, góp ý để agent sửa, hoặc Approve."""

    def __init__(self, title: str, markdown: str):
        super().__init__()
        self.title_text, self.markdown = title, markdown

    def compose(self) -> ComposeResult:
        yield Header()
        yield Label(self.title_text)
        with VerticalScroll(id="draft"):
            yield Markdown(self.markdown)
        yield Input(placeholder="Góp ý cho agent (vd. thêm mã giảm giá, bỏ đánh giá sản phẩm) rồi Enter", id="note")
        with Horizontal(classes="buttons"):
            yield Button("Gửi góp ý", id="feedback", variant="primary")
            yield Button("Approve", id="approve", variant="success")
            yield Button("Thoát", id="quit", variant="error")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#note", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.value.strip():
            self.dismiss(("feedback", event.value.strip()))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        note = self.query_one("#note", Input).value.strip()
        if event.button.id == "feedback" and not note:
            return
        self.dismiss((event.button.id, note))


class PreExecutorApp(App):
    TITLE = "RAAC pre_executor"
    CSS = CSS
    BINDINGS = [("ctrl+q", "quit", "Thoát (đã lưu câu trả lời)")]

    def __init__(self, artifacts: Artifacts, *, open_runtime=None, model: str | None = None):
        super().__init__()
        self.artifacts, self.open_runtime, self.model = artifacts, open_runtime, model
        self.clarification: list[dict] | None = None
        self.requirements: dict | None = None
        self.profile: dict | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        yield RichLog(id="log", wrap=True, markup=True)
        yield Footer()

    def write(self, text: str) -> None:
        self.query_one("#log", RichLog).write(text)

    def on_mount(self) -> None:
        if self.artifacts.request():
            self.run_worker(self.session(), exclusive=True)
        else:
            self.push_screen(RequestScreen(), self.request_entered)

    def request_entered(self, text: str | None) -> None:
        if text:
            self.artifacts.save_request(text)
            self.run_worker(self.session(), exclusive=True)

    async def run_flow(self, flow: str, note: str | None = None) -> dict | None:
        """Chạy một flow; lỗi thì hỏi Thử lại / Thoát. None = người dùng chọn thoát."""
        while True:
            self.write(f"[dim]{flow}: đang chạy agent...[/dim]")
            try:
                return await cli._run_flow(flow, self.artifacts, note=note, open_runtime=self.open_runtime,
                                           write=lambda _text: None, model=self.model)
            except Exception as exc:  # noqa: BLE001 -- báo lỗi, cho thử lại hoặc thoát; kết quả trước đã lưu
                detail = exc if isinstance(exc, (ValueError, RuntimeError)) else type(exc).__name__
                self.write(f"[red]Lỗi: {detail}[/red]")
                choice = await self.push_screen_wait(MessageScreen(f"Lỗi: {detail}", [("retry", "Thử lại"),
                                                                                       ("quit", "Thoát")]))
                if choice != "retry":
                    return None

    async def session(self) -> None:
        self.write(f"[b]Yêu cầu:[/b] {self.artifacts.request()}")
        if gate.state(self.artifacts, "clarify") != "approved" and not await self.clarify_stage():
            self.exit(None)
            return
        self.clarification = self.artifacts.read_json("clarification.json")
        for flow, next_label in DRAFT_FLOWS:
            if not await self.draft_stage(flow, next_label):
                self.exit(self.profile or self.requirements)
                return
        self.exit(self.profile)

    async def clarify_stage(self) -> bool:
        """Hỏi - trả lời đến khi clarify tự chốt. False = người dùng thoát."""
        while True:
            result = await self.run_flow("clarify")
            if result is None:
                return False
            if result["status"] == "done":
                self.clarification = self.artifacts.read_json("clarification.json")
                message = (f"Làm rõ xong sau {result['round']} vòng ({result['reason']}).\n"
                           f"{result['answered']} câu trả lời, {result['assumed']} câu dùng gợi ý (giả định).")
                self.write(message)
                choice = await self.push_screen_wait(MessageScreen(message, [("next", "Tiếp: phân tích yêu cầu"),
                                                                             ("close", "Đóng")]))
                return choice == "next"
            history = clarify.rounds(self.artifacts)
            questions = history[-1]["questions"]
            if history[-1].get("summary"):
                self.write(f"[i]{history[-1]['summary']}[/i]")
            for index, question in enumerate(questions, start=1):
                answer = await self.push_screen_wait(QuestionScreen(question, index, len(questions), len(history)))
                self.write(f"{question['id']}. {question['text']} → [b]{answer}[/b]")
                clarify.save_answers(self.artifacts, {question["id"]: answer})  # lưu từng câu

    async def draft_stage(self, flow: str, next_label: str | None) -> bool:
        """Bản nháp -> góp ý -> agent sửa -> ... -> Approve. True = đi tiếp flow sau, False = dừng."""
        title, summary = DRAFT_TITLES[flow]
        state = gate.state(self.artifacts, flow)
        if state != "approved":
            if state == "missing" and await self.run_flow(flow) is None:  # chưa có bản nháp thì viết
                return False
            while True:
                action, note = await self.push_screen_wait(DraftScreen(
                    f"{title} (bản nháp): góp ý để agent sửa, vừa ý thì Approve",
                    self.artifacts.read_text(gate.APPROVED_FILE[flow].replace(".json", ".md")) or ""))
                if action == "feedback":
                    self.write(f"{flow} · góp ý: {note}")
                    if await self.run_flow(flow, note) is None:
                        return False
                elif action == "approve":
                    cli._approve(flow, self.artifacts, write=self.write)
                    break
                else:
                    return False
        data = self.artifacts.read_json(gate.APPROVED_FILE[flow])
        setattr(self, flow, data)
        buttons = ([("next", next_label)] if next_label else []) + [("close", "Đóng")]
        message = f"Đã duyệt {title.lower()}: {summary(data)}.\nKết quả: {self.artifacts.path(gate.APPROVED_FILE[flow])}"
        self.write(message)
        return await self.push_screen_wait(MessageScreen(message, buttons)) == "next"


DRAFT_FLOWS = (("requirements", "Tiếp: Requirement Profile"), ("profile", None))
DRAFT_TITLES = {
    "requirements": ("Yêu cầu sản phẩm", lambda data: f"{len(data['features'])} chức năng cốt lõi, "
                                                      f"{len(data['non_functional_requirements'])} NFR"),
    "profile": ("Hồ sơ yêu cầu", lambda data: f"{len(data['user_flows'])} user flow, "
                                             f"{len(data['functional_requirements'])} yêu cầu chi tiết, "
                                             f"{sum(len(fr['acceptance_criteria']) for fr in data['functional_requirements'])} AC"),
}


def run_tui(artifacts: Artifacts, *, model: str | None = None) -> list[dict] | None:
    return PreExecutorApp(artifacts, model=model).run()
