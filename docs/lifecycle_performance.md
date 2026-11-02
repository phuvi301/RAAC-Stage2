# MAS lifecycle performance — 2026-10-26

## Coder follow-up — 2026-10-27

The shared lifecycle now requests one output-format correction per activation for
invalid final JSON/diff or DSML text. Recovery consumes the existing call/token/time
limits, persists its quota through resume and never executes DSML. Adapter side
effects are outside this retry boundary. Coder receives a bounded progress hint
(three recent evidence receipts, tracked revision, calls/time left), with a handoff
reminder at 60 seconds. This is guidance, not an automatic success or forced stop.
Registry is `0.19.2`, Coder `chain-v6`, Tester `chain-v5`.

The Coder fixture performs one implementation edit, six read/shell cycles and a
final patch handoff: eight HTTP mock responses and thirteen top-level tools. The
repository contains 1,901 files / 40,996,803 baseline bytes. Three new containers per
version use the same local image `404ac6a56760`, with no provider request or image pull.
The control loads the five lifecycle/workspace modules from `HEAD` in memory; it
does not change working files, prompts, topology or configuration.

| Metric | HEAD control | After |
| --- | ---: | ---: |
| Median lifecycle | 70.787 s | 48.583 s |
| Median cumulative checkpoint | 24.936 s | 13.713 s |
| Snapshot calls | 28 | 16 |
| Checkpoint saves | 78 | 78 |

Total fixture time decreases **31.4%**; checkpoint overhead decreases **45.0%**.
The improvement comes from reusing the verified post-command snapshot for evidence
and subsequent checkpoint fingerprinting, plus removing a redundant deep copy before
synchronous checkpoint serialization. Every shell still receives fresh before/after
integrity checks; file mutations, failures/cancellation and new activations invalidate
the cache. Resume and candidate export still read fresh source. Local workspaces
retain their fresh-snapshot behavior. SQLite durability/revision checks are unchanged.

Raw measurements: `results/lifecycle-coder-control-20261027/summary.json` and
`results/lifecycle-coder-after-20261027/summary.json`. Reproduce with:

```powershell
.venv/Scripts/python.exe scripts/profile_lifecycle.py --image 404ac6a56760 --scenario coder --control-ref HEAD --output results/coder-control-new
.venv/Scripts/python.exe scripts/profile_lifecycle.py --image 404ac6a56760 --scenario coder --output results/coder-after-new
```

The default `--scenario analyst` retains the original two-response fixture. Output
directories must be new. Detailed `checkpoint_timing` records separate runtime/memory
snapshot, redaction, serialization, SQLite and payload bytes. `command_timing` records
before/after snapshot and execution; `evidence_timing` measures patch evidence. Batch
totals count only top-level tools. Events contain metrics, not source or command output.

A separate instrumented Coder run (`results/lifecycle-coder-timing-20261027/llm.jsonl`)
verified six command timing records and sixteen snapshots, finishing in 46.146 s.
Each tiny shell execution takes 0.317–0.422 s while its before/after snapshots take
about 1.58–2.00 s each. This explains why even a small command visibly takes several
seconds; those integrity checks remain necessary.

Context replay of the two latest traces is stored in
`results/token-reduction/2026-10-27-lifecycle-replay.json`; source hashes were rechecked
unchanged. The newest trace is already compacted (363,409 estimated input tokens
across 27 requests); replaying existing policies does not reduce it further. The prior
trace decreases from 642,723 to 467,040 under compact/filtered; concise adds input
(468,456). Defaults and full raw evidence remain unchanged.

These measurements do not estimate live-model speedup or solve rate. Browser UI
automation was unavailable in this session; HTTP viewer and Node tests cover run
selection, automatic following, pinned history and existing memory/usage behavior.


## What changed, in order

1. Avoid saving an identical Reason outcome twice. Keep intent/outcome commits and tool observation advancement.
2. Reuse baseline encoding and secret checks while baseline content and credentials are unchanged. Redact all changing state on every save.
3. Reuse Docker workspace fingerprints between non-mutating activities. Invalidate on mutations, failures/cancellation and new activations; always validate fresh content on resume and candidate export.
4. Tell Analyst/Planner to batch known independent file reads and reuse visible context. Registry is `0.19.1`; both prompt versions are `chain-v6`. No model, topology, budget or retry changes.

