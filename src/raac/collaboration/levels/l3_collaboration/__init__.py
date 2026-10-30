"""Chain demo with a fixed coordinator; adaptive selection/shared Engine are pending."""

from .chain_graph import build_chain_graph
from .chain_nodes import ChainNodes
from .chain_state import (
    AnalystOutput, PlannerOutput, CoderOutput, TesterOutput, ReviewerOutput,
    JSON_OUTPUT_MODELS, RoleHandoff, OutputFormatError, parse_role_output, role_output_model,
    ChainLimits, ChainState, WorkItem, initial_chain_state,
)
from .l3_collaboration import CoordinatorDecision, build_collaboration_graph, coordinate_chain

__all__ = [
    "AnalystOutput", "PlannerOutput", "CoderOutput", "TesterOutput", "ReviewerOutput",
    "JSON_OUTPUT_MODELS", "RoleHandoff", "OutputFormatError", "parse_role_output", "role_output_model",
    "ChainLimits", "ChainNodes", "ChainState", "WorkItem",
    "build_chain_graph", "initial_chain_state",
    "CoordinatorDecision", "build_collaboration_graph", "coordinate_chain",
]
