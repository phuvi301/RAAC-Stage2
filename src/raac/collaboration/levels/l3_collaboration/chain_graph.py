"""Compose Chain nodes as a LangGraph graph, without replacing the shared Engine."""

from langgraph.graph import END, START, StateGraph

from raac.collaboration.topologies import CHAIN, TopologySpec

from .chain_nodes import ChainNodes
from .chain_state import ChainState


def build_chain_graph(nodes: ChainNodes, *, checkpointer=None, topology: TopologySpec = CHAIN):
    """
    Tạo một đồ thị trạng thái từ các nút ChainNodes, với các cạnh được xác định bởi CHAIN.
    Các nút được thêm vào đồ thị theo thứ tự trong CHAIN.steps, 
    với các cạnh điều kiện được xác định bởi CHAIN.edges.
    """
    builder = StateGraph(ChainState)
    for step in topology.steps:
        builder.add_node(step.node_id, getattr(nodes, step.node_id))
    builder.add_edge(START, topology.entry_node)
    for step in topology.steps:
        if step.node_id == topology.exit_node:
            builder.add_edge(step.node_id, END)
            continue
        targets = {target for source, target in topology.edges if source == step.node_id}
        # Any agent can fail or be interrupted; result preserves partial work.
        targets.add("result")
        builder.add_conditional_edges(
            step.node_id, lambda state: state["next_node"],
            {target: target for target in targets},
        )
    return builder.compile(checkpointer=checkpointer)
