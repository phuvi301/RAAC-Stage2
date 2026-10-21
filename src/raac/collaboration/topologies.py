"""Finite collaboration layouts; edges include possible conditional paths."""

from dataclasses import dataclass
from types import MappingProxyType


@dataclass(frozen=True)
class TopologyStep:
    node_id: str
    role: str | None


@dataclass(frozen=True)
class TopologySpec:
    """None roles are control nodes. Builders must route conditional edges.

    START/END and exceptional exits are supplied by the graph builder.
    Role names identify core roles; the shared Engine resolves Registry IDs.
    """

    topology_id: str
    version: str
    steps: tuple[TopologyStep, ...]
    edges: tuple[tuple[str, str], ...]
    entry_node: str
    exit_node: str

    def incoming_nodes(self, node_id: str) -> tuple[str, ...]:
        if not any(step.node_id == node_id for step in self.steps):
            raise ValueError(f"Unknown node {node_id!r} in {self.topology_id!r}")
        return tuple(source for source, target in self.edges if target == node_id)


CHAIN = TopologySpec(
    topology_id="chain", version="5",
    steps=(
        TopologyStep("environment", "Environment"),
        TopologyStep("analyst", "Analyst"),
        TopologyStep("planner", "Planner"),
        TopologyStep("coder", "Coder"),
        TopologyStep("tester", "Tester"),
        TopologyStep("check", None),
        TopologyStep("limit", None),
        TopologyStep("reviewer", "Reviewer"),
        TopologyStep("result", None),
    ),
    edges=(
        ("environment", "analyst"), ("analyst", "planner"),
        ("planner", "coder"), ("coder", "tester"), ("tester", "check"),
        ("check", "reviewer"), ("check", "limit"),
        ("limit", "coder"), ("limit", "reviewer"),
        ("reviewer", "result"),
    ),
    entry_node="environment", exit_node="result",
)

TOPOLOGIES = MappingProxyType({CHAIN.topology_id: CHAIN})


def get_topology(topology_id: str) -> TopologySpec:
    """Select a declared layout; never create nodes/edges from LLM output."""
    try:
        return TOPOLOGIES[topology_id]
    except KeyError:
        raise ValueError(
            f"Unsupported topology {topology_id!r}; available: {', '.join(TOPOLOGIES)}"
        ) from None
