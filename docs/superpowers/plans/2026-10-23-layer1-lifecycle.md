# Layer 1 Lifecycle Implementation Plan
> Execute inline with superpowers:executing-plans and TDD. No commits/pushes authorized.
Goal: reusable LangGraph single-agent lifecycle with durable same-workspace resume.
Spec: ../specs/2026-10-23-layer1-lifecycle-design.md and user-approved full implementation plan in chat.
- [x] Task 1: extract shared contracts; keep re-exports; move runner; graph Reason/Act/Observe/Finalize, preserving role validation, guards, tool cap and final-call behavior.
- [x] Task 2: six-state machine, invalid transition events, guard/cancel, revision-bound one-shot manual approval and auto-approval.
- [x] Task 3: versioned SQLite JSON checkpoints and explicit bounded workspace/tool runtime snapshots; restore per-run usage and deadlines; no credential persistence.
- [x] Task 4: intent/outcome journal, safe-boundary resume, deny unknown operations/workspace/config/identity changes; exclusive run lock; completed output replay without work.
- [x] Task 5: lifecycle tests, broad HTTP-mock regressions, Docker fixture, architecture/docs/checklist; independent final review. Keep benchmark restart semantics and A5 unresolved.
Verification: pytest tests/mas/unit tests/mas/integration tests/unit/registry tests/unit/test_benchmark_chain.py tests/unit/test_benchmark_runner.py tests/unit/test_benchmark_trace.py -q; pytest tests/integration/test_mas_docker.py -q with RAAC_DOCKER_TESTS=1 when daemon available.

## Public contracts and execution details

`AgentRunner` preserves the former DemoAgentRunner constructor, `__call__(role, request)`, `budget_guard` and `role_timeout`. Adds `auto_approve` and `resume(invocation_id, *, approval=None)`; results remain dict/string/RoleHandoff, suspension raises AgentPaused. ApprovalDecision binds invocation/revision/operation/argument hash/boolean decision. LifecycleError exposes fixed failure codes.

Task 1: test behavior first, move shared schema/parser/handoff into common/agent_contracts.py with old imports re-exported; move runner to layer1_lifecycle/agent.py, leave a facade. LangGraph executes reason/decide/act/observe/finalize. Preserve max four model tools, final-call no tools, malformed argument observations, patch reconstruction, environment proof and Tester real verification.

Task 2: implement six-state valid transition map and audit events. Guard before/after model and before operations, apply saved wall deadlines, keep Environment exemption. Manual approval pauses before tool; consume exact approval once; benchmark remains auto-approve with tool permissions unchanged.

Task 3: SQLite FULL synchronous transactional JSON snapshots at lifecycle.sqlite3 beside the log. Store original invocation baselines, turn, messages, operations/receipts, usage completeness/cache, workspace bytes/modes/attribution, environment config/verification, evidence and observation refs. Bound snapshot size and paths. Redaction changing executable data disables resume; never store clients or credentials.

Task 4: intent before outbound work, committed outcome before phase advance. Block any unknown in-flight outcome and never automatically rerun side effects. Validate run/task/context/config/source/registry/model/workspace/memory, restore state and bind callbacks on new runner, restore trace sequence without recount. Only latest activation resumes; completed output is returned unchanged. Exclusive run lock prevents concurrent progress. No fresh budget/deadline and no Docker rebuild.

Task 5: test state edges/rejections, bad schema/IDs/size, guards/cancel, multi-tool safe boundaries, finalization replay, approvals/mismatch/rejection, redaction, usage restore, new runner and real same-container restore. Run original regression and full pytest; report baseline Analyst/Planner prompt failures separately. Update checklist without marking Layer4/Engine/A5 done. Fresh reviewer checks whole diff; fixes require reproducing tests.

Rulings from implementation are in 2026-10-23-layer1-lifecycle-progress.md. No commit or push is authorized. Save the design and plan; leave implementation reviewable as uncommitted changes.

## Verification evidence (2026-10-23)

- Original broad baseline: 444 passed +159 subtests, four existing Analyst/Planner prompt failures.
- New lifecycle files: 35 passed (4.12 s).
- Full pytest run before final edge tests: 636 passed, 34 skipped, 159 subtests, same four baseline prompt failures.
- Final full suite deselecting exactly those four baseline failures: 638 passed, 35 skipped, 4 deselected, 159 subtests (75.02 s).
- Real Docker fixture: 16 passed (93.05 s), including same-container new-runner resume/export.
- Fresh reviewer: two P2 issues (receipt IDs across turns; interrupting deadlines) reproduced, fixed and rechecked with six loop tests passing. No other confirmed blocker.
- Core lifecycle/shared contracts/usage Ruff passed; six existing Layer3 warnings match HEAD baseline. No model call, commit or push.

## Follow-up approved 2026-10-23

The user explicitly requested removal of the DemoAgentRunner compatibility facade. This supersedes the earlier facade requirement: delete harness/demo_agent.py and migrate CLI, benchmark and tests directly to raac.harness.layer1_lifecycle.AgentRunner. Keep collaboration Protocol naming and runtime behavior unchanged. Historical entries above describe the initial implementation.
