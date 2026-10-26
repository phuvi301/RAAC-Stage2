"""Coordinator for the demo: topology and rounds are set by hand (config or CLI); no adaptive selection yet."""

from typing import Literal

from pydantic import Field, model_validator

from raac.collaboration.topologies import get_topology

from .chain_graph import build_chain_graph
from .chain_state import StructuredOutput


class CoordinatorConfig(StructuredOutput):
    version: str = Field(min_length=1)
    topology: Literal["chain", "chain_review_loop"] = "chain"
    max_rounds: int = Field(ge=1)
    hard_max_rounds: int = Field(ge=1)
    max_review_rounds: int = Field(default=2, ge=0)

    @model_validator(mode="after")
    def within_hard_limit(self):
        if self.max_rounds > self.hard_max_rounds:
            raise ValueError("max_rounds exceeds hard_max_rounds")
        if self.max_review_rounds > self.hard_max_rounds:
            raise ValueError("max_review_rounds exceeds hard_max_rounds")
        return self


class CoordinatorDecision(StructuredOutput):
    topology: Literal["chain", "chain_review_loop"] = "chain"
    topology_version: str = "5"
    max_rounds: int = Field(ge=1)
    max_review_rounds: int | None = Field(default=None, ge=0)  # only set for topologies with a Reviewer -> Coder edge
    source: Literal["fixed_default"] = "fixed_default"


def coordinate_chain(config: dict, *, max_rounds: int | None = None, topology: str | None = None,
                     max_review_rounds: int | None = None) -> CoordinatorDecision:
    """
    Trả về quyết định của coordinator dựa trên cấu hình đã cho. gồm topology, phiên bản topology, số vòng tối đa và nguồn quyết định.
    Coordinator hiện tại chọn theo cấu hình, không gọi LLM để quyết định.
    `topology`, `max_rounds` và `max_review_rounds` truyền vào (ví dụ từ CLI) ghi đè giá trị trong cấu hình.
    `max_review_rounds` chỉ có nghĩa với topology có cạnh Reviewer → Coder; các topology khác ghi None.
    """
    settings = CoordinatorConfig.model_validate(config)
    rounds = settings.max_rounds if max_rounds is None else max_rounds
    chosen = settings.topology if topology is None else topology
    review_rounds = settings.max_review_rounds if max_review_rounds is None else max_review_rounds
    settings = CoordinatorConfig.model_validate({**settings.model_dump(), "max_rounds": rounds, "topology": chosen,
                                                 "max_review_rounds": review_rounds})
    spec = get_topology(settings.topology)
    return CoordinatorDecision(
        topology=spec.topology_id,
        topology_version=spec.version,
        max_rounds=settings.max_rounds,
        max_review_rounds=settings.max_review_rounds if ("reviewer", "coder") in spec.edges else None,
    )


def build_collaboration_graph(decision: CoordinatorDecision, nodes, *, checkpointer=None):
    """Dispatch only to implemented graph factories after the coordinator runs."""
    topology = get_topology(decision.topology)
    if decision.topology_version != topology.version:
        raise ValueError("Coordinator topology version does not match implementation")
    if decision.topology == "chain":
        return build_chain_graph(nodes, checkpointer=checkpointer)
    if decision.topology == "chain_review_loop":
        return build_chain_graph(nodes, checkpointer=checkpointer, topology=topology)
    raise ValueError(f"No graph factory for topology {decision.topology!r}")
