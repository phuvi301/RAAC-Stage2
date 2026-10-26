"""Isolated MAS candidate execution followed by independent issue evaluation."""

import asyncio
import hashlib
import json
import re
import tarfile
import tempfile
import threading
import time
from pathlib import Path
from uuid import uuid4

import yaml
from docker.errors import DockerException
from requests.exceptions import RequestException

from raac.common.provenance import collect_provenance
from raac.common.tracing import JSONLTrace
from raac.harness.layer3_execution.docker_execution import (
    DockerExecutionPolicy,
    DockerExecutor,
    DockerRunWorkspace,
)
from raac.harness.layer3_execution.sandbox.batch import atomic_json
from raac.harness.layer3_execution.sandbox.docker_sandbox import (
    DockerSandbox,
    archive_repo,
    cache_lock,
    resolve_path,
)
from raac.harness.layer3_execution.tools.docker_workspace import DockerWorkspaceTools
from raac.harness.layer3_execution.tools.paths import repo_path
from raac.llm.config import load_dotenv, load_llm_config
from raac.registry import load_registry

from .chain_runner import run_chain
from .dataset import Task, load_tasks, source_dataset
from .l1_runner import run_l1
from .run_record import RunRecord, RunUsage, write_record

FRAMEWORK_ROOT = Path(__file__).resolve().parents[3]

# Only the agent phase differs by level; setup, export and evaluation are shared.
LEVELS = {"L1": "configs/levels/l1_fixed.yaml", "L3": "configs/levels/l3_collaboration.yaml"}


def level_agent(level):
    return {"L1": run_l1, "L3": run_chain}[level]

# Drop cloned history before the model can inspect it: it may contain the gold fix.
REMOVE_HISTORY = """
import os, shutil, signal
signal.alarm(30)
for directory, dirs, names in os.walk('/workspace', topdown=True):
    if '.git' in dirs:
        shutil.rmtree(os.path.join(directory,'.git')); dirs.remove('.git')
    if '.git' in names: os.unlink(os.path.join(directory,'.git'))
"""


def prepare_workspace(task, config_path, output_dir, policy, cancel, deadline):
    sandbox = DockerSandbox(root=FRAMEWORK_ROOT, config=config_path)
    session = None

    def progress(event):
        if cancel.is_set() or time.monotonic() >= deadline:
            raise RuntimeError("Preparation cancelled or timed out")

    try:
        prepared = sandbox.run(task.id, prepare=True, run_id="prepare-" + uuid4().hex,
                               build_timeout=max(1, int(deadline - time.monotonic())),
                               lock_timeout=max(0, deadline - time.monotonic()), progress=progress)
        progress({})
        if prepared["status"] != "prepared":
            raise DockerException("Task image preparation failed")
        session = DockerRunWorkspace(sandbox.client, prepared["image_id"], output_dir.name, policy=policy)
        session.__enter__()
        executor = DockerExecutor(session.container, policy, deadline=deadline)
        cleaned = session.container.exec_run(["python", "-E", "-s", "-S", "-c", REMOVE_HISTORY], user="1000:1000", workdir="/")
        if cleaned.exit_code != 0:
            raise RuntimeError("Unable to isolate task history")
        workspace = DockerWorkspaceTools(task.title + "\n\n" + task.body, executor)
        # Check source against the actual base commit, not a mutable image Git index.
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            archive_repo(task, resolve_path(sandbox.root, sandbox.config["repo_cache"]), directory / "source.tar", deadline=deadline)
            source = {}
            with tarfile.open(directory / "source.tar") as archive:
                for member in archive:
                    if not member.isfile():
                        continue
                    try:
                        name = repo_path(member.name.removeprefix("./"))
                    except ValueError:
                        continue
                    if member.size > workspace.max_file_bytes:
                        raise ValueError("Base source exceeds snapshot limits")
                    if any(part == "build" or part == "dist" or part.endswith(".egg-info") for part in Path(name).parts):
                        continue
                    source[name] = archive.extractfile(member).read(workspace.max_file_bytes + 1)
            if any(workspace.baseline_files.get(name) != content for name, content in source.items()):
                raise ValueError("Profile installation changed base source")
            workspace.setup_generated_paths = set(workspace.baseline_files) - set(source)
        progress({})
        return session, workspace, prepared
    except BaseException:
        if session:
            session.__exit__(RuntimeError, RuntimeError("setup failed"), None)
        if sandbox.client:
            sandbox.client.close()
        raise


