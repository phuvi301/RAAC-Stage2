"""L1 Fixed collaboration level: linear Analyst -> Planner -> Coder -> Tester -> Reviewer."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from raac.collaboration.levels.base import BaseLevel, LevelGraph
from raac.collaboration.state import CollaborationState
from raac.collaboration.topologies import TopologySpec, TopologyStep

L1_ROLES: tuple[str, ...] = ("Analyst", "Planner", "Coder", "Tester", "Reviewer")

L1_LINEAR = TopologySpec(
    topology_id="linear",
    version="1",
    steps=(
        TopologyStep("analyst", "Analyst"),
        TopologyStep("planner", "Planner"),
        TopologyStep("coder", "Coder"),
        TopologyStep("tester", "Tester"),
        TopologyStep("reviewer", "Reviewer"),
    ),
    edges=(
        ("analyst", "planner"),
        ("planner", "coder"),
        ("coder", "tester"),
        ("tester", "reviewer"),
    ),
    entry_node="analyst",
    exit_node="reviewer",
)


class L1Config(BaseModel):
    """Fixed orchestration parameters for L1 (no routing, topology or team changes)."""

    model_config = ConfigDict(strict=True, extra="forbid")

    version: str = Field(default="l1-fixed-v1", min_length=1)
    level: Literal["L1"] = "L1"
    topology: Literal["linear"] = "linear"
    roles: tuple[str, ...] = L1_ROLES
    max_rounds: int = Field(default=1, ge=1, le=1)
    hard_max_rounds: int = Field(default=1, ge=1, le=1)

    @model_validator(mode="before")
    @classmethod
    def _coerce_roles(cls, data: Any) -> Any:
        if isinstance(data, Mapping):
            cleaned = {k: v for k, v in data.items() if k != "engine"}
            if isinstance(cleaned.get("roles"), list):
                cleaned["roles"] = tuple(cleaned["roles"])
            return cleaned
        return data

    @model_validator(mode="after")
    def _validate_fixed_roles(self) -> L1Config:
        if self.roles != L1_ROLES:
            raise ValueError(f"L1 roles are fixed to {list(L1_ROLES)}; got {list(self.roles)}")
        if self.max_rounds != 1 or self.hard_max_rounds != 1:
            raise ValueError("L1 fixed workflow runs exactly 1 round")
        return self


def validate_l1_config(config: Mapping[str, Any] | None = None) -> L1Config:
    """Validate L1 config while ignoring runtime-injected keys like ``engine``."""
    return L1Config.model_validate(dict(config or {}))


def _require_engine(config: Mapping[str, Any]):
    engine = config.get("engine")
    if engine is None:
        raise RuntimeError("L1Fixed node execution requires 'engine' in config")
    return engine


def _tests_passed(test_results: Any) -> bool:
    if not isinstance(test_results, Mapping):
        return False
    verdict = str(test_results.get("verdict", "")).lower()
    results = test_results.get("results")
    if not isinstance(results, list) or not results:
        return False
    any_failed = any(
        isinstance(item, Mapping) and item.get("status") in {"failed", "error"}
        for item in results
    )
    return verdict == "pass" and not any_failed and not test_results.get("blocking_issues")


def _review_passed(review: Any) -> tuple[str, str]:
    if not isinstance(review, Mapping):
        return "REJECT", "invalid_review_output"
    decision = str(review.get("decision", "")).lower()
    comments = review.get("comments") if isinstance(review.get("comments"), list) else []
    has_blocker = any(
        isinstance(item, Mapping) and item.get("severity") == "blocker"
        for item in comments
    )
    if decision == "approve" and not has_blocker:
        return "PASS", "tests_and_review_passed"
    return "REJECT", "reviewer_rejected"


def _failed_result(state: CollaborationState, settings: L1Config) -> dict[str, Any]:
    error = state.get("error") or {}
    reason = error.get("kind") or "agent_failed"
    return {
        "run_id": state.get("run_id"),
        "level": settings.level,
        "topology": L1_LINEAR.topology_id,
        "topology_version": L1_LINEAR.version,
        "roles": list(settings.roles),
        "status": "aborted",
        "verdict": None,
        "stop_reason": reason,
        "error": error,
        "rounds_completed": state.get("rounds", 1),
        "analysis": state.get("analysis"),
        "plan": state.get("plan"),
        "patch": state.get("patch"),
        "test_results": state.get("test_results"),
        "review": state.get("review"),
    }


class L1Fixed(BaseLevel):
    """Level 1 Fixed: linear 5-role pipeline with predetermined edges and 1 round."""

    name = "L1"
    topology = L1_LINEAR
    roles = L1_ROLES

    def build_graph(self, task: Any, registry: Any, config: Mapping[str, Any]) -> LevelGraph:
        if task is None:
            raise ValueError("L1Fixed requires a task")
        if registry is None:
            raise ValueError("L1Fixed requires a registry")
        settings = validate_l1_config(config)
        for role_name in settings.roles:
            registry.get(role_name)

        async def analyst(state: CollaborationState) -> Mapping[str, Any]:
            if state.get("status") == "failed" or state.get("error"):
                return {"status": "failed", "result": _failed_result(state, settings)}
            engine = _require_engine(config)
            requirement = state.get("requirement", "")
            update = await engine.invoke_agent(
                "Analyst",
                {**state, "rounds": 1},
                incoming_message={"from": "caller", "requirement": requirement},
                output_key="analysis",
            )
            return {**update, "rounds": 1}

        async def planner(state: CollaborationState) -> Mapping[str, Any]:
            if state.get("status") == "failed" or state.get("error"):
                return {"status": "failed", "result": _failed_result(state, settings)}
            engine = _require_engine(config)
            analysis = state.get("analysis")
            return await engine.invoke_agent(
                "Planner",
                state,
                incoming_message={"from": "Analyst", "content": analysis},
                artifacts={"spec": analysis},
                output_key="plan",
            )

        async def coder(state: CollaborationState) -> Mapping[str, Any]:
            if state.get("status") == "failed" or state.get("error"):
                return {"status": "failed", "result": _failed_result(state, settings)}
            engine = _require_engine(config)
            analysis = state.get("analysis")
            plan = state.get("plan")
            return await engine.invoke_agent(
                "Coder",
                state,
                incoming_message={"from": "Planner", "content": plan},
                artifacts={"spec": analysis, "plan": plan},
                output_key="patch",
            )

        async def tester(state: CollaborationState) -> Mapping[str, Any]:
            if state.get("status") == "failed" or state.get("error"):
                return {"status": "failed", "result": _failed_result(state, settings)}
            engine = _require_engine(config)
            analysis = state.get("analysis")
            plan = state.get("plan")
            patch = state.get("patch") if isinstance(state.get("patch"), str) else ""
            return await engine.invoke_agent(
                "Tester",
                state,
                incoming_message={"from": "Coder", "patch": patch},
                artifacts={"spec": analysis, "plan": plan, "patch": patch},
                output_key="test_results",
            )

        async def reviewer(state: CollaborationState) -> Mapping[str, Any]:
            if state.get("status") == "failed" or state.get("error"):
                return {"status": "failed", "result": _failed_result(state, settings)}
            engine = _require_engine(config)
            analysis = state.get("analysis")
            plan = state.get("plan")
            patch = state.get("patch") if isinstance(state.get("patch"), str) else ""
            test_results = state.get("test_results")
            update = await engine.invoke_agent(
                "Reviewer",
                state,
                incoming_message={"from": "Tester", "content": test_results},
                artifacts={
                    "spec": analysis,
                    "plan": plan,
                    "patch": patch,
                    "test_report": test_results,
                },
                output_key="review",
            )
            review = update.get("review")
            review_verdict, stop_reason = _review_passed(review)
            tests_ok = _tests_passed(test_results)
            if review_verdict == "PASS" and tests_ok:
                verdict = "PASS"
                status = "completed"
                reason = "tests_and_review_passed"
            else:
                verdict = "REJECT"
                status = "rejected"
                reason = stop_reason if review_verdict != "PASS" else "tests_failed"
            result = {
                "run_id": state.get("run_id"),
                "level": settings.level,
                "topology": L1_LINEAR.topology_id,
                "topology_version": L1_LINEAR.version,
                "roles": list(settings.roles),
                "status": status,
                "verdict": verdict,
                "stop_reason": reason,
                "rounds_completed": state.get("rounds", 1),
                "analysis": analysis,
                "plan": plan,
                "patch": patch,
                "test_results": test_results,
                "review": review,
            }
            return {**update, "status": status, "result": result}

        return LevelGraph(
            nodes={
                "analyst": analyst,
                "planner": planner,
                "coder": coder,
                "tester": tester,
                "reviewer": reviewer,
            },
            edges=L1_LINEAR.edges,
            entry_node=L1_LINEAR.entry_node,
            exit_node=L1_LINEAR.exit_node,
        )
