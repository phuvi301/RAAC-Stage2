"""TUI của pre_executor: clarify (hỏi và trả lời) -> business_analyst -> solution_architect -> project_manager
(bản nháp, góp ý, approve).

    python -m raac.pre_executor tui --workspace <repo> [--prompt "..." | --prompt-file request.md] [--model ...]

Luồng: (nhập yêu cầu nếu workspace chưa có) -> agent hỏi một vòng -> từng câu một cửa sổ, ô trả lời điền sẵn đáp án
gợi ý (Enter để trả lời) -> agent tự hỏi vòng tiếp dựa trên câu trả lời -> ... -> khi agent thấy đủ (hoặc hết số vòng)
clarify tự chốt (clarification.json). Không có góp ý hay approve ở bước này.
Tiếp đó business_analyst: agent viết đặc tả nghiệp vụ (chức năng, FR + AC, NFR, thực thể, tích hợp ngoài, ...) ->
TUI hiện bản nháp -> người dùng góp ý để agent sửa, lặp tới khi vừa ý thì Approve. Rồi solution_architect (tech
stack, module, API, ràng buộc toàn cục, runtime/test) và project_manager (DAG task + tasks.json) cùng cách duyệt.
Mở lại TUI sẽ tiếp tục đúng bước đang dở.
Câu trả lời được ghi vào answers.md, nên dừng giữa chừng (Ctrl+Q) rồi mở lại vẫn tiếp tục được.
Độc lập với TUI của MAS (raac.tui); dùng chung flow, gate và runtime với CLI.
"""

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.widgets import Button, Footer, Header, Input, Label, Markdown, OptionList, RichLog, Static, TextArea

from . import cli, gate
from .artifacts import Artifacts
from .flows import clarify
from .events import describe
from .render import CATEGORY_LABELS, STATUS_LABELS

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
    """Một câu hỏi. Trắc nghiệm: chọn trong danh sách (khuyến nghị chọn sẵn) hoặc tự nhập ở ô "Khác".
    Trả lời ngắn: sửa gợi ý trong ô rồi Enter."""

    def __init__(self, question: dict, index: int, total: int, round_number: int):
        super().__init__()
        self.question, self.index, self.total, self.round_number = question, index, total, round_number
        self.options = list(question.get("options") or [])
        self.default = question.get("default_answer", "")

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            category = CATEGORY_LABELS.get(self.question.get("category"), self.question.get("category") or "")
            yield Label(f"Vòng {self.round_number} · Câu {self.index}/{self.total}" + (f" · {category}" if category else ""))
            yield Static(f"[b]{self.question['text']}[/b]")
            if self.question.get("why"):
                yield Static(f"Vì sao hỏi: {self.question['why']}", classes="why")
            if self.options:
                yield OptionList(*[f"{option}  (khuyến nghị)" if option == self.default else option
                                   for option in self.options], id="options")
                yield Input(placeholder="Khác: tự nhập câu trả lời rồi Enter", id="answer")
            else:
                yield Input(value=self.default, placeholder="Câu trả lời", id="answer")
            with Horizontal(classes="buttons"):
                yield Button("Trả lời (Enter)", id="submit", variant="primary")
                yield Button("Dùng khuyến nghị", id="default")

    def on_mount(self) -> None:
        if self.options:
            choices = self.query_one("#options", OptionList)
            choices.highlighted = self.options.index(self.default) if self.default in self.options else 0
            choices.focus()
        else:
            self.query_one("#answer", Input).focus()

    def chosen(self) -> str:
        typed = self.query_one("#answer", Input).value.strip()
        if typed or not self.options:
            return typed
        highlighted = self.query_one("#options", OptionList).highlighted
        return self.options[highlighted if highlighted is not None else 0]

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(self.options[event.option_index])

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(self.chosen())

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(self.default if event.button.id == "default" else self.chosen())


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