def evaluate_candidate(task, *, config_path, patch_path, remaining, cancel=None):
    sandbox = DockerSandbox(root=FRAMEWORK_ROOT, config=config_path)
    def progress(event):
        if cancel is not None and cancel.is_set():
            raise KeyboardInterrupt

    try:
        return sandbox.run(task.id, patch_path=patch_path, test_mode="issue", validate=False,
                           build_timeout=max(1, int(remaining)), test_timeout=max(1, int(remaining)),
                           lock_timeout=max(0, remaining), progress=progress)
    finally:
        if sandbox.client:
            sandbox.client.close()


async def run_task(task: Task, *, level: str, repetition: int, output_dir: Path, config_path: Path, attempt: int = 1) -> RunRecord:
    if level not in LEVELS or type(repetition) is not int or repetition < 1:
        raise ValueError("Benchmark currently supports only L1/L3 and positive repetitions")
    config_path = Path(config_path).resolve()
    config = yaml.safe_load(config_path.read_text(encoding="utf-8-sig"))
    harness = yaml.safe_load((FRAMEWORK_ROOT / "configs/harness.yaml").read_text(encoding="utf-8-sig"))
    level_path = FRAMEWORK_ROOT / LEVELS[level]
    level_config = yaml.safe_load(level_path.read_text(encoding="utf-8-sig"))
    load_dotenv(FRAMEWORK_ROOT / ".env")
    llm_config = load_llm_config(FRAMEWORK_ROOT / "configs/llm.yaml")
    registry = load_registry(FRAMEWORK_ROOT / "registry")
    models = llm_config.resolve_role_models({role.id: role.model.ref for role in registry.roles.values()})
    policy = DockerExecutionPolicy(timeout_seconds=harness["execution"]["timeout_seconds"],
        max_output_bytes=harness["execution"]["max_output_bytes"],
        run_timeout_seconds=float(config.get("agent_run_timeout_seconds", 9000.0)))
    output_dir = Path(output_dir).resolve()
    record = RunRecord(run_id=output_dir.name, task_id=task.id, base_commit=task.base_commit,
                       level=level, repetition=repetition, attempt=attempt)
    output_dir.mkdir(parents=True, exist_ok=False)
    record_path = output_dir / "record.json"
    trace = JSONLTrace(output_dir / "llm.jsonl", secrets=(llm_config.api_key.get_secret_value(),))
    context = {"run_id": record.run_id, "task_id": task.id, "level": level}
    record.provenance = {**collect_provenance(FRAMEWORK_ROOT, registry,
        [FRAMEWORK_ROOT / "configs/llm.yaml", FRAMEWORK_ROOT / "configs/harness.yaml", level_path],
        {"llm": llm_config.model_dump(mode="json"), "harness": harness, "level": level_config,
         "execution": policy.model_dump(), "role_models": {name: value.model_dump() for name, value in models.items()}}),
        "benchmark_config": {"sha256": hashlib.sha256(config_path.read_bytes()).hexdigest(), "snapshot": config},
        "lock_sha256": hashlib.sha256((FRAMEWORK_ROOT / "requirements.lock").read_bytes()).hexdigest()}
    record.trace = {"file": "llm.jsonl", "content_enabled": harness["trace_content"]}
    started = time.monotonic()
    deadline = started + policy.run_timeout_seconds
    cancel = threading.Event()
    session, workspace, prepare_task, evaluation_task = None, None, None, None
    evaluation_started = None

    def phase(name):
        if record.phase != "pending":
            trace.emit(context, event="benchmark_phase_completed", phase=record.phase)
        record.phase = name
        write_record(record_path, record)
        trace.emit(context, event="benchmark_phase_started", phase=name)

    try:
        record.status = "running"
        async with asyncio.timeout(policy.run_timeout_seconds):
            phase("setup")
            before = time.monotonic()
            prepare_task = asyncio.create_task(asyncio.to_thread(prepare_workspace, task, config_path,
                output_dir, policy, cancel, deadline))
            session, workspace, prepared = await asyncio.shield(prepare_task)
            record.timing.setup_seconds = time.monotonic() - before
            record.provenance["image_id"] = prepared["image_id"]
            trace.emit(context, event="container_started", container_id=getattr(session.container, "id", "unknown"))
            phase("agent")
            before = time.monotonic()
            try:
                agent = await level_agent(level)(task, run_id=record.run_id, workspace=workspace, registry=registry,
                    llm_config=llm_config, harness=harness, level_config=level_config, output_dir=output_dir, trace=trace)
            finally:
                record.timing.agent_seconds = time.monotonic() - before
                partial = output_dir / "agent-result.json"
                if partial.exists():
                    saved = json.loads(partial.read_text(encoding="utf-8"))
                    record.usage = RunUsage.model_validate(saved["usage"])
                    record.agent_status = saved["status"]
            record.agent_status = agent["status"]
            record.verdict, record.stop_reason = agent.get("verdict"), agent.get("stop_reason")
            record.usage = RunUsage.model_validate(agent["usage"])
            record.provenance.update(agent.get("provenance", {}))
            record.rounds = agent.get("rounds_completed", 0)
            for event in agent.get("events", []):
                if event.get("event") == "agent_started":
                    name = event.get("role") or event.get("node") or event.get("agent_id", "unknown")
                    record.agent_activations[name] = record.agent_activations.get(name, 0) + 1
            record.messages = sum(record.agent_activations.values())
            record.provenance["metric_definitions"] = {"messages": "incoming role handoffs"}
            if record.agent_status in {"aborted", "error", "interrupted", "timeout"}:
                record.errors.append({"phase": "agent", "kind": "AgentExecutionFailed", "retryable": "false"})
            if record.stop_reason == "environment_setup_failed":
                raise RuntimeError("Environment verification failed")
            phase("export")
            artifact = workspace.export_candidate(output_dir / "candidate.patch")
            record.patch_path, record.patch_sha256 = artifact["path"], artifact["sha256"]
            phase("evaluation")
            before = time.monotonic()
            evaluation_started = before
            evaluation_task = asyncio.create_task(asyncio.to_thread(evaluate_candidate, task, config_path=config_path,
                patch_path=record.patch_path, remaining=max(1.0, deadline - time.monotonic()), cancel=cancel))
            record.evaluation = await asyncio.shield(evaluation_task)
            record.timing.evaluation_seconds = time.monotonic() - before
            record.evaluation_status = record.evaluation["status"]
            if record.evaluation_status not in {"resolved", "unresolved"}:
                record.errors.append({"phase": "evaluation", "kind": record.evaluation_status, "retryable": "false"})
            record.status = "error" if record.errors else "completed"
    except asyncio.CancelledError:
        record.status = "interrupted"
        record.errors.append({"phase": record.phase, "kind": "CancelledError"})
        raise
    except Exception as error:  # noqa: BLE001 -- persist a safe record for every failed runtime boundary.
        record.status = "timeout" if isinstance(error, TimeoutError) else "error"
        retryable = record.phase in {"setup", "evaluation"} and isinstance(error, (DockerException, RequestException, OSError))
        record.errors.append({"phase": record.phase, "kind": type(error).__name__, "retryable": str(retryable).lower()})
    finally:
        cancel.set()
        if evaluation_task is not None:
            results = await asyncio.gather(evaluation_task, return_exceptions=True)
            if results and isinstance(results[0], dict):
                record.evaluation = results[0]
                record.evaluation_status = results[0]["status"]
        if evaluation_started is not None:
            record.timing.evaluation_seconds = time.monotonic() - evaluation_started
        if prepare_task is not None and session is None:
            result = await asyncio.gather(prepare_task, return_exceptions=True)
            if result and isinstance(result[0], tuple):
                session, workspace, _ = result[0]
        if record.phase == "setup":
            record.timing.setup_seconds = time.monotonic() - started
        failed_phase = record.phase
        record.phase = "cleanup"
        if session:
            try:
                await asyncio.to_thread(session.__exit__, None, None, None)
                trace.emit(context, event="container_removed")
            except Exception as error:  # noqa: BLE001 -- cleanup must not erase the primary failure.
                record.errors.append({"phase": "cleanup", "kind": type(error).__name__})
                record.status = "error"
            finally:
                if getattr(session, "client", None):
                    session.client.close()
        record.timing.total_seconds = time.monotonic() - started
        record.phase = "finished"
        write_record(record_path, record)
        trace.emit(context, event="benchmark_phase_failed" if record.errors else "benchmark_phase_completed",
                   phase=failed_phase, status=record.status)
    return record


