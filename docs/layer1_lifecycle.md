# Layer 1 — single-agent lifecycle

Layer 1 owns one role invocation, not collaboration topology or Coder–Tester rounds. Local CLI and benchmark callers import `AgentRunner` directly from `raac.harness.layer1_lifecycle`. The former demo facade and its import path have been removed.

## Flow and states

LangGraph routes Reason → Decide → Act → Observe → Reason; final output goes through Finalize. Act executes one model tool at a time, in response order. Adapter calls such as Coder apply_patch and Tester verification use the same operation journal and approval gate.

States: IDLE → PLANNING; PLANNING → EXECUTING_TOOL / WAITING_HUMAN_APPROVAL / COMPLETED / FAILED; EXECUTING_TOOL → PLANNING / FAILED; WAITING_HUMAN_APPROVAL → EXECUTING_TOOL / FAILED. Terminal states cannot restart. `lifecycle_transition` and rejection/pause/resume events remain visible in JSONL even when content tracing is disabled.

COMPLETED means the role produced a validated handoff. It does not mean benchmark resolved. Run token usage is shared across agents; model-call caps remain per invocation. Persisted wall deadlines are not refreshed on resume. Environment retains its existing role timeout exemption; a Docker run deadline still applies.

## API

```python
from raac.harness.layer1_lifecycle import AgentRunner, AgentPaused, ApprovalDecision

runner = AgentRunner(registry, client, tools, provenance=provenance)
result = await runner(role, request)
```

The constructor accepts the existing context/memory policies and token limit. `auto_approve=True` is the benchmark/default behavior. Use `auto_approve=False` for manual approval; it pauses before each tool, including adapter validation. Auto-approval never bypasses tool permissions or the sandbox.

```python
try:
    result = await runner(role, request)
except AgentPaused as paused:
    state = runner.checkpoints.load(paused.invocation_id)
    pending = state["pending_approval"]
    decision = ApprovalDecision(
        invocation_id=paused.invocation_id,
        revision=paused.revision,
        operation_id=pending["id"],
        arguments_hash=pending["arguments_hash"],
        approve=True,
    )
    result = await runner.resume(paused.invocation_id, approval=decision)
```

Each approval is for one operation at exactly that revision. A subsequent tool can raise AgentPaused again. A rejection marks the invocation FAILED; mismatched approval changes nothing. The collaboration adapter does not consume AgentPaused as a successful role response; manual collaboration/CLI orchestration is not implemented here.

## Durable checkpoints

The runner creates `lifecycle.sqlite3` and a per-run lock beside its `llm.jsonl`. SQLite stores versioned JSON, including messages, model turns, operation receipts, usage, workspace baseline, attribution, environment binding/evidence, observations and memory identity/revision. This can contain source and task content; treat it as a local run artifact, not a shareable public report. Credentials are excluded/redacted. If required execution data was redacted, normal execution may finish but that checkpoint cannot resume.

For resume, construct a new client/runner against the same log directory and same workspace/container, then call `await runner.resume(invocation_id)`. Restore uses the original baseline and counters, not a baseline recomputed from modified source. Invocation IDs are exposed by lifecycle events and the checkpoint API. Only the latest activation in a run may resume; COMPLETED returns its stored output without another LLM/tool call. Starting another activation restores the run counters instead of resetting budget.

Resume rejects incompatible code/config/registry/model/policy, altered workspace content, missing container, changed memory, expired deadlines, unknown schema, terminal failures or uncertain in-flight operations. The caller must preserve the same workspace and runtime; no external mutations or concurrent executions are supported.

Intent is committed before an LLM/tool call. Outcome, usage and runtime are committed before advancing the graph. A process crash between side effect and outcome commit is deliberately **not** replayed: `resume_unsafe_inflight`. No exactly-once guarantee is claimed for external tools. A safe committed boundary can resume after interruption; budget failures and ambiguous operations cannot.

A saved model/tool outcome can be reused, including within multi-tool responses or adapter finalization. Provider tool IDs are scoped by turn and index, so later reuse cannot return stale results. Execution evidence remains adapter-owned and is not recreated from LLM claims.

## Boundaries and verification

No new connector backend, CLI resume command, whole-collaboration resume or Docker reconstruction is included. Benchmark batch --resume continues to restart attempts in fresh environments. Layer 2 context policy and L3 topology behavior are unchanged. Shared wire contracts are in `common/agent_contracts.py`; old collaboration import paths re-export the same objects.

Run the lifecycle tests with `python -m pytest tests/mas/unit/test_lifecycle_*.py -q` (expand filenames if the shell does not expand globs). Run all framework tests with `python -m pytest -q`; enable real Docker fixtures using `RAAC_DOCKER_TESTS=1` and run `tests/integration/test_mas_docker.py`. No live-model call is needed for these tests.
