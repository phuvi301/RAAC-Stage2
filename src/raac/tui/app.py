"""Local MAS dashboard. All widget mutations happen on the Textual thread."""
import asyncio
import queue
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar

from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult, SystemCommand
from textual.binding import Binding
from textual.containers import Grid, Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    Collapsible,
    Footer,
    Label,
    OptionList,
    RichLog,
    Select,
    Static,
    TextArea,
)
from textual.widgets.option_list import Option

from .bridge import RuntimeBridge
from .state import RunState, display_event, history, literal, read_page

LEVEL_CHOICES = [('L1 Fixed', 'L1'), ('L2 Routing', 'L2'), ('L3 Collaboration', 'L3'), ('L4 Mock', 'L4')]
APPROVAL_MODES = [('Ask each time', 'manual'), ('Auto approve (Full access)', 'auto')]


class PromptEditor(ModalScreen):
    BINDINGS: ClassVar = [('escape', 'dismiss(None)', 'Back')]

    def __init__(self, prompt, read_only):
        super().__init__()
        self.prompt, self.read_only = prompt, read_only

    def compose(self):
        with Vertical(classes='detail'):
            yield Label('Full prompt')
            yield TextArea(self.prompt, read_only=self.read_only, id='expanded-prompt')
            yield Button('Done', id='done')

    @on(Button.Pressed)
    def done(self):
        self.dismiss(self.query_one(TextArea).text)


class Confirm(ModalScreen):
    BINDINGS: ClassVar = [('escape', 'dismiss(False)', 'Back')]

    def __init__(self, question):
        super().__init__()
        self.question = question

    def compose(self):
        with Vertical(classes='dialog'):
            yield Label(self.question)
            with Horizontal():
                yield Button('Keep running', id='no', variant='primary')
                yield Button('Stop run', id='yes')

    @on(Button.Pressed)
    def choose(self, event):
        self.dismiss(event.button.id == 'yes')


class ApprovalDialog(ModalScreen):
    BINDINGS: ClassVar = [('escape', 'dismiss(None)', 'Close (keep pending)')]

    def __init__(self, token, request):
        super().__init__()
        self.token, self.request = token, request

    def compose(self):
        with Vertical(classes='dialog'):
            yield Label(literal(f"Approval required: {self.request.get('role', 'Agent')} / {self.request.get('tool', '')}"))
            yield Label('Approve once authorizes the entire operation, including batch/configure/provision steps.')
            with VerticalScroll():
                yield Static(display_event(self.request), markup=False)
            with Horizontal():
                yield Button('Deny', id='deny', variant='primary')
                yield Button('Approve once', id='approve')
                yield Button('Details', id='details')

    @on(Button.Pressed)
    def choose(self, event):
        if event.button.id == 'details':
            self.query_one(VerticalScroll).focus()
        else:
            self.dismiss((self.token, self.request, event.button.id == 'approve'))


class Detail(ModalScreen):
    BINDINGS: ClassVar = [('escape', 'dismiss', 'Back')]

    def __init__(self, path, key):
        super().__init__()
        self.path, self.key = path, key
        self.journal_offset = 0
        self.pages = [0]
        self.page = 0

    def compose(self):
        with Vertical(classes='detail'):
            yield Label('Agent events — Enter/Space expands a focused event')
            yield VerticalScroll(id='events')
            with Horizontal():
                yield Button('Previous page', id='previous')
                yield Button('Next page', id='next')
                yield Button('Refresh', id='refresh')
                yield Button('Back', id='back')

    async def on_mount(self):
        await self.load_page()

    async def load_page(self):
        try:
            events, end = await asyncio.to_thread(read_page, self.path, self.pages[self.page], 200, self.key)
        except OSError:
            self.notify('Cannot read the selected log')
            return
        self.journal_offset = end
        if not self.is_running:
            return
        box = self.query_one('#events', VerticalScroll)
        await box.remove_children()
        for event in events:
            title = literal(f"{event.get('sequence', '—')} {event.get('event', 'Event')} {event.get('timestamp', '')}")
            await box.mount(Collapsible(Static(display_event(event), markup=False), title=title))
        self.query_one('#previous', Button).disabled = self.page == 0
        self.query_one('#next', Button).disabled = end == self.pages[self.page]

    @on(Button.Pressed)
    async def choose(self, event):
        if event.button.id == 'back':
            self.dismiss()
            return
        if event.button.id == 'refresh':
            await self.load_page()
            return
        if event.button.id == 'next':
            self.page += 1
            if len(self.pages) <= self.page:
                self.pages.append(self.journal_offset)
        elif self.page:
            self.page -= 1
        await self.load_page()


