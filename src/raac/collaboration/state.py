"""State owned by collaboration orchestration, not an individual agent lifecycle."""
from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict


class CollaborationState(TypedDict, total=False):
    run_id: str
    task: Any
    requirement: str
    plan: Any
    patch: str | None
    test_results: Any
    messages: Annotated[list[Any], operator.add]
    agent_outputs: Annotated[list[dict[str, Any]], operator.add]
    tool_observations: Annotated[list[dict[str, Any]], operator.add]
    rounds: int
    active_agent: str | None
    level: str
    registry_version: str
    events: Annotated[list[dict[str, Any]], operator.add]
    result: Any
    status: str
    error: dict[str, str] | None


def initial_state(*, run_id: str, task: Any, level: str, registry_version: str) -> CollaborationState:
    """Create a fresh collaboration state; Layer 1 state is intentionally absent."""
    requirement = task.get("requirement", "") if isinstance(task, dict) else getattr(task, "requirement", "")
    return {
        "run_id": run_id,
        "task": task,
        "requirement": requirement,
        "plan": None,
        "patch": None,
        "test_results": None,
        "messages": [],
        "agent_outputs": [],
        "tool_observations": [],
        "rounds": 0,
        "active_agent": None,
        "level": level,
        "registry_version": registry_version,
        "events": [],
        "result": None,
        "status": "running",
        "error": None,
    }
