"""Compose Chain nodes as a LangGraph graph, without replacing the shared Engine."""

from langgraph.graph import END, START, StateGraph

from raac.collaboration.topologies import CHAIN

from .chain_nodes import ChainNodes
from .chain_state import ChainState


def build_chain_graph(nodes: ChainNodes, *, checkpointer=None):
    """Build once, then invoke with fresh state or resume a saved checkpoint.

    Use ``config={"recursion_limit": 4 * max_rounds + 12}`` when invoking.
    The invocation's max_rounds is enforced by nodes, independently of this
    LangGraph superstep safety limit. Checkpoint version matching and agent
    lifecycle/workspace restoration remain responsibilities of the Engine.
    """
    builder = StateGraph(ChainState)
    for step in CHAIN.steps:
        builder.add_node(step.node_id, getattr(nodes, step.node_id))
    builder.add_edge(START, CHAIN.entry_node)
    for step in CHAIN.steps:
        if step.node_id == CHAIN.exit_node:
            builder.add_edge(step.node_id, END)
            continue
        targets = {target for source, target in CHAIN.edges if source == step.node_id}
        # Any agent can fail or be interrupted; result preserves partial work.
        targets.add("result")
        builder.add_conditional_edges(
            step.node_id, lambda state: state["next_node"],
            {target: target for target in targets},
        )
    return builder.compile(checkpointer=checkpointer)