class Picker(ModalScreen):
    BINDINGS: ClassVar = [('escape', 'dismiss(None)', 'Back')]

    def __init__(self, title, choices):
        super().__init__()
        self.title_text, self.choices = title, choices

    def compose(self):
        with Vertical(classes='dialog'):
            yield Label(self.title_text)
            yield OptionList(*(Option(Text(label), id=str(i)) for i, (label, value) in enumerate(self.choices)))

    @on(OptionList.OptionSelected)
    def selected(self, event):
        self.dismiss(self.choices[int(event.option.id)][1])


class History(ModalScreen):
    BINDINGS: ClassVar = [('escape', 'dismiss(None)', 'Back')]

    def __init__(self, roots):
        super().__init__()
        self.roots, self.page, self.paths = roots, 0, []

    def compose(self):
        with Vertical(classes='dialog'):
            yield Label('Local run history (read only)', id='page')
            yield OptionList(id='runs')
            with Horizontal():
                yield Button('Previous', id='previous')
                yield Button('Next', id='next')
                yield Button('Back', id='back')

    async def on_mount(self):
        await self.load_page()

    async def load_page(self):
        try:
            self.paths, count = await asyncio.to_thread(history, self.roots, self.page)
        except OSError:
            self.notify('Cannot read run history')
            self.paths, count = [], 0
        if not self.is_running:
            return
        self.query_one('#page', Label).update(f'History — page {self.page + 1}, {count} runs')
        options = self.query_one('#runs', OptionList)
        options.clear_options()
        options.add_options([Option(Text(literal(p.parent.name)), id=str(i)) for i, p in enumerate(self.paths)])
        self.query_one('#previous', Button).disabled = not self.page
        self.query_one('#next', Button).disabled = (self.page + 1) * 20 >= count

    @on(OptionList.OptionSelected)
    def selected(self, event):
        self.dismiss(self.paths[int(event.option.id)])

    @on(Button.Pressed)
    async def choose(self, event):
        if event.button.id == 'back':
            self.dismiss(None)
            return
        self.page += 1 if event.button.id == 'next' else -1
        await self.load_page()


class ExecutionLog(RichLog):
    can_focus = False

    def on_mouse_scroll_up(self):
        self.parent.follow = False