async def execute(args):
    config_path = resolve_path(FRAMEWORK_ROOT, args.config or "configs/benchmark.yaml")
    config = yaml.safe_load(config_path.read_text(encoding="utf-8-sig"))
    tasks = load_tasks(resolve_path(FRAMEWORK_ROOT, config["dataset"]))
    if args.command == "batch":
        summary = await run_benchmark_batch(config_path=config_path, task_ids=args.task_id,
            levels=[args.level], repetitions=args.repetitions, batch_id=args.batch_id,
            resume=args.resume, retry_errors=args.retry_errors)
        print(json.dumps(summary))
        return 0 if summary["completed"] == summary["total"] else 1
    if args.task_id not in tasks:
        raise ValueError("Unknown task selection")
    output = resolve_path(FRAMEWORK_ROOT, config["results"]) / ("benchmark-" + uuid4().hex)
    record = await run_task(tasks[args.task_id], level=args.level, repetition=args.repetition,
                             output_dir=output, config_path=config_path)
    print(json.dumps({"run_id": record.run_id, "status": record.status,
                      "agent_status": record.agent_status, "evaluation_status": record.evaluation_status,
                      "record": str(output / "record.json")}))
    return 0 if record.evaluation_status == "resolved" and record.status == "completed" else 1


def benchmark_fingerprint(config_path, config, selection):
    dataset = resolve_path(FRAMEWORK_ROOT, config["dataset"])
    paths = {Path(config_path), dataset, source_dataset(dataset),
             resolve_path(FRAMEWORK_ROOT, config["evaluation"]), FRAMEWORK_ROOT / "requirements.lock",
             FRAMEWORK_ROOT / "docker/Dockerfile.sandbox"}
    paths.update(resolve_path(FRAMEWORK_ROOT, p) for p in config["profiles"].values())
    paths.update((FRAMEWORK_ROOT / "src").rglob("*.py"))
    paths.update(p for p in (FRAMEWORK_ROOT / "registry").rglob("*") if p.is_file())
    paths.update((FRAMEWORK_ROOT / "configs").rglob("*.yaml"))
    load_dotenv(FRAMEWORK_ROOT / ".env")
    effective = load_llm_config(FRAMEWORK_ROOT / "configs/llm.yaml").model_dump(mode="json")
    contents = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(paths)}
    contents["framework_commit"] = collect_provenance(FRAMEWORK_ROOT, load_registry(FRAMEWORK_ROOT / "registry"), [], {})["framework_commit"]
    return hashlib.sha256(json.dumps({"files": contents, "selection": selection, "effective_llm": effective},
                                    sort_keys=True).encode()).hexdigest()


