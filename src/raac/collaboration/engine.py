"""Shared LangGraph engine for collaboration-level orchestration."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from raac.harness.layer1_lifecycle import AgentRunner

from .graph_builder import build_graph
from .state import CollaborationState, initial_state


@dataclass(frozen=True)
class EngineResult:
    run_id: str
    state: CollaborationState
    resumed: bool = False

    @property
    def result(self):
        return self.state.get("result")


class Engine:
    """Run a level graph while keeping its checkpoint scope separate from Layer 1."""

    def __init__(self, *, registry, runner: AgentRunner | None = None, checkpointer=None, logger=None) -> None:
        self.registry = registry
        self.runner = runner
        self.checkpointer = checkpointer
        self.logger = logger
        self.events: list[dict[str, Any]] = []

    @classmethod
    def from_runtime(cls, *, registry, client, tools, provenance: dict[str, Any],
                     checkpointer=None, logger=None, **runner_options):
        """Create an Engine backed by the real LLM client and tool runtime.

        ``tools`` is normally a ``ToolOrchestrator`` backed by a local or Docker
        workspace. AgentRunner remains the owner of model-call caps, approvals,
        lifecycle checkpoints and resume safety.
        """
        runner = AgentRunner(registry, client, tools, provenance=provenance, **runner_options)
        return cls(registry=registry, runner=runner, checkpointer=checkpointer, logger=logger)

    def _log(self, event: dict[str, Any]) -> None:
        self.events.append(dict(event))
        if self.logger:
            self.logger(dict(event))

    @staticmethod
    def _task_value(task, key: str, default=None):
        if isinstance(task, dict):
            return task.get(key, default)
        return getattr(task, key, default)

    @staticmethod
    def _output_value(output):
        if hasattr(output, "output"):
            output = output.output
        if hasattr(output, "model_dump"):
            return output.model_dump(mode="json")
        return output

    def _require_runner(self):
        if self.runner is None:
            raise RuntimeError("Engine requires an AgentRunner; use Engine.from_runtime or pass runner")
        return self.runner

    async def invoke_agent(self, role: str, state: CollaborationState, *, incoming_message=None,
                           artifacts: Mapping[str, Any] | None = None):
        """Invoke one Registry role through Layer 1 and return state updates.

        The runner performs the real LLM request and any model-requested tools.
        Its tool orchestrator is therefore the only path to workspace/sandbox I/O.
        """
        runner = self._require_runner()
        task = state["task"]
        task_id = self._task_value(task, "task_id", self._task_value(task, "id", state["run_id"]))
        request = {
            "run_id": state["run_id"], "task_id": task_id, "work_id": task_id,
            "scope": "task", "level": state.get("level", "unknown"),
            "round": state.get("rounds", 0),
            "artifacts": {"collaboration_state": {
                "requirement": state.get("requirement"), "plan": state.get("plan"),
                "patch": state.get("patch"), "test_results": state.get("test_results"),
                "messages": state.get("messages", []),
            }, **(artifacts or {})},
            "incoming_message": incoming_message or {"from": "engine", "content": state.get("requirement", "")},
        }
        self._log({"event": "agent_call_started", "agent_id": role, "run_id": state["run_id"],
                   "level": state.get("level")})
        try:
            output = self._output_value(await runner(role, request))
        except Exception as exc:
            self._log({"event": "agent_call_failed", "agent_id": role, "run_id": state["run_id"],
                       "error": type(exc).__name__})
            raise
        self._log({"event": "agent_call_completed", "agent_id": role, "run_id": state["run_id"],
                   "level": state.get("level")})
        return {"agent_outputs": [{"role": role, "output": output}],
                "messages": [{"from": role, "content": output}]}

    async def execute_tool(self, role: str, state: CollaborationState, tool_name: str,
                           arguments: dict[str, Any], *, tool_call_id: str | None = None):
        """Execute a declared role tool through ToolOrchestrator/sandbox."""
        runner = self._require_runner()
        loaded_role = self.registry.get(role)
        context = {"run_id": state["run_id"],
                   "task_id": self._task_value(state["task"], "task_id", self._task_value(state["task"], "id", state["run_id"])),
                   "work_id": state["run_id"], "scope": "task", "level": state.get("level", "unknown"),
                   "agent_id": loaded_role.spec.id, "registry_version": self.registry.version,
                   "provenance": getattr(runner, "provenance", {})}
        self._log({"event": "tool_call_started", "agent_id": role, "tool": tool_name,
                   "run_id": state["run_id"]})
        try:
            observation = await runner.tools.execute(loaded_role, tool_name, arguments,
                                                     context=context, tool_call_id=tool_call_id,
                                                     origin="engine")
        except Exception as exc:
            self._log({"event": "tool_call_failed", "agent_id": role, "tool": tool_name,
                       "run_id": state["run_id"], "error": type(exc).__name__})
            raise
        self._log({"event": "tool_call_completed", "agent_id": role, "tool": tool_name,
                   "run_id": state["run_id"]})
        return {"tool_observations": [{"role": role, "tool": tool_name,
                                        "observation": observation}],
                "messages": [{"from": role, "tool": tool_name, "observation": observation}]}

    async def run(self, *, run_id: str, task: Any, level, config: Mapping[str, Any] | None = None,
                  resume: bool = False, interrupt_after: list[str] | None = None) -> EngineResult:
        config = {**(config or {}), "engine": self}
        level_name = getattr(level, "name", config.get("level", "unknown"))
        initial = initial_state(run_id=run_id, task=task, level=level_name,
                                registry_version=self.registry.version)
        graph = build_graph(level, task=task, registry=self.registry, config=config,
                            checkpointer=self.checkpointer, logger=self._log)
        invoke_config = {"configurable": {"thread_id": run_id}}
        options = {"config": invoke_config}
        if interrupt_after:
            options["interrupt_after"] = interrupt_after
        self._log({"event": "run_resumed" if resume else "run_started", "run_id": run_id,
                   "level": level_name, "registry_version": self.registry.version})
        state = await graph.ainvoke(None if resume else initial, **options)
        self._log({"event": "run_completed", "run_id": run_id, "level": level_name,
                   "status": state.get("status", "completed")})
        return EngineResult(run_id, state, resumed=resume)
