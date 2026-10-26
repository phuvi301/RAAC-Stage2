"""Compose the existing Chain against a supplied isolated workspace."""

import asyncio
from pathlib import Path

from raac.collaboration.levels.l3_collaboration import (
    ChainLimits,
    ChainNodes,
    WorkItem,
    build_collaboration_graph,
    coordinate_chain,
    initial_chain_state,
)
from raac.common.provenance import collect_provenance
from raac.harness.layer1_lifecycle import AgentRunner
from raac.harness.layer2_core.context_manager import RoleContextPolicy
from raac.harness.layer2_core.memory.short_term import ShortTermMemoryPolicy
from raac.harness.layer3_execution.sandbox.batch import atomic_json
from raac.harness.layer3_execution.tool_orchestrator import ToolOrchestrator
from raac.llm.client import LLMClient

from .dataset import Task


async def run_chain(task: Task, *, run_id: str, workspace, registry, llm_config,
                    harness: dict, level_config: dict, output_dir: Path, trace=None, harness_path=None) -> dict:
    root = Path(__file__).resolve().parents[3]
    decision = coordinate_chain(level_config)
    limits = ChainLimits(max_rounds=decision.max_rounds, hard_max_rounds=level_config["hard_max_rounds"],
                         timeout_seconds=harness["timeout_seconds"], agent_timeout_seconds=harness["agent_timeout_seconds"])
    role_models = llm_config.resolve_role_models({role.id: role.model.ref for role in registry.roles.values()})
    effective = {"llm": llm_config.model_dump(mode="json"), "harness": harness,
                 "level": level_config, "token_limit": harness["token_limit"],
                 "execution_mode": "docker", "role_models": {name: value.model_dump() for name, value in role_models.items()}}
    provenance = collect_provenance(root, registry, [root / "configs/llm.yaml", Path(harness_path) if harness_path else root / "configs/harness.yaml",
        root / "configs/levels/l3_collaboration.yaml"], effective)
    provenance["task_snapshot"] = workspace.baseline_ref
    provenance["candidate_contract"] = "implementation-only-v1"
    output_dir.mkdir(parents=True, exist_ok=True)
    client = LLMClient(llm_config, log_path=output_dir / "llm.jsonl", trace_content=harness["trace_content"])
    if trace is not None:
        client.trace = trace
    context = {"run_id": run_id, "task_id": task.id, "level": "L3"}

    def trace_tool(call_context, event, **payload):
        workspace.executor.bind_trace(client.trace, call_context, content_enabled=client.trace_content)
        if not client.trace_content:
            payload = {key: value for key, value in payload.items() if key not in {"arguments", "observation"}}
        client.trace.emit(call_context, event=event, **payload)

    tools = ToolOrchestrator(workspace, timeout_seconds=workspace.executor.policy.timeout_seconds + 15,
                             trace=trace_tool)
    runner = AgentRunner(registry, client, tools, provenance=provenance, token_limit=harness["token_limit"],
        context_policy=RoleContextPolicy.model_validate(harness.get("context", {})),
        memory_policy=ShortTermMemoryPolicy.model_validate(harness.get("memory", {})))
    work = WorkItem(run_id=run_id, task_id=task.id, work_id=task.id, scope="task",
                    requirement=workspace.requirement, baseline_ref=workspace.baseline_ref)
    record = {"status": "error", "events": [], "provenance": provenance}
    try:
        client.trace.emit(context, event="run_started", decision=decision.model_dump(),
                          workspace={"mode": "docker", "repo_dir": "/workspace"})
        graph = build_collaboration_graph(decision, ChainNodes(runner, budget_guard=runner.budget_guard))
        state = await graph.ainvoke(initial_chain_state(work, limits), {"recursion_limit": 6 * limits.max_rounds + 16})
        record.update(**state["result"], events=state["events"])
        return record
    except asyncio.CancelledError:
        record.update(status="interrupted", stop_reason="cancelled")
        raise
    finally:
        record["usage"] = client.usage.snapshot(run_id=run_id)
        client.trace.emit(context, event="run_completed", status=record["status"],
                          stop_reason=record.get("stop_reason"), usage=record["usage"])
        atomic_json(output_dir / "agent-result.json", client.trace.redact(record))
        await client.aclose()
