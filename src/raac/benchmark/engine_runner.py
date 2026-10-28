"""Compose Engine-backed levels (L1 Fixed, L2 Routing) against a supplied isolated workspace."""

import asyncio
from pathlib import Path

from raac.collaboration import Engine
from raac.collaboration.levels.l1_fixed import L1_ROLES, L1Fixed, validate_l1_config
from raac.collaboration.levels.l2_routing import L2Routing, validate_l2_config
from raac.common.provenance import collect_provenance
from raac.harness.layer2_core.context_manager import RoleContextPolicy
from raac.harness.layer2_core.memory.short_term import ShortTermMemoryPolicy
from raac.harness.layer3_execution.sandbox.batch import atomic_json
from raac.harness.layer3_execution.tool_orchestrator import ToolOrchestrator
from raac.llm.client import LLMClient

from .dataset import Task


async def _run_engine_level(
    task: Task,
    *,
    level,
    validate_config,
    level_config_relpath: str,
    run_id: str,
    workspace,
    registry,
    llm_config,
    harness: dict,
    level_config: dict,
    output_dir: Path,
    trace=None,
    harness_path=None,
    llm_path=None,
) -> dict:
    root = Path(__file__).resolve().parents[3]
    level_config = validate_config(level_config).model_dump(mode="json")
    role_models = llm_config.resolve_role_models({role.id: role.model.ref for role in registry.roles.values()})
    effective = {"llm": llm_config.model_dump(mode="json"), "harness": harness,
                 "level": level_config, "token_limit": harness["token_limit"],
                 "execution_mode": "docker", "role_models": {name: value.model_dump() for name, value in role_models.items()}}
    provenance = collect_provenance(root, registry, [Path(llm_path) if llm_path else root / "configs/llm.yaml", Path(harness_path) if harness_path else root / "configs/harness.yaml",
        root / level_config_relpath], effective)
    provenance["task_snapshot"] = workspace.baseline_ref
    provenance["candidate_contract"] = "implementation-only-v1"
    output_dir.mkdir(parents=True, exist_ok=True)
    client = LLMClient(llm_config, log_path=output_dir / "llm.jsonl", trace_content=harness["trace_content"])
    if trace is not None:
        client.trace = trace
    context = {"run_id": run_id, "task_id": task.id, "level": level.name}

    def trace_tool(call_context, event, **payload):
        workspace.executor.bind_trace(client.trace, call_context, content_enabled=client.trace_content)
        if not client.trace_content:
            payload = {key: value for key, value in payload.items() if key not in {"arguments", "observation"}}
        client.trace.emit(call_context, event=event, **payload)

    tools = ToolOrchestrator(workspace, timeout_seconds=workspace.executor.policy.timeout_seconds + 15,
                             trace=trace_tool)
    def trace_routing(event):
        if event.get("event") in {"routing_decided", "routing_fallback", "agent_skipped"}:
            client.trace.emit(context, **event)

    engine = Engine.from_runtime(registry=registry, client=client, tools=tools, provenance=provenance,
        logger=trace_routing,
        token_limit=harness["token_limit"],
        context_policy=RoleContextPolicy.model_validate(harness.get("context", {})),
        memory_policy=ShortTermMemoryPolicy.model_validate(harness.get("memory", {})))
    record = {"status": "error", "events": [], "provenance": provenance}
    try:
        client.trace.emit(context, event="run_started", roles=list(L1_ROLES),
                          decision={"topology": level_config.get("topology", "linear"),
                                    "max_rounds": level_config.get("max_rounds", 1)},
                          workspace={"mode": "docker", "repo_dir": "/workspace"})
        result = await engine.run(run_id=run_id, level=level, config=level_config,
            task={"task_id": task.id, "requirement": workspace.requirement, "base_commit": task.base_commit})
        state = result.state
        outcome = state.get("result") if isinstance(state.get("result"), dict) else {}
        status = outcome.get("status") or state.get("status", "error")
        record.update(outcome)
        record.update(status="aborted" if status == "failed" else status,
                      stop_reason=outcome.get("stop_reason") or (state.get("error") or {}).get("kind"),
                      rounds_completed=outcome.get("rounds_completed", state.get("rounds", 0)))
        if state.get("routing") is not None:
            record["routing"] = state["routing"]
            provenance["routing"] = state["routing"]
        return record
    except asyncio.CancelledError:
        record.update(status="interrupted", stop_reason="cancelled")
        raise
    finally:
        record["events"] = list(engine.events)
        if record["status"] in {"aborted", "error", "interrupted", "timeout"} and client.upstream_errors.get(run_id):
            record["upstream_error"] = client.upstream_errors[run_id]
        record["usage"] = client.usage.snapshot(run_id=run_id)
        client.trace.emit(context, event="run_completed", status=record["status"],
                          stop_reason=record.get("stop_reason"), usage=record["usage"],
                          upstream_error=record.get("upstream_error"))
        atomic_json(output_dir / "agent-result.json", client.trace.redact(record))
        await client.aclose()


async def run_l1(task: Task, *, run_id: str, workspace, registry, llm_config,
                 harness: dict, level_config: dict, output_dir: Path, trace=None, harness_path=None, llm_path=None) -> dict:
    return await _run_engine_level(
        task, level=L1Fixed(), validate_config=validate_l1_config,
        level_config_relpath="configs/levels/l1_fixed.yaml", run_id=run_id,
        workspace=workspace, registry=registry, llm_config=llm_config,
        harness=harness, level_config=level_config, output_dir=output_dir, trace=trace, harness_path=harness_path, llm_path=llm_path,
    )


async def run_l2(task: Task, *, run_id: str, workspace, registry, llm_config,
                 harness: dict, level_config: dict, output_dir: Path, trace=None, harness_path=None, llm_path=None) -> dict:
    return await _run_engine_level(
        task, level=L2Routing(), validate_config=validate_l2_config,
        level_config_relpath="configs/levels/l2_routing.yaml", run_id=run_id,
        workspace=workspace, registry=registry, llm_config=llm_config,
        harness=harness, level_config=level_config, output_dir=output_dir, trace=trace, harness_path=harness_path, llm_path=llm_path,
    )
