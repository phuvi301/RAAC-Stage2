"""Isolated MAS candidate execution followed by independent issue evaluation."""

import asyncio
import hashlib
import json
import tarfile
import tempfile
import threading
import time
from pathlib import Path
from uuid import uuid4

import yaml

from raac.common.tracing import JSONLTrace
from raac.common.provenance import collect_provenance
from raac.harness.layer3_execution.docker_execution import DockerExecutor, DockerExecutionPolicy, DockerRunWorkspace
from raac.harness.layer3_execution.sandbox.docker_sandbox import DockerSandbox, archive_repo, resolve_path
from raac.harness.layer3_execution.tools.docker_workspace import DockerWorkspaceTools
from raac.harness.layer3_execution.tools.paths import repo_path
from raac.llm.config import load_dotenv, load_llm_config
from raac.registry import load_registry

from .chain_runner import run_chain
from .dataset import Task, load_tasks
from .run_record import RunRecord, RunUsage, write_record

FRAMEWORK_ROOT = Path(__file__).resolve().parents[3]

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
                               build_timeout=max(1, int(deadline - time.monotonic())), progress=progress)
        progress({})
        if prepared["status"] != "prepared":
            raise RuntimeError("Task image preparation failed")
        session = DockerRunWorkspace(sandbox.client, prepared["image_id"], output_dir.name, policy=policy)
        session.__enter__()
        executor = DockerExecutor(session.container, policy, deadline=deadline)
        cleaned = session.container.exec_run(["python", "-c", REMOVE_HISTORY], user="1000:1000")
        if cleaned.exit_code != 0:
            raise RuntimeError("Unable to isolate task history")
        workspace = DockerWorkspaceTools(task.title + "\n\n" + task.body, executor)
        # Check source against the actual base commit, not a mutable image Git index.
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            archive_repo(task, resolve_path(sandbox.root, sandbox.config["repo_cache"]), directory)
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
                           build_timeout=max(1, int(remaining)), test_timeout=max(1, int(remaining)), progress=progress)
    finally:
        if sandbox.client:
            sandbox.client.close()


async def run_task(task: Task, *, level: str, repetition: int, output_dir: Path, config_path: Path) -> RunRecord:
    if level != "L3" or type(repetition) is not int or repetition < 1:
        raise ValueError("Benchmark currently supports only L3 and positive repetitions")
    config_path = Path(config_path).resolve()
    config = yaml.safe_load(config_path.read_text(encoding="utf-8-sig"))
    harness = yaml.safe_load((FRAMEWORK_ROOT / "configs/harness.yaml").read_text(encoding="utf-8-sig"))
    level_config = yaml.safe_load((FRAMEWORK_ROOT / "configs/levels/l3_collaboration.yaml").read_text(encoding="utf-8-sig"))
    load_dotenv(FRAMEWORK_ROOT / ".env")
    llm_config = load_llm_config(FRAMEWORK_ROOT / "configs/llm.yaml")
    registry = load_registry(FRAMEWORK_ROOT / "registry")
    models = llm_config.resolve_role_models({role.id: role.model.ref for role in registry.roles.values()})
    policy = DockerExecutionPolicy(timeout_seconds=harness["execution"]["timeout_seconds"],
        max_output_bytes=harness["execution"]["max_output_bytes"],
        run_timeout_seconds=float(config.get("agent_run_timeout_seconds", 9000.0)))
    output_dir = Path(output_dir).resolve()
    record = RunRecord(run_id=output_dir.name, task_id=task.id, base_commit=task.base_commit,
                       level=level, repetition=repetition)
    output_dir.mkdir(parents=True, exist_ok=False)
    record_path = output_dir / "record.json"
    trace = JSONLTrace(output_dir / "llm.jsonl", secrets=(llm_config.api_key.get_secret_value(),))
    context = {"run_id": record.run_id, "task_id": task.id, "level": level}
    record.provenance = {**collect_provenance(FRAMEWORK_ROOT, registry,
        [FRAMEWORK_ROOT / "configs/llm.yaml", FRAMEWORK_ROOT / "configs/harness.yaml",
         FRAMEWORK_ROOT / "configs/levels/l3_collaboration.yaml"],
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
                agent = await run_chain(task, run_id=record.run_id, workspace=workspace, registry=registry,
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
                    name = event.get("role", event.get("node", "unknown"))
                    record.agent_activations[name] = record.agent_activations.get(name, 0) + 1
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
            record.status = "completed" if record.evaluation_status in {"resolved", "unresolved"} else "error"
    except asyncio.CancelledError:
        record.status = "interrupted"
        record.errors.append({"phase": record.phase, "kind": "CancelledError"})
        raise
    except Exception as error:
        record.status = "timeout" if isinstance(error, TimeoutError) else "error"
        record.errors.append({"phase": record.phase, "kind": type(error).__name__})
    finally:
        cancel.set()
        if evaluation_task is not None and not evaluation_task.done():
            results = await asyncio.gather(evaluation_task, return_exceptions=True)
            if results and isinstance(results[0], dict):
                record.evaluation = results[0]
                record.evaluation_status = results[0]["status"]
        if evaluation_started is not None:
            record.timing.evaluation_seconds = time.monotonic() - evaluation_started
        if prepare_task is not None and not prepare_task.done():
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
            except Exception as error:
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
    if args.task_id not in tasks:
        raise ValueError("Unknown task selection")
    output = resolve_path(FRAMEWORK_ROOT, config["results"]) / ("benchmark-" + uuid4().hex)
    record = await run_task(tasks[args.task_id], level=args.level, repetition=args.repetition,
                             output_dir=output, config_path=config_path)
    print(json.dumps({"run_id": record.run_id, "status": record.status,
                      "agent_status": record.agent_status, "evaluation_status": record.evaluation_status,
                      "record": str(output / "record.json")}))
    return 0 if record.evaluation_status == "resolved" and record.status == "completed" else 1
