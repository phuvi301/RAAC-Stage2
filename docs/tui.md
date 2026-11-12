# RAAC local MAS TUI

Install the optional UI (Python 3.11 or newer):

```powershell
python -m pip install -e ".[tui]"
raac-mas tui --workspace "C:/work/my-project"
raac-mas tui --workspace "C:/work/my-project" --prompt "Fix the failing test"
raac-mas tui --workspace "C:/work/my-project" --prompt-file "C:/work/request.txt"
raac-mas show-log --log "C:/path/to/llm.jsonl" --tui
```

The module entry point also works: `python -m raac.mas_cli tui ...`.
For a pinned development environment, install `requirements.lock`, then install
this project editable with `--no-deps --no-build-isolation`. The lock includes
the `dev` and `tui` extras. Existing CLI commands import no Textual modules.

Enter a prompt, select a level, then press **Ctrl+Enter**. Default level is L3.

| Level | Behavior |
| --- | --- |
| L1 Fixed | Existing Engine runs Analyst → Planner → Coder → Tester → Reviewer once. |
| L2 Routing | Existing Router chooses the roles, retaining Coder and Tester; skipped roles show their reasons. |
| L3 Collaboration | Choose `chain`, `chain_review_loop`, or `star`; default topology is `chain`. |
| L4 Mock | Preview only: no model request, tools or workspace edits; result is `mock`, with no PASS verdict. |

Topology selection applies only to L3. Level and topology are locked while a run
is active. **Ctrl+P → Change level** also works when the terminal hides the
controls. The overview shows the current run's level and the next selection;
history retains the recorded level. Only one run can edit the workspace
at a time. After it finishes, edit the prompt and submit another request.
Workspace files are edited directly with the existing role permissions.

| Key | Action |
| --- | --- |
| Ctrl+Enter | Start the request |
| Ctrl+E | Expand/read/edit the full prompt |
| Tab / Shift+Tab | Move focus through controls and agent panels |
| Ctrl+A | Select an agent activation, including off-screen agents |
| Enter on a panel | Open paged events; Enter/Space expands an event |
| Esc | Close an overlay; closing approval leaves it pending |
| j / k, PageUp / PageDown | Scroll the focused panel and pause follow |
| Ctrl+L on a panel | Resume following live output |
| Ctrl+O | Return to the overview |
| Ctrl+H | Browse local run history, 20 runs per page |
| Ctrl+R | Return to the current run |
| Ctrl+Y | Open pending approvals |
| Ctrl+P | Command palette, including level, topology and prompt actions |
| ? outside the prompt editor | Help |
| Ctrl+C | Confirm cancellation; quit when idle |
| Ctrl+Q | Confirm stop and wait for cleanup before quitting |

Approval shows the role, tool, workspace and redacted arguments. **Approve once**
authorizes the complete operation, including all steps in a command batch or
configure/provision operation. **Deny** fails the operation. Runtime validation
still checks invocation, revision, operation and argument hash. Decisions cannot
be reused. Cancellation releases approvals and waits for subprocess cleanup.
It retains workspace changes; it does not reset Git or undo edits.

The overview labels the Hub for L3 topologies. Every runtime activation has
its own panel even when roles repeat. Status and input/output tokens come from
runtime events and provider usage; unavailable usage is shown as `—`. There is
no fabricated progress percentage or hidden reasoning. Local TUI requests stream
public output by default. Before the first delta, the panel shows model wait time.
Content disabled by the
existing `trace_content` policy stays unavailable.

Each LLM call updates one output block; its final snapshot is retained in JSONL
for comparison and is not echoed in the panel. Public summary has a separate
collapsed section. The current 9router Chat Completions endpoint has no verified
public-summary mapping, so it displays **Summary unavailable**. Arbitrary
`reasoning`, `reasoning_content`, and unverified summary fields are ignored.
There is no extra model request to manufacture a summary.

Each panel follows independently. Scrolling up pauses that panel; new events
continue to arrive without moving its reading position. Switching agents,
resizing and returning from history preserve selection and scroll/follow state
within the retained activity window. Detail pages scan up to 200 journal records
at a time, with individual events collapsed by default; **Refresh** reloads a
page of a growing log. Tool arguments, results, stdout/stderr, errors and diffs
remain in their original structured events. Detail/history views do not pause
the runtime. The current-run indicator and pending approval count remain visible
on the dashboard, including when browsing history.

Width breakpoints use 4/3/2 columns at 160/120/80 columns respectively. Narrower
or shorter-than-24-row terminals focus one selected agent. Below 50 columns,
headers and borders are reduced. Below 40 columns or 15 rows, a resize hint is
shown. Ctrl+A and the command palette make hidden controls accessible.

`show-log --tui` is read only and follows appended complete JSONL records. It
cannot approve, restart or resume a recorded run. History searches only the
codebase's `results/` directory. All local run artifacts, including L4 mock runs,
are saved there, relative to the installed source location rather than the working
directory or user profile. When the workspace is the codebase itself, artifacts
are inside that workspace. Existing logs in the old user-profile location are not migrated.

## Integration and limits

`run_local(..., level="L3", stream=False, event_handler=callback, run_handler=callback)` adds optional
callbacks without importing Textual into the harness. `run_handler` receives
`run_id`, `log`, `workspace`, `level`, and `topology` after initialization; `event_handler`
receives isolated post-redaction records after JSONL persistence. Subscribers
should return promptly. A subscriber failure cannot fail the run.

