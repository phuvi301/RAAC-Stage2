# SDD ledger — plan: docs/superpowers/plans/2026-10-23-layer1-lifecycle.md
Pre-flight: tasks 1-2 share event loop/state; tasks 3-4 share committed state/runtime snapshot. Keep resume API separate from collaboration.
Ruling: work in managed isolated worktree using original venv + PYTHONPATH=worktree/src, no dependency installs — same locked environment, no accidental imports from original checkout.
Ruling: no commit steps — explicit project/user restriction overrides skill defaults.
Ruling: implement inline with one final fresh reviewer, to minimize duplicate context costs.

Task 1: complete — shared contracts extracted with identity-preserving reexports; DemoAgentRunner facade; LangGraph reason/decide/act/observe/finalize. Initial RED lacked lifecycle transitions/checkpoints, GREEN recorded.
Task 2: complete — six states, guards and approval API, with manual rejection/mismatch and persistent events covered.
Task 3: complete — versioned strict JSON SQLite state, runtime snapshots, baseline and per-run usage restoration. Initial RED missing transition/store/snapshot methods, GREEN recorded.
Task 4: complete — 14 resume tests (safe boundaries, adapter patch, Tester finalization, unknown side effects, memory, budget/deadline, redaction, lock). Original RED resume API absent. Reviewer findings receipt namespace and interrupting deadline reproduced before fixes.
Ruling: JSON strings normalized by trace redaction are compared semantically; only actual data redaction blocks resume — raw comparison incorrectly rejected harmless formatting.
Ruling: per-run locking converts only lock-acquisition timeouts, not tool/deadline timeouts — preserving failure classification.
Ruling: only latest activation may resume; new activations restore prior run usage; never reset budget by reconstructing client.
Ruling: respect existing Environment timeout exemption; enforce saved Docker/run/role deadlines with asyncio.timeout.
Ruling: Docker first test attempt ran while source was edited and invalidated its batch fingerprint; rerun after source freeze, 16/16 passed.
Task 5: complete — full framework 638 passed +159 subtests with four known prompt failures deselected, 35 skipped; Docker16 passed, core Ruff/diff clean. Fresh read-only reviewer confirmed fixes. Docs/A1 updated, Layer4 connector and A5 remain incomplete.
Ruling: implementation copied back as uncommitted changes only after clean-original verification; managed worktree retained for review, no archive snapshot or commit/push.

## Follow-up approved 2026-10-23

The user explicitly requested removal of the DemoAgentRunner compatibility facade. This supersedes the earlier facade requirement: delete harness/demo_agent.py and migrate CLI, benchmark and tests directly to raac.harness.layer1_lifecycle.AgentRunner. Keep collaboration Protocol naming and runtime behavior unchanged. Historical entries above describe the initial implementation.