class AgentPanel(Vertical):
    can_focus = True
    BINDINGS: ClassVar = [Binding('enter', 'detail', 'Details'), Binding('j', 'down', 'Scroll down', show=False),
                Binding('k', 'up', 'Scroll up', show=False), Binding('pageup', 'page_up', show=False),
                Binding('pagedown', 'page_down', show=False), Binding('ctrl+l', 'follow', 'Follow')]

    def __init__(self, key):
        super().__init__()
        self.key = key
        self.version = -1
        self.last_sequence = None
        self.follow = True
        self.rewrite = 0

    def compose(self):
        yield Static('', classes='agent-header', markup=False)
        yield ExecutionLog(wrap=True, markup=False, highlight=False, max_lines=1000, auto_scroll=False)
        yield Static('', classes='usage', markup=False)
        yield Collapsible(Static('', classes='public-summary', markup=False), title='Public summary', collapsed=True)

    def render_agent(self, agent):
        self.border_title = agent.role
        header = f'{agent.status} | {agent.phase}'
        if agent.waiting_since:
            try:
                elapsed = (datetime.now(UTC) - datetime.fromisoformat(agent.waiting_since)).total_seconds()
                header += f' | Waiting for model {max(0, int(elapsed))}s'
            except ValueError:
                pass
        if agent.started:
            try:
                duration = (datetime.fromisoformat(agent.ended) if agent.ended else datetime.now(UTC)) - datetime.fromisoformat(agent.started)
                header += f' | {max(0, int(duration.total_seconds()))}s'
            except (ValueError, TypeError):
                pass
        if header != getattr(self, '_header', None):
            self._header = header
            self.query_one('.agent-header', Static).update(literal(header))
        if self.version != agent.version:
            log = self.query_one(RichLog)
            position = log.scroll_y
            rewriting = self.rewrite != agent.rewrite
            if rewriting:
                log.clear()
                self.last_sequence = None
            for seq, entry in agent.entries:
                if self.last_sequence is None or (isinstance(seq, int) and seq > self.last_sequence):
                    log.write(Text(entry), scroll_end=self.follow)
            if agent.entries:
                self.last_sequence = agent.entries[-1][0]
            if rewriting and not self.follow:
                log.scroll_to(y=position, animate=False, force=True)
            self.rewrite = agent.rewrite
            self.query_one('.public-summary', Static).update(agent.public_summary or 'Summary unavailable')
            missing = agent.usage_missing
            incoming = agent.input_tokens if agent.input_tokens is not None and not missing else '—'
            outgoing = agent.output_tokens if agent.output_tokens is not None and not missing else '—'
            self.query_one('.usage', Static).update(f'Tokens in {incoming} / out {outgoing} | {"Live" if self.follow else "Paused scroll"}')
            self.version = agent.version

    def on_focus(self):
        self.app.selected = self.key
        self.app.layout_panels()

    def action_detail(self):
        self.app.open_detail(self.key)

    def action_up(self):
        self.follow = False
        self.query_one(RichLog).scroll_up(animate=False)

    def action_down(self):
        self.follow = False
        self.query_one(RichLog).scroll_down(animate=False)

    def action_page_up(self):
        self.follow = False
        self.query_one(RichLog).scroll_page_up(animate=False)

    def action_page_down(self):
        self.follow = False
        self.query_one(RichLog).scroll_page_down(animate=False)

    def on_mouse_scroll_up(self):
        self.follow = False

    def action_follow(self):
        self.follow = True
        self.query_one(RichLog).scroll_end(animate=False)
        self.version = -1