In plain language: avoid repeatedly inspecting and copying the whole repository just to record that an agent moved to its next activity. Do not remove the checks around actual changes.

## Offline comparison

Three fresh Docker containers per version, same installed Astropy image (`404ac6a56760`), 1,901 baseline files / 40,992,643 bytes. Each invocation uses the same two HTTP mock responses and one `read_files` batch. No provider request or token charge is involved.

| Metric | Before | After |
| --- | ---: | ---: |
| Median lifecycle time | 109.823 s | 12.399 s |
| Median cumulative checkpoint time | 106.260 s | 8.743 s |
| Checkpoint saves per invocation | 14 | 12 |
| Workspace snapshots per invocation | 15 | 2 |

Checkpoint overhead decreases **91.8%**, exceeding the 50% target; total fixture time decreases **88.7%**. The important change is doing expensive baseline work once, not weakening SQLite durability. Raw measurements are in `results/lifecycle-profile-control-final/summary.json` and `results/lifecycle-profile-after-final/summary.json` (local artifacts, not committed). The control loads the original four core modules from `HEAD` into memory; it does not restore or overwrite working files. Both versions use the same current Registry and fixture responses.

The final profiler directly instruments the outer tool call, excludes nested batch double-counting, and retains separate model latency. Tool time is roughly 0.47 s in each version; median SQLite write time is about 0.22 s before / 0.18 s after. The bottleneck is not SQLite itself. The initial measurement pair was 89.349 s / 9.356 s overall, confirming that absolute times vary with host load while the reduction remains substantial. Its old zero tool-seconds field was uninstrumented, not a measured zero.

These are fixture results, **not a full L3 speedup estimate**. Real model latency, prompts, context size, test execution and provider connection failures still affect wall time. Baseline secret detection may deliberately make a checkpoint non-resumable, exactly as before; resume is verified separately on a clean fixture.

Final recheck after adding run-isolation and tool-success assertions: median total **11.500 s**, checkpoint **8.443 s**, still 12 saves / 2 snapshots (`results/lifecycle-profile-verified/summary.json`). The combined focused command `python -m pytest tests/integration/test_lifecycle_docker_cache.py tests/mas/unit/test_lifecycle_performance.py -q` passed **23 tests**, including all three real Docker cases, in 11.64 s with `RAAC_LIFECYCLE_IMAGE` set. Lifecycle/checkpoint/resume regression separately passed 43 tests; the broader unit run passed 675 tests plus 153 subtests, with the 14 control-reproduced failures below. New files and Layer 1 pass Ruff; Layer 3 adds no lint findings compared with `HEAD`.

## Safety checks and limits

Pre-commit verification on 2026-10-27: exported the staged files into a separate temporary checkout, excluding the unrelated local topology change. All lifecycle tests, Registry loader tests, token-reduction tests and the offline Docker cache/resume tests passed: **93 tests in 26.49 s**, including three Docker cases. Ruff passed for the profiler, new tests and Layer 1. This verifies compatibility with the token-reduction changes committed earlier; no live API was called.

- Regression tests cover duplicate saves, changed baseline/secret sets, cached baseline poisoning, default fresh snapshots, resume workspace mismatch and cache invalidation after success, failure and cancellation.
- Real offline Docker tests cover same-container resume with a new runner, baseline/attribution preservation, direct edits, shell edits, external changes on resume and read-only-role mutation rejection.
- Existing lifecycle tests retain approval, crash-after-side-effect rejection, committed-observation reuse, shared usage and deadlines.
- The broader suite has unrelated failures involving the existing topology, seven-role model-count expectations and older Analyst/Planner output-format assertions. The same 14 failures reproduce after loading the original four core modules from `HEAD` into memory while retaining the current topology and Registry. They are not repaired by this performance change.
- No live API benchmark, dependency installation, schema migration, commit or push is performed. Connection retry/usage policy remains a separate issue.
