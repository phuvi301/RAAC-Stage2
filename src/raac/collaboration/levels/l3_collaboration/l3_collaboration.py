"""Fixed coordinator for the demo, before adding adaptive LLM selection."""

from typing import Literal

from pydantic import Field, model_validator

from raac.collaboration.topologies import get_topology

from .chain_graph import build_chain_graph
from .chain_state import StructuredOutput


class CoordinatorConfig(StructuredOutput):
    version: str = Field(min_length=1)
    topology: Literal["chain"] = "chain"
    max_rounds: int = Field(ge=1)
    hard_max_rounds: int = Field(ge=1)

    @model_validator(mode="after")
    def within_hard_limit(self):
        if self.max_rounds > self.hard_max_rounds:
            raise ValueError("max_rounds exceeds hard_max_rounds")
        return self


class CoordinatorDecision(StructuredOutput):
    topology: Literal["chain"] = "chain"
    topology_version: str = "5"
    max_rounds: int = Field(ge=1)
    source: Literal["fixed_default"] = "fixed_default"


def coordinate_chain(config: dict, *, max_rounds: int | None = None) -> CoordinatorDecision:
    """
    Trả về quyết định của coordinator dựa trên cấu hình đã cho. gồm topology, phiên bản topology, số vòng tối đa và nguồn quyết định.
    Coordinator hiện tại chọn theo cấu hình, không gọi LLM để quyết định.
    """
    settings = CoordinatorConfig.model_validate(config)
    rounds = settings.max_rounds if max_rounds is None else max_rounds
    settings = CoordinatorConfig.model_validate({**settings.model_dump(), "max_rounds": rounds})
    topology = get_topology(settings.topology)
    return CoordinatorDecision(
        topology=topology.topology_id,                       
        topology_version=topology.version, 
        max_rounds=settings.max_rounds
    )


def build_collaboration_graph(decision: CoordinatorDecision, nodes, *, checkpointer=None):
    """Dispatch only to implemented graph factories after the coordinator runs."""
    topology = get_topology(decision.topology)
    if decision.topology_version != topology.version:
        raise ValueError("Coordinator topology version does not match implementation")
    factories = {"chain": build_chain_graph}
    return factories[decision.topology](nodes, checkpointer=checkpointer)