async def run_benchmark_batch(*, config_path: Path, task_ids: list[str], levels: list[str], repetitions: int,
                              batch_id: str, resume: bool = False, retry_errors: bool = False) -> dict:
    if retry_errors and not resume:
        raise ValueError("retry-errors requires resume")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", batch_id):
        raise ValueError("Invalid batch ID")
    if (not levels or len(set(levels)) != len(levels) or set(levels) - LEVELS.keys()
            or type(repetitions) is not int or repetitions < 1):
        raise ValueError("Only distinct L1/L3 levels with positive repetitions are supported")
    config_path = Path(config_path).resolve()
    config = yaml.safe_load(config_path.read_text(encoding="utf-8-sig"))
    tasks = load_tasks(resolve_path(FRAMEWORK_ROOT, config["dataset"]))
    ids = list(dict.fromkeys(task_ids))
    if not ids or set(ids) - tasks.keys():
        raise ValueError("Select known task IDs explicitly")
    selection = [[task, level, repetition] for task in ids for level in levels
                 for repetition in range(1, repetitions + 1)]
    signature = benchmark_fingerprint(config_path, config, selection)
    results = resolve_path(FRAMEWORK_ROOT, config["results"])
    directory = results / "benchmark-batches" / batch_id
    directory.mkdir(parents=True, exist_ok=True)
    manifest_path = directory / "manifest.json"

    def read_attempt(run_id, task_id, level, repetition, attempt):
        if not isinstance(run_id, str) or not re.fullmatch(r"benchmark-[0-9a-f]{32}", run_id):
            raise ValueError("Invalid run ID in manifest")
        path = results / run_id / "record.json"
        if not path.resolve().is_relative_to(results.resolve()):
            raise ValueError("Record path escapes results")
        if path.exists():
            record = RunRecord.model_validate_json(path.read_text(encoding="utf-8"))
            if (record.run_id, record.task_id, record.level, record.repetition) != (run_id, task_id, level, repetition):
                raise ValueError("Batch record identity mismatch")
            if record.attempt != attempt:
                raise ValueError("Batch attempt identity mismatch")
        else:
            record = RunRecord(run_id=run_id, task_id=task_id, level=level, repetition=repetition,
                               base_commit=tasks[task_id].base_commit, attempt=attempt)
        if record.status in {"pending", "running"}:
            record.status = "interrupted"
            record.errors.append({"phase": record.phase, "kind": "RecoveredInterruptedAttempt"})
            write_record(path, record)
        return record

    with cache_lock(directory / "batch.lock", timeout=0):
        if resume:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if manifest["fingerprint"] != signature:
                raise ValueError("Batch fingerprint changed; create a new batch")
            if [[entry["task_id"], entry["level"], entry["repetition"]] for entry in manifest["entries"]] != selection:
                raise ValueError("Batch manifest selection mismatch")
        else:
            if manifest_path.exists():
                raise ValueError("Batch exists; use resume")
            manifest = {"schema_version": 1, "fingerprint": signature, "status": "running",
                        "entries": [{"task_id": task, "level": level, "repetition": repetition, "attempts": []}
                                    for task, level, repetition in selection]}
        active = manifest.pop("active", None)
        if active:
            entry = manifest["entries"][active["index"]]
            position = entry["attempts"].index(active["run_id"]) + 1 if active["run_id"] in entry["attempts"] else len(entry["attempts"]) + 1
            read_attempt(active["run_id"], entry["task_id"], entry["level"], entry["repetition"], position)
            if active["run_id"] not in entry["attempts"]:
                entry["attempts"].append(active["run_id"])
        manifest["status"] = "running"
        atomic_json(manifest_path, manifest)
        for index, entry in enumerate(manifest["entries"]):
            if benchmark_fingerprint(config_path, config, selection) != signature:
                raise ValueError("Batch fingerprint changed during execution")
            if entry["attempts"]:
                previous = read_attempt(entry["attempts"][-1], entry["task_id"], entry["level"],
                                        entry["repetition"], len(entry["attempts"]))
                retryable = any(error.get("retryable") == "true" for error in previous.errors)
                if previous.status != "interrupted" and not (retry_errors and retryable and previous.status in {"error", "timeout"}):
                    continue
            run_id = "benchmark-" + uuid4().hex
            manifest["active"] = {"index": index, "run_id": run_id}
            atomic_json(manifest_path, manifest)
            try:
                await run_task(tasks[entry["task_id"]], level=entry["level"], repetition=entry["repetition"],
                    attempt=len(entry["attempts"]) + 1, output_dir=results / run_id, config_path=config_path)
            except BaseException:
                manifest["status"] = "interrupted"
                atomic_json(manifest_path, manifest)
                raise
            entry["attempts"].append(run_id)
            manifest.pop("active", None)
            atomic_json(manifest_path, manifest)
        manifest["status"] = "processed"
        atomic_json(manifest_path, manifest)
        latest = [read_attempt(entry["attempts"][-1], entry["task_id"], entry["level"], entry["repetition"],
                               len(entry["attempts"])) for entry in manifest["entries"]]
        return {"batch_id": batch_id, "status": "processed", "total": len(latest),
                "completed": sum(record.status == "completed" for record in latest),
                "resolved": sum(record.evaluation_status == "resolved" for record in latest),
                "attempts": sum(len(entry["attempts"]) for entry in manifest["entries"]), "manifest": str(manifest_path)}
