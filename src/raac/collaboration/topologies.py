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
    layout: str = "chain"  # drawing hint for viewers: chain | star

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

# Same roles and contracts as CHAIN; only the communication differs: a rejecting
# Reviewer may send its feedback back to Coder while rounds remain.
CHAIN_REVIEW_LOOP = TopologySpec(
    topology_id="chain_review_loop", version="1",
    steps=CHAIN.steps,
    edges=CHAIN.edges + (("reviewer", "coder"),),
    entry_node=CHAIN.entry_node, exit_node=CHAIN.exit_node,
)

_SPOKES = ("analyst", "planner", "coder", "tester", "reviewer")

# Star: every role reports to the Hub, which chooses the next role. Same five-role team plus
# Environment; check/limit are folded into the Hub's routing rules.
STAR = TopologySpec(
    topology_id="star", version="1",
    steps=(
        TopologyStep("environment", "Environment"),
        TopologyStep("hub", "Hub"),
        *(TopologyStep(node, node.capitalize()) for node in _SPOKES),
        TopologyStep("result", None),
    ),
    edges=(("environment", "hub"),
           *(edge for node in _SPOKES for edge in (("hub", node), (node, "hub"))),
           ("hub", "result")),
    entry_node="environment", exit_node="result", layout="star",
)

TOPOLOGIES = MappingProxyType({spec.topology_id: spec for spec in (CHAIN, CHAIN_REVIEW_LOOP, STAR)})


def get_topology(topology_id: str) -> TopologySpec:
    """Select a declared layout; never create nodes/edges from LLM output."""
    try:
        return TOPOLOGIES[topology_id]
    except KeyError:
        raise ValueError(
            f"Unsupported topology {topology_id!r}; available: {', '.join(TOPOLOGIES)}"
        ) from None