The MAS runs in a Textual thread worker with its own asyncio loop. UI mutations
stay on the Textual thread, following the [official worker guidance](https://textual.textualize.io/guide/workers/).
The bridge queues at most 256 wake notices and manages approvals separately.
The UI batches at 50 ms, reading JSONL by byte cursor in background I/O threads;
overflow catches up from the journal without dropping events or blocking MAS.
Only the current and viewed runs retain state. Each activation retains at most
1,000 activity previews of at most 2,048 characters, with streamed output blocks
retaining the last 65,536 characters per call; full detail comes from disk.
A read batch stops after 250 records or about 4 MiB (one larger record can exceed
that byte target). Agent panels redraw activity only when their state changes.

Command pipes decode UTF-8 incrementally and publish complete lines while the
process runs, flushing the final partial line on completion/cancellation.
An incomplete line or private-key block is buffered so redaction cannot expose
credentials split between reads. Known configured secrets spanning line breaks
are also held until redaction is safe. Existing command timeout, total output
limit, exit status and process-tree cleanup remain enforced. Displayed content
is literal text with terminal control sequences removed.

The LLM client reads incremental UTF-8 SSE with HTTPX, requests final provider
usage through `stream_options.include_usage`, and assembles content/tool-call
fragments by index. Only a finished, valid completion reaches the existing
DSML normalization, lifecycle validation and approval gates. SSE requires a
finish reason and either `[DONE]` or clean HTTP EOF with complete usage;
a response is capped at 16 MiB. Missing usage marks
accounting incomplete and blocks further calls, without estimating tokens.
Malformed/interrupted streams are not retried or switched to non-streaming.
HTTP failures before completion data retain the existing retry/usage policy.
Cancellation closes the HTTP stream and retains runtime cleanup.

9router 0.5.112 can place usage on the finish-reason chunk instead of an
empty-choices trailer; both forms are accepted. Its repeated full tool-call IDs
are kept once while argument fragments are concatenated. Null no-op SSE frames
and nullable optional role/tool-call fields are accepted. Its Codex stream
transform consumes upstream `[DONE]` without forwarding it. The client accepts
clean EOF after an explicit finish reason and valid prompt/completion usage.
Missing finish/usage, unfinished SSE frames and read errors still fail; receiving
the finish/usage chunk alone never executes a tool before HTTP closes. Valid
output and complete usage remain required before tool execution. Parser
failures log a fixed `response_error` code (for example `invalid_stream_chunk`,
`incomplete_stream` or `upstream_stream_error`), without logging provider error
bodies or hidden reasoning. After updating the client, restart the TUI process
to load the new parser; 9router itself does not need restarting for this fix.

Output deltas are redacted before JSONL persistence and subscriber delivery.
Incomplete known secrets/credential markers and terminal escape sequences are
held across chunks; credential-bearing tails can remain buffered until stream
completion. An interrupted buffered tail is discarded. Stream events identify
run, activation, LLM call, attempt and chunk order. With `trace_content=False`,
only stream lifecycle metadata and provider usage are emitted. CLI and benchmark
requests continue to use `stream=False`; only the local TUI opts in.

The TUI uses the declared backend topology. L3 starts at Hub and no topology
schedules Environment. Interleaved-event stress tests establish UI
behavior, not parallel backend execution. There is no hidden
reasoning, automatic topology selection, Requirement Analyzer, Decision Engine,
or whole-MAS execution recovery after closing the application. Commands retain
the user's OS privileges; approval is not an operating-system sandbox.

## Verification

```powershell
python -m pytest tests/mas/unit/test_token_streaming.py tests/mas/unit/test_tui.py tests/mas/integration/test_tui_local.py -q
python -m pytest tests/mas/unit/test_local_levels.py -q
```

Tests use [Textual run_test/Pilot](https://textual.textualize.io/guide/testing/),
HTTP mocks and real commands in temporary workspaces. They cover seven design
sizes (200×55, 160×45, 120×35, 100×30, 80×24, 60×20, 40×15), resizing, activation
identity/replay, queue overflow, scroll/history, approval identity and duplicate
resolution, UTF-8/redaction/control characters, timeout/output limits, and
cancellation during approval or command execution. No paid model call is needed.
SSE tests also cover single-byte UTF-8/framing splits, heartbeat, fragmented tools,
final usage, malformed/truncated streams, timeout, cancellation, secret/control
splits, replay and visible output before HTTP completion. Provider compatibility
is checked offline with HTTP mocks and a synthetic fixture produced by the
installed 9router 0.5.112 mapper/ID normalizer/SSE serializer and full Codex
stream transform, including its EOF without `[DONE]`. Fixture provenance
is recorded in `tests/mas/unit/fixtures/9router-sse.md`. No paid endpoint
acceptance run was made; the historical failed runs were not replayed.
Level tests use HTTP mocks through the real L1/L2 Engine and lifecycle, edit a
temporary file, run actual unittest suites after approval, and verify denial
prevents execution. L4 tests forbid client/executor/workspace construction and
verify that the saved result and history are explicitly mock. L4 orchestration
itself remains unimplemented.
