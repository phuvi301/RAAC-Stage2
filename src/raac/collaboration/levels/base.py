"""Interface implemented by collaboration levels."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Mapping, Protocol

from raac.collaboration.state import CollaborationState

Node = Callable[[CollaborationState], Mapping[str, Any] | Awaitable[Mapping[str, Any]]]


@dataclass(frozen=True)
class LevelGraph:
    """Declarative graph returned by a level after inspecting task/registry/config."""

    nodes: Mapping[str, Node]
    edges: tuple[tuple[str, str], ...]
    entry_node: str
    exit_node: str | None = None
    conditional_edges: Mapping[str, tuple[Callable[[CollaborationState], str], Mapping[str, str]]] = field(default_factory=dict)


class Level(Protocol):
    name: str

    def build_graph(self, task: Any, registry: Any, config: Mapping[str, Any]) -> LevelGraph:
        """Receive task, registry and config; return this level's graph contract."""
        ...


class BaseLevel:
    """Convenience base class for levels with a stable name."""

    name = "level"

    def build_graph(self, task: Any, registry: Any, config: Mapping[str, Any]) -> LevelGraph:
        raise NotImplementedError