class MASApp(App):
    TITLE = 'RAAC MAS'
    BINDINGS: ClassVar = [Binding('ctrl+e', 'prompt', 'Prompt', priority=True), Binding('ctrl+enter', 'start', 'Run', priority=True),
                Binding('ctrl+c', 'stop', 'Stop / Quit', priority=True),
                Binding('ctrl+q', 'quit', 'Quit', priority=True),
                Binding('ctrl+a', 'agents', 'Agents', priority=True),
                Binding('ctrl+o', 'overview', 'Overview', priority=True),
                Binding('ctrl+h', 'history', 'History', priority=True),
                Binding('ctrl+r', 'current', 'Current run', priority=True),
                Binding('ctrl+y', 'approval', 'Approvals', priority=True),
                Binding('question_mark', 'help', 'Help')]
    CSS = '''
    Screen { background: #191A23; color: #CBD2ED; }
    #workspace { height: 1; color: #8993BC; }
    #prompt { height: 5; max-height: 5; background: #1D1F2A; border: solid #41465B; }
    #controls { height: 3; }
    #approval-mode { width: 30; }
    #approvals.pending, #approvals.pending:hover, #approvals.pending:focus {
        background: #FFE066; color: #191A23; text-style: bold; border: tall #FFE066;
    }
    #expanded-prompt { height: 1fr; }
    Select { width: 1fr; height: 3; }
    Button { min-width: 8; width: auto; margin-right: 1; }
    #overview { height: 3; border: solid #41465B; background: #222435; }
    #dashboard { height: 1fr; }
    #grid { height: auto; grid-size: 2; grid-rows: 16; grid-gutter: 1; }
    AgentPanel { height: 16; border: solid #41465B; background: #1D1F2A; }
    AgentPanel:focus { border: solid #A6B4DC; }
    AgentPanel RichLog { height: 1fr; background: #1D1F2A; color: #CBD2ED; }
    .agent-header { height: 2; color: #A6B4DC; }
    .usage { height: 1; color: #8993BC; }
    #status { height: 1; background: #222435; }
    #resize-hint { display: none; height: 1fr; content-align: center middle; }
    ModalScreen { align: center middle; background: #191A23 80%; }
    .dialog { width: 90%; height: 85%; border: solid #A6B4DC; background: #1D1F2A; padding: 1; }
    .dialog Label { height: auto; }
    .dialog VerticalScroll, .dialog OptionList { height: 1fr; }
    .dialog Horizontal, .detail Horizontal { height: 3; }
    .dialog Button, .detail Button { min-width: 0; width: 1fr; margin: 0; }
    .detail { width: 95%; height: 95%; border: solid #A6B4DC; background: #1D1F2A; }
    .detail #events { height: 1fr; }
    .compact #prompt { height: 3; }
    .compact #overview { height: 1; border: none; }
    .tiny AgentPanel { border: none; }
    .tiny #workspace { display: none; }
    '''

    def __init__(self, *, workspace=None, prompt='', topology='chain', level='L3', log=None):
        super().__init__()
        self.workspace = Path(workspace) if workspace else None
        self.initial_prompt, self.topology = prompt, topology
        self.level = level
        self.read_only = log is not None
        self.bridge = RuntimeBridge()
        self.sources = {}
        self.view_positions = {}
        self.current_path = Path(log).resolve() if log else None
        self.view_path = self.current_path
        self.empty_state = RunState()
        self.panels = {}
        self.selected = None
        self.closing = False
        self.polling = False
        self.view_lock = asyncio.Lock()
        self.notice = ''
        self.notified_approvals = set()
        if self.current_path:
            self.sources[self.current_path] = [RunState(), 0]

    @property
    def dashboard(self):
        return self.screen_stack[0]

    @property
    def state(self):
        return self.sources[self.view_path][0] if self.view_path in self.sources else self.empty_state

    def compose(self) -> ComposeResult:
        yield Static(literal(str(self.workspace or self.current_path or 'RAAC')), id='workspace', markup=False)
        yield TextArea(self.initial_prompt, id='prompt', read_only=self.read_only)
        with Horizontal(id='controls'):
            yield Select(LEVEL_CHOICES, value=self.level, allow_blank=False, id='level', disabled=self.read_only, tooltip='Collaboration level; L4 is a mock with no model or tools')
            yield Select([(label, label) for label in ('chain', 'chain_review_loop', 'star')], value=self.topology, allow_blank=False, id='topology', disabled=self.read_only or self.level != 'L3', tooltip='Topology applies to L3 only')
            yield Button('Run', id='run', disabled=self.read_only)
            yield Button('Approvals', id='approvals')
            yield Select(APPROVAL_MODES, value='manual', allow_blank=False, id='approval-mode',
                         disabled=self.read_only, tooltip='Auto approve immediately allows pending and future operations for this TUI session')
        yield Static('Run overview — Ready', id='overview', markup=False)
        yield Static('Increase terminal to at least 40 columns × 15 rows', id='resize-hint', markup=False)
        with VerticalScroll(id='dashboard'):
            yield Grid(id='grid')
        yield Static('Ctrl+Enter Run · Ctrl+H History · Ctrl+Y Approvals · Ctrl+R Current', id='status', markup=False)
        yield Footer()

    async def on_mount(self):
        self.set_interval(0.05, self.poll)
        self.layout_panels()
        if self.read_only and not self.current_path.is_file():
            self.notice = 'Cannot read log; check the selected path'
        if not self.read_only:
            self.dashboard.query_one(TextArea).focus()

    @work(thread=True, exit_on_error=False)
    def runtime(self, bridge, prompt, topology, level):
        bridge.run(self.workspace, prompt, topology, level)

    def get_system_commands(self, screen):
        yield from super().get_system_commands(screen)
        for title, help_text, action in (
            ('Start run', 'Submit the current prompt', self.action_start),
            ('Select agent', 'Focus an activation', self.action_agents),
            ('Run history', 'Browse saved JSONL runs', self.action_history),
            ('Current run', 'Return from history', self.action_current),
            ('Pending approvals', 'Approve once, deny or inspect', self.action_approval),
            ('Approval mode', 'Ask each time or auto approve (Full access)', self.action_approval_mode),
            ('Change level', 'Select L1, L2, L3 or L4 Mock for the next run', self.action_level),
            ('Change topology', 'Choose the next run topology', self.action_topology),
            ('Expand prompt', 'Read or edit the full request', self.action_prompt),
        ):
            yield SystemCommand(title, help_text, action)

    def action_topology(self):
        if not self.read_only and not self.bridge.busy and self.dashboard.query_one('#level', Select).value == 'L3':
            self.push_screen(Picker('Topology for next run', [(s, s) for s in ('chain', 'chain_review_loop', 'star')]), self.set_topology)

    def set_topology(self, value):
        if value:
            self.dashboard.query_one('#topology', Select).value = value

    def action_level(self):
        if not self.read_only and not self.bridge.busy:
            self.push_screen(Picker('Level for next run', LEVEL_CHOICES), self.set_level)

    def set_level(self, value):
        if value and not self.read_only and not self.bridge.busy:
            self.dashboard.query_one('#level', Select).value = value

    @on(Select.Changed, '#level')
    def level_changed(self):
        self.update_level_controls()

    def update_level_controls(self):
        locked = self.read_only or self.bridge.busy
        self.dashboard.query_one('#level', Select).disabled = locked
        self.dashboard.query_one('#topology', Select).disabled = locked or self.dashboard.query_one('#level', Select).value != 'L3'

    def action_prompt(self):
        self.push_screen(PromptEditor(self.dashboard.query_one(TextArea).text, self.read_only or self.bridge.busy), self.set_prompt)

    def set_prompt(self, value):
        if value is not None:
            self.dashboard.query_one(TextArea).load_text(value)

    def action_start(self):
        if self.read_only or self.bridge.busy:
            return
        if isinstance(self.screen, PromptEditor):
            self.set_prompt(self.screen.query_one(TextArea).text)
            self.screen.dismiss(None)
        prompt = self.dashboard.query_one(TextArea).text
        if not prompt.strip():
            self.notify('Enter a prompt first')
            return
        self.bridge = RuntimeBridge()
        self.bridge.set_approval_mode(self.dashboard.query_one('#approval-mode', Select).value)
        self.notified_approvals.clear()
        self.bridge.busy = True
        self.notice = ''
        self.dashboard.query_one('#run', Button).disabled = True
        self.dashboard.query_one(TextArea).read_only = True
        self.update_level_controls()
        self.runtime(self.bridge, prompt, self.dashboard.query_one('#topology', Select).value,
                     self.dashboard.query_one('#level', Select).value)

    @on(Button.Pressed, '#run')
    def start_button(self):
        self.action_start()

    @on(Button.Pressed, '#approvals')
    def approval_button(self):
        self.action_approval()

    @on(Select.Changed, '#approval-mode')
    def approval_mode_changed(self, event):
        if not self.read_only:
            self.bridge.set_approval_mode(event.value)

    def action_approval_mode(self):
        if not self.read_only:
            self.push_screen(Picker('Approval mode', APPROVAL_MODES), self.set_approval_mode)

    def set_approval_mode(self, mode):
        if mode and not self.read_only:
            self.dashboard.query_one('#approval-mode', Select).value = mode

    async def poll(self):
        if self.polling:
            return
        self.polling = True
        try:
            metadata = self.bridge.metadata
            if metadata:
                path = Path(metadata['log'])
                if path not in self.sources:
                    self.sources[path] = [RunState(), 0]
                    self.current_path = path
                    await self.switch_view(path)
            while True:
                try:
                    self.bridge.notifications.get_nowait()
                except queue.Empty:
                    break
            active_paths = {p for p in (self.current_path, self.view_path) if p}
            for path in active_paths:
                source = self.sources.get(path)
                if source is None:
                    continue
                try:
                    events, end = await asyncio.to_thread(read_page, path, source[1], 250)
                except OSError:
                    continue
                source[1] = end
                for event in events:
                    source[0].apply(event)
            if not self.is_running:
                return
            async with self.view_lock:
                await self.refresh_agents()
            if not self.is_running:
                return
            approvals = self.bridge.approvals()
            count = len(approvals)
            if isinstance(self.screen, ApprovalDialog) and self.screen.token not in {token for token, _ in approvals}:
                self.screen.dismiss(None)
            button = self.dashboard.query_one('#approvals', Button)
            button.label = f'Approve ({count})' if count else 'Approvals'
            button.set_class(bool(count), 'pending')
            tokens = {token for token, _ in approvals}
            if tokens - self.notified_approvals:
                self.notify(f'{count} approval request(s) waiting — press Ctrl+Y to review',
                            title='Approval required', severity='warning', timeout=8)
            self.notified_approvals = tokens
            viewing = 'CURRENT' if self.view_path == self.current_path else 'HISTORY — Ctrl+R returns to current run'
            chosen = self.dashboard.query_one('#level', Select).value
            next_run = 'L4 MOCK: no model/tools' if chosen == 'L4' else chosen
            self.dashboard.query_one('#overview', Static).update(literal(f'{"Hub / " if self.state.level == "L3" else ""}Run overview | {self.state.level} {self.state.status} | {self.state.run_id or "No run"} | Next: {next_run}'))
            self.update_level_controls()
            mode = 'AUTO APPROVE / FULL ACCESS' if self.bridge.approval_mode == 'auto' else 'Ask each time'
            self.dashboard.query_one('#status', Static).update(literal(f'{viewing} | {mode} | Pending {count} | {self.notice or self.bridge.message}'))
            if not self.bridge.busy:
                self.dashboard.query_one('#run', Button).disabled = self.read_only
                self.dashboard.query_one(TextArea).read_only = self.read_only
                if self.closing:
                    self.exit()
        finally:
            self.polling = False

    async def refresh_agents(self):
        if not self.is_running:
            return
        grid = self.dashboard.query_one('#grid', Grid)
        new_panels = {key: AgentPanel(key) for key in self.state.agents if key not in self.panels}
        positions = self.view_positions.get(self.view_path, (None, {}))[1]
        for key, panel in new_panels.items():
            if key in positions:
                panel.follow = positions[key][0]
        if new_panels:
            await grid.mount(*new_panels.values())
            if not self.is_running:
                return
            self.panels.update(new_panels)
        if self.selected is None and self.panels:
            self.selected = next(iter(self.panels))
        for key, agent in self.state.agents.items():
            self.panels[key].render_agent(agent)
        self.layout_panels()

    def layout_panels(self, size=None):
        if not self.is_mounted or not self.is_running:
            return
        width, height = size or self.size
        compact = width < 80 or height < 24
        self.dashboard.set_class(compact, 'compact')
        self.dashboard.set_class(width < 50, 'tiny')
        too_small = width < 40 or height < 15
        self.dashboard.query_one('#resize-hint').display = too_small
        self.dashboard.query_one('#dashboard').display = not too_small
        self.dashboard.query_one('#controls').display = height >= 24
        columns = 1 if compact else 4 if width >= 160 else 3 if width >= 120 else 2
        grid = self.dashboard.query_one('#grid', Grid)
        grid.styles.grid_size_columns = columns
        panel_height = max(5, height - 10) if compact else 16
        grid.styles.grid_rows = str(panel_height)
        for key, panel in self.panels.items():
            panel.display = not compact or key == self.selected
            panel.styles.height = panel_height

    def on_resize(self, event):
        self.layout_panels(event.size)

    def open_detail(self, key):
        if self.view_path:
            self.push_screen(Detail(self.view_path, key))

    def action_agents(self):
        choices = [(f'{a.role} / {a.key[1][:10]} / {a.status}', key) for key, a in self.state.agents.items()]
        if choices:
            self.push_screen(Picker('Select agent', choices), self.select_agent)

    def select_agent(self, key):
        if key in self.panels:
            self.selected = key
            self.layout_panels()
            self.panels[key].focus()

    def action_overview(self):
        if len(self.screen_stack) > 1:
            self.pop_screen()
        self.dashboard.query_one('#dashboard').focus()

    async def switch_view(self, path):
        async with self.view_lock:
            if path is None:
                return
            if self.view_path is not None:
                self.view_positions[self.view_path] = (self.selected, {
                    key: (panel.follow, panel.query_one(RichLog).scroll_y) for key, panel in self.panels.items()
                })
            self.view_path = path
            self.sources.setdefault(path, [RunState(), 0])
            self.sources = {p: source for p, source in self.sources.items() if p in {path, self.current_path}}
            await self.dashboard.query_one('#grid', Grid).remove_children()
            self.panels = {}
            selection, positions = self.view_positions.get(path, (None, {}))
            self.selected = selection
            await self.refresh_agents()
            for key, (follow, scroll_y) in positions.items():
                if key in self.panels:
                    self.panels[key].follow = follow
                    self.panels[key].query_one(RichLog).call_after_refresh(
                        self.panels[key].query_one(RichLog).scroll_to, y=scroll_y, animate=False,
                        force=True, immediate=True)
            self.layout_panels()
            self.view_positions = {p: value for p, value in self.view_positions.items() if p in self.sources}

    def action_history(self):
        roots = [Path(__file__).resolve().parents[3] / 'results']
        self.push_screen(History(roots), self.switch_view)

    async def action_current(self):
        await self.switch_view(self.current_path)

    def action_approval(self):
        pending = self.bridge.approvals()
        if pending:
            token, request = pending[0]
            self.push_screen(ApprovalDialog(token, request), self.resolve_approval)
        else:
            self.notify('No pending approvals')

    def resolve_approval(self, result):
        if result is not None and not self.bridge.resolve(*result):
            self.notify('Approval expired or identity mismatch')

    def action_help(self):
        if isinstance(self.focused, TextArea):
            return
        self.push_screen(Picker('Keyboard help', [(s, None) for s in (
            'Tab / Shift+Tab: focus panels and controls', 'Enter: agent details; Esc: back',
            'j/k, PageUp/PageDown: scroll panel; Ctrl+L: follow', 'Ctrl+A: select agent; Ctrl+O: overview',
            'Ctrl+H: history; Ctrl+R: current run; Ctrl+Y: approvals',
            'Ctrl+Enter: run; Ctrl+C: stop confirmation; Ctrl+Q: quit', 'Ctrl+P: command palette')]))

    def action_stop(self):
        if self.bridge.busy:
            self.push_screen(Confirm('Stop the current run? File changes are retained.'), self.stop_confirmed)
        else:
            self.exit()

    def stop_confirmed(self, approved):
        if approved:
            self.bridge.cancel()

    def action_quit(self):
        if self.bridge.busy:
            self.push_screen(Confirm('Stop the run, wait for cleanup, and quit?'), self.quit_confirmed)
        else:
            self.exit()

    def quit_confirmed(self, approved):
        if approved:
            self.closing = True
            self.bridge.cancel()

    def on_unmount(self):
        if self.bridge.busy:
            self.bridge.cancel()