MIN_NOTE = 3  # góp ý ngắn hơn thế (gõ nhầm) không gửi: mỗi lần sửa tốn hàng chục nghìn token


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

    def _note_ok(self, note: str) -> bool:
        if len(note) >= MIN_NOTE:
            return True
        self.app.notify(f"Góp ý quá ngắn (ít nhất {MIN_NOTE} ký tự), chưa gửi.", severity="warning")
        return False

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if self._note_ok(event.value.strip()):
            self.dismiss(("feedback", event.value.strip()))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        note = self.query_one("#note", Input).value.strip()
        if event.button.id == "feedback" and not self._note_ok(note):
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
        self.ba_spec: dict | None = None
        self.solution_architecture: dict | None = None
        self.project_manager: dict | None = None

    def last_result(self) -> dict | None:
        return self.project_manager or self.solution_architecture or self.ba_spec

    def compose(self) -> ComposeResult:
        yield Header()
        yield RichLog(id="log", wrap=True, markup=True)
        yield Footer()

    def write(self, text: str) -> None:
        self.query_one("#log", RichLog).write(text)

    def on_trace(self, record: dict) -> None:
        """Từng sự kiện của agent (gọi model, dùng tool, token, vi phạm luật) hiện ngay trong log."""
        line = describe(record)
        if line:
            self.write(f"[dim]{line}[/dim]" if line.startswith("   ") else line)

    def show_usage(self, result: dict | None) -> None:
        """Như MAS: token của lần chạy vào log, tổng của workspace lên thanh tiêu đề."""
        if result and result.get("usage", {}).get("calls"):
            self.write(f"[dim]Token: {cli.format_usage(result['usage'])}[/dim]")
        total = self.artifacts.usage_total()
        if total["calls"]:
            self.sub_title = f"Tổng {cli.format_usage(total)}"

    def on_mount(self) -> None:
        self.show_usage(None)
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
                result = await cli._run_flow(flow, self.artifacts, note=note, open_runtime=self.open_runtime,
                                             write=lambda _text: None, model=self.model, event_handler=self.on_trace)
                self.show_usage(result)
                if result.get("changes"):
                    self.write(f"[b]Thay đổi so với bản trước ({len(result['changes'])}):[/b]")
                    for change in result["changes"]:
                        self.write(f"  {change}")
                return result
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
                self.exit(self.last_result())
                return
        self.exit(self.last_result())

    async def clarify_stage(self) -> bool:
        """Hỏi - trả lời đến khi clarify tự chốt. False = người dùng thoát."""
        while True:
            result = await self.run_flow("clarify")
            if result is None:
                return False
            if result["status"] == "done":
                self.clarification = self.artifacts.read_json("clarification.json")
                coverage = ", ".join(f"{STATUS_LABELS.get(status, status)}: {count}"
                                     for status, count in result.get("coverage", {}).items())
                message = (f"Làm rõ xong sau {result['round']} vòng ({result['reason']}).\n"
                           f"{result['answered']} câu trả lời, {result['assumed']} câu dùng khuyến nghị (giả định).\n"
                           + (f"Độ phủ: {coverage}" if coverage else ""))
                self.write(message)
                choice = await self.push_screen_wait(MessageScreen(message, [("next", "Tiếp: Business Analyst"),
                                                                             ("close", "Đóng")]))
                return choice == "next"
            history = clarify.rounds(self.artifacts)
            questions = history[-1]["questions"]
            if history[-1].get("summary"):
                self.write(f"[i]{history[-1]['summary']}[/i]")
            for item in history[-1].get("coverage", []):
                if item["status"] in ("partial", "missing"):
                    self.write(f"[yellow]{CATEGORY_LABELS.get(item['category'], item['category'])}: "
                               f"{STATUS_LABELS.get(item['status'], item['status'])}[/yellow] {item.get('note', '')}")
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
        setattr(self, STATE_ATTR.get(flow, flow), data)
        buttons = ([("next", next_label)] if next_label else []) + [("close", "Đóng")]
        message = f"Đã duyệt {title.lower()}: {summary(data)}.\nKết quả: {self.artifacts.path(gate.APPROVED_FILE[flow])}"
        self.write(message)
        return await self.push_screen_wait(MessageScreen(message, buttons)) == "next"


DRAFT_FLOWS = (
    ("business_analyst", "Tiếp: Solution Architect"),
    ("solution_architect", "Tiếp: Project Manager"),
    ("project_manager", None),
)
STATE_ATTR = {"business_analyst": "ba_spec", "solution_architect": "solution_architecture"}  # flow -> thuộc tính lưu bản đã duyệt trên app
DRAFT_TITLES = {
    "business_analyst": ("Đặc tả nghiệp vụ", lambda data: f"{len(data['features'])} chức năng, "
                                                         f"{len(data['functional_requirements'])} yêu cầu chi tiết, "
                                                         f"{sum(len(fr['acceptance_criteria']) for fr in data['functional_requirements'])} AC, "
                                                         f"{len(data['non_functional_requirements'])} NFR, "
                                                         f"{len(data['edge_cases'])} trường hợp biên"),
    "solution_architect": ("Kiến trúc giải pháp", lambda data: f"{data['tech_stack']['language']['name']}, "
                                                               f"{len(data['modules'])} module, "
                                                               f"{len(data['api_contracts'])} hợp đồng API, "
                                                               f"{len(data['global_constraints'])} ràng buộc"),
    "project_manager": ("Kế hoạch công việc (PM)", lambda data: f"{len(data['tasks'])} task, "
                                                                f"{len(data.get('milestones', []))} milestone"),
}


def run_tui(artifacts: Artifacts, *, model: str | None = None) -> dict | None:
    return PreExecutorApp(artifacts, model=model).run()
