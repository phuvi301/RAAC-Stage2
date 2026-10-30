"""Build a LangGraph collaboration graph from a level contract."""
from __future__ import annotations

import inspect
from typing import Any, Mapping

from langgraph.graph import END, START, StateGraph

from .levels.base import LevelGraph
from .state import CollaborationState


def _level_graph(level, task, registry, config) -> LevelGraph:
    if isinstance(level, LevelGraph):
        return level
    builder = getattr(level, "build_graph", None)
    if builder is None:
        raise TypeError("level must implement build_graph(task, registry, config)")
    graph = builder(task, registry, config)
    if inspect.isawaitable(graph):
        raise TypeError("level build_graph must be synchronous")
    if not isinstance(graph, LevelGraph):
        raise TypeError("build_graph must return LevelGraph")
    return graph


def build_graph(level, *, task: Any, registry: Any, config: Mapping[str, Any] | None = None, checkpointer=None,
                logger=None):
    """Compile a level graph and wrap every node with collaboration lifecycle logging."""
    config = config or {}
    definition = _level_graph(level, task, registry, config)
    node_names = set(definition.nodes)
    if not definition.entry_node or definition.entry_node not in node_names:
        raise ValueError("level entry_node must name a declared node")
    if definition.exit_node is not None and definition.exit_node not in node_names:
        raise ValueError("level exit_node must name a declared node")
    for source, target in definition.edges:
        if source not in node_names or target not in node_names:
            raise ValueError(f"edge references undeclared node: {source!r} -> {target!r}")
    for source, (_, targets) in definition.conditional_edges.items():
        if source not in node_names or any(target not in node_names for target in targets.values()):
            raise ValueError(f"conditional edge references undeclared node: {source!r}")

    graph = StateGraph(CollaborationState)
    for name, node in definition.nodes.items():
        graph.add_node(name, _wrap_node(name, node, logger))
    graph.add_edge(START, definition.entry_node)
    outgoing: dict[str, list[str]] = {}
    for source, target in definition.edges:
        outgoing.setdefault(source, []).append(target)
    for source, (router, targets) in definition.conditional_edges.items():
        graph.add_conditional_edges(source, router, dict(targets))
    for source, targets in outgoing.items():
        if source in definition.conditional_edges:
            continue
        if len(targets) != 1:
            raise ValueError(f"node {source!r} has multiple edges; use conditional_edges")
        graph.add_edge(source, targets[0])
    if definition.exit_node:
        graph.add_edge(definition.exit_node, END)
    return graph.compile(checkpointer=checkpointer)


def _wrap_node(name, node, logger):
    async def wrapped(state: CollaborationState):
        event = {"event": "agent_started", "agent_id": name, "run_id": state.get("run_id"),
                 "level": state.get("level"), "round": state.get("rounds", 0)}
        if logger:
            logger(event)
        try:
            result = node({**state, "active_agent": name})
            if inspect.isawaitable(result):
                result = await result
            if not isinstance(result, Mapping):
                raise TypeError(f"node {name!r} must return a mapping")
            completed = {"event": "agent_completed", "agent_id": name, "run_id": state.get("run_id"),
                         "level": state.get("level"), "round": state.get("rounds", 0)}
            if logger:
                logger(completed)
            return {**result, "active_agent": None,
                    "events": [event, completed]}
        except Exception as exc:
            failed = {"event": "agent_failed", "agent_id": name, "run_id": state.get("run_id"),
                      "level": state.get("level"), "error": type(exc).__name__}
            if logger:
                logger(failed)
            return {"active_agent": None, "status": "failed",
                    "error": {"agent_id": name, "kind": type(exc).__name__},
                    "events": [event, failed]}
    return wrapped
