"""One graph per agent activation; persisted phase chooses its entry point."""

from langgraph.graph import END, START, StateGraph


def build_event_loop(runner):
    builder = StateGraph(dict)
    phases = ("reason", "decide", "act", "observe", "finalize")
    for phase in phases:

        async def step(state, phase=phase):
            return await runner._step(state, phase)

        builder.add_node(phase, step)
    routes = {phase: phase for phase in phases} | {"done": END}
    builder.add_conditional_edges(START, lambda state: state["phase"], routes)
    for phase in phases:
        builder.add_conditional_edges(phase, lambda state: state["phase"], routes)
    return builder.compile()
