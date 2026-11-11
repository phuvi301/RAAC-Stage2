# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

RAAC (`raac-agent`) is a Python ≥ 3.11 / LangGraph research framework that compares four multi-agent **coordination levels** (L1 fixed, L2 routing, L3 collaboration, L4 team formation) on a fixed SWE-bench-Verified subset. Most docs, comments and the checklist are written in Vietnamese.

## Mandatory project rules (from AGENTS.md / `.agents/rules/progress.md`)

- Before starting work, read `.agents/rules/progress.md` and `docs/implementation_checklist.md`. The checklist is the main progress tracker. **Update it in the same turn** when you finish or change the status of an item: `[ ]` not started / `[~]` partial (note what's left) / `[x]` done *and* reviewed by someone or proven by a test. Add evidence under the checkbox (date YYYY-MM-DD, who/agent, files, command + result), bump "Cập nhật gần nhất ngày …", and append decisions/issues to "Nhật ký vấn đề và quyết định" as `date | issue/decision | owner | status`.
- Scaffolds, TODOs and stubs are **not** implemented features. Mock-LLM results do not satisfy items that require a real API, Docker, real tasks or a pilot. Never claim a teammate reviewed/approved something without evidence. Keep the owner names and the week 8–9 structure as they are.
- **Never `git commit` or `git push` unless the user explicitly asks for that specific action.** Permission to commit does not include permission to push, and the reverse is also true.
- Experiment constraints: in benchmark runs, RAG/long-term memory is off, short-term memory is reset after each run, and resume only uses data from the same run. Every result record must trace `registry/VERSION` plus the version and hash of each config used, with a secret-free snapshot of the effective config.
- Connectors (Layer 4) stay at the interface level: add a minimal MCP adapter only if an external tool is truly needed, and keep A2A a stub. WASM sandbox and long-term memory are stubs by design.

## Commands

Setup (the lock file pins everything; install it before the editable package):
```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\Activate.ps1
python -m pip install -r requirements.lock
python -m pip install --no-deps --no-build-isolation -e .
```

Tests (Make picks the `.venv` Python automatically on Windows/Linux):
```bash
make test                                   # = python -m pytest (testpaths = tests)
make test TEST_ARGS="-q tests/unit/collaboration/test_engine.py"
python -m pytest tests/mas/unit/test_lifecycle_state.py::test_name -q   # single test
python -m pytest tests/mas/unit/test_lifecycle_*.py -q                  # Layer 1 lifecycle suite
RAAC_DOCKER_TESTS=1 python -m pytest tests/integration/                  # real-Docker tests (skipped otherwise)
node --test tests/mas/web/                                               # trace-viewer JS tests (.mjs)
```
Tests use the mock LLM. They need no live model. `ruff` and `radon` are in the dev extras, but no lint config or target is defined.

CLIs (`raac` = `raac.cli`, `raac-mas` = `raac.mas_cli`; you can also run `python -m …` with `PYTHONPATH=src`):
```bash
# Docker sandbox (independent of LLM/collaboration). Requires Docker in Linux-container mode.
raac sandbox check | profiles check
raac sandbox prepare --task-id psf__requests-6027
raac sandbox run --task-id psf__requests-6027 [--patch candidate.patch] [--test-mode suite|issue]
raac sandbox preflight → raac sandbox validate --stage pilot → raac sandbox validate --stage full --reuse-validated
raac sandbox batch --batch-id ID [--resume]        # results/batches/<id>/summary.{json,csv}
make pilot TASK=psf__requests-6027                 # sandbox validate --stage pilot (not an agent pilot)
make freeze                                        # config/registry snapshot + SHA-256 → results/freezes/

# MAS (L1 Fixed / L2 Routing / L3 Chain share one benchmark lifecycle)
raac benchmark run --task-id psf__requests-6027 --level L1|L2|L3    # candidate in Docker, evaluated independently
raac benchmark batch --task-id A --task-id B --level L2 --repetitions 2 --batch-id X [--resume]
raac-mas models                                                     # list models at configured endpoint
raac-mas run-chain --project-dir PATH [--model ID] [--max-rounds N] # local run on a repo + user chat
raac-mas show-log --run-id RUN_ID [--follow] [--details] [--agent coder] [--web]
```
Updating dependencies: `uv pip compile pyproject.toml --extra dev --universal --python-version 3.11 -o requirements.lock --no-annotate`, then re-run the tests.

## Architecture

Two orthogonal concepts. Don't mix them up:

1. **Harness: 4 layers wrapped around *each* agent** (`src/raac/harness/`, design in `docs/architecture.md`):
   - `layer1_lifecycle/`: `AgentRunner` runs one role invocation as a LangGraph loop (Reason → Decide → Act → Observe → … → Finalize) with a state machine (IDLE/PLANNING/EXECUTING_TOOL/WAITING_HUMAN_APPROVAL/FAILED/COMPLETED). It keeps durable checkpoints in `lifecycle.sqlite3` next to `llm.jsonl`, plus an operation journal, and approval (auto-approve for benchmarks). Resume is strict: same workspace/container and same config/registry. In-flight operations are never replayed (`resume_unsafe_inflight`). See `docs/layer1_lifecycle.md`.
   - `layer2_core/`: context manager, prompt hydrator, short-term memory (long-term/RAG is a stub).
   - `layer3_execution/`: `ToolOrchestrator` is the **only** path to workspace/sandbox I/O. It runs tools (`tools/`) through `local_executor` or `docker_execution`, applies guardrails, and enforces cost control. `sandbox/` is the standalone SWE-bench Docker pipeline: dataset → repo profile → git mirror cache (`data/repos/`) → image build/cache → baseline/candidate run → F2P/P2P issue evaluation → record. It has its own batch manifest/checkpoints, separate from the agent and collaboration checkpoints.
   - `layer4_connectors/`: interfaces only (MCP minimal, A2A stub).
2. **Collaboration: L1–L4 coordination levels** (`src/raac/collaboration/`):
   - `engine.py`: the shared `Engine` builds a level graph via `graph_builder.build_graph` and runs it with its own LangGraph checkpointer (thread_id = run_id). It invokes roles through `AgentRunner`. Collaboration state (`state.py`: plan, patch, messages, rounds) is kept separate from Layer 1 per-agent state.
   - `levels/base.py`: a level implements `build_graph(task, registry, config) -> LevelGraph` (nodes, edges, conditional_edges, entry/exit). `topologies.py` holds static `TopologySpec`s. Topologies are never created from LLM output.
   - Status: **L1 Fixed** (`levels/l1_fixed.py`: fixed workflow `Requirement Analyst → Architect → Planner → Developer ⇄ Tester ⇄ Code Reviewer → finish` with `max_rounds = 3`, early stop on Code Reviewer approval, mandatory final-round review and `completed_with_failures` when tests still fail, runs through the shared Engine), **L2 Adaptive Routing** (`levels/l2_routing.py`: Router selects a subset of `L2_CATALOG` keeping Coder + Tester, with conditional bypass edges and L1 fallback on invalid output) and **L3 Chain** (`levels/l3_collaboration/`: Environment → Analyst → Planner → Coder ⇄ Tester → Reviewer, fixed config-driven coordinator) are implemented. `l4_team_formation.py` is still an empty placeholder. The L3 chain uses its own graph (`chain_graph.py`/`chain_nodes.py`) and is not yet routed through the shared Engine.

Supporting packages:
- `registry/` (data) + `src/raac/registry/` (pydantic loader): roles in `registry/roles/core/*.yaml` (tools allowlist, prompt file, `prompt_version`, `model.ref`, `max_llm_calls`), prompts in `registry/prompts/`, tool catalog in `registry/tools.yaml`, version in `registry/VERSION`. If you change role, prompt or tool data, bump the relevant versions so provenance stays meaningful.
- `src/raac/common/agent_contracts.py`: strict pydantic role output schemas (`ROLE_OUTPUT_MODELS`). The Coder outputs a unified diff, and the other roles output JSON.
- `src/raac/llm/`: the single LLM client (OpenAI-compatible endpoint from `configs/llm.yaml`, with retry/backoff and usage logging to JSONL). `.env` `LLM_MODEL` overrides the top-level model; per-role `profiles` map `model.ref`.
- `src/raac/benchmark/runner.py`: candidate benchmark shared by all implemented levels; `LEVELS`/`level_agent` pick the level config and agent adapter (`l1_runner.run_l1`, `l1_runner.run_l2`, `chain_runner.run_chain`), everything else (setup, export, evaluation, batch/resume) is common. The agent works in a no-network container with no gold patch or evaluator tests. The host exports `candidate.patch` from the Coder's real changes under the `implementation-only-v1` contract (the Coder may not edit tests, and protected-test edits fail with `protected_test_error`). A fresh evaluator container then grades F2P/P2P. Output goes to `results/benchmark-<id>/` (`record.json`, `agent-result.json`, `llm.jsonl`).
- `src/raac/common/trace_*` + `trace_assets/`: terminal/web log viewer used by `show-log`.

Data and config:
- The benchmark set is fixed at **105 tasks / 12 repos** (`data/tasks/benchmark105-v1.json`, drawn from `cleaned_benchmark.json`'s 120). `configs/benchmark*.yaml` all default to it. The score is `100 × resolved / 105`.
- Evaluator data (`data/evaluation/swe_bench_verified.json`: gold/test patches, F2P/P2P) is kept separate from the agent-facing Task and must never reach the agent.
- `configs/`: `llm.yaml`, `harness.yaml` (token limit, timeouts, context/memory/execution), `levels/*.yaml`, `repos/*.yaml` (per-repo sandbox profiles), `benchmark*.yaml`.
- `results/`, `data/repos/` and `currentProject/` hold generated or local artifacts.
