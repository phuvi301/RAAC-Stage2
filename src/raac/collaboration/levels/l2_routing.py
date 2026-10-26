"""L2 Adaptive Routing: Router selects a subset of L1 roles (always keeping Coder and Tester)."""
from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from raac.collaboration.levels.base import BaseLevel, LevelGraph
from raac.collaboration.levels.l1_fixed import (
    L1_ROLES,
    _require_engine,
    _review_passed,
    _tests_passed,
)
from raac.collaboration.state import CollaborationState
from raac.collaboration.topologies import TopologySpec, TopologyStep
from raac.common.agent_contracts import (
    OutputFormatError,
    RouterOutput,
    parse_role_output,
)

L2_REQUIRED_ROLES: tuple[str, ...] = ("Coder", "Tester")
L2_OPTIONAL_ROLES: tuple[str, ...] = ("Analyst", "Planner", "Reviewer")

L2_ROUTED_LINEAR = TopologySpec(
    topology_id="linear-routed",
    version="1",
    steps=(
        TopologyStep("router", "Router"),
        TopologyStep("analyst", "Analyst"),
        TopologyStep("planner", "Planner"),
        TopologyStep("coder", "Coder"),
        TopologyStep("tester", "Tester"),
        TopologyStep("reviewer", "Reviewer"),
        TopologyStep("finish", None),
    ),
    edges=(
        ("router", "analyst"),
        ("router", "planner"),
        ("router", "coder"),
        ("analyst", "planner"),
        ("analyst", "coder"),
        ("planner", "coder"),
        ("coder", "tester"),
        ("tester", "reviewer"),
        ("tester", "finish"),
        ("reviewer", "finish"),
    ),
    entry_node="router",
    exit_node="finish",
)


class L2Config(BaseModel):
    """Fixed catalog and routing boundaries for L2 Adaptive Routing."""

    model_config = ConfigDict(strict=True, extra="forbid")

    version: str = Field(default="l2-routing-v1", min_length=1)
    level: Literal["L2"] = "L2"
    topology: Literal["linear-routed"] = "linear-routed"
    router_role: Literal["Router"] = "Router"
    roles: tuple[str, ...] = L1_ROLES
    required_roles: tuple[str, ...] = L2_REQUIRED_ROLES
    optional_roles: tuple[str, ...] = L2_OPTIONAL_ROLES
    fallback: Literal["L1"] = "L1"
    max_rounds: int = Field(default=1, ge=1, le=1)
    hard_max_rounds: int = Field(default=1, ge=1, le=1)

    @model_validator(mode="before")
    @classmethod
    def _coerce_sequences(cls, data: Any) -> Any:
        if isinstance(data, Mapping):
            cleaned = {k: v for k, v in data.items() if k != "engine"}
            for key in ("roles", "required_roles", "optional_roles"):
                if isinstance(cleaned.get(key), list):
                    cleaned[key] = tuple(cleaned[key])
            return cleaned
        return data

    @model_validator(mode="after")
    def _validate_l2_rules(self) -> L2Config:
        if self.roles != L1_ROLES:
            raise ValueError(f"L2 catalog is fixed to {list(L1_ROLES)}; got {list(self.roles)}")
        if not set(L2_REQUIRED_ROLES).issubset(self.required_roles):
            raise ValueError("L2 required_roles must always include Coder and Tester")
        if not set(self.required_roles).issubset(self.roles):
            raise ValueError("L2 required_roles must be a subset of the fixed role catalog")
        expected_optional = tuple(r for r in self.roles if r not in self.required_roles)
        if set(self.optional_roles) != set(expected_optional):
            raise ValueError("L2 optional_roles must complement required_roles within the fixed catalog")
        if self.max_rounds != 1 or self.hard_max_rounds != 1:
            raise ValueError("L2 routing runs a single forward pass without adaptive loop counts")
        return self


def validate_l2_config(config: Mapping[str, Any] | None = None) -> L2Config:
    """Validate L2 config while ignoring runtime-injected keys like ``engine``."""
    return L2Config.model_validate(dict(config or {}))


def parse_router_decision(raw: Any, settings: L2Config) -> RouterOutput:
    """Validate router output against RouterOutput schema and L2Config required_roles."""
    if isinstance(raw, RouterOutput):
        parsed = raw
    elif isinstance(raw, str):
        parsed = parse_role_output("router", raw)
    elif isinstance(raw, Mapping):
        try:
            parsed = RouterOutput.model_validate(dict(raw))
        except ValidationError as exc:
            raise OutputFormatError(f"router: {exc}") from exc
    else:
        raise OutputFormatError("router: output must be a JSON object")
    if not set(settings.required_roles).issubset(parsed.selected_roles):
        raise OutputFormatError("router: selected_roles missing required roles")
    return parsed


def _fallback_routing(settings: L2Config, fallback_reason: str) -> dict[str, Any]:
    return {
        "source": "fallback_l1",
        "selected_roles": list(settings.roles),
        "skipped": [],
        "reason": f"Fallback to full L1 workflow ({fallback_reason})",
        "fallback_reason": fallback_reason,
    }


def _next_node_after(current_role: str | None, state: CollaborationState) -> str:
    if state.get("status") == "failed" or state.get("error"):
        return "finish"
    routing = state.get("routing") or {}
    selected = routing.get("selected_roles") or list(L1_ROLES)
    order = [role for role in L1_ROLES if role in selected]
    if current_role is None:
        return order[0].lower() if order else "finish"
    if current_role not in order:
        idx_in_catalog = L1_ROLES.index(current_role)
        remaining = [r for r in L1_ROLES[idx_in_catalog + 1 :] if r in selected]
        return remaining[0].lower() if remaining else "finish"
    idx = order.index(current_role)
    return order[idx + 1].lower() if idx + 1 < len(order) else "finish"


class L2Routing(BaseLevel):
    """Level 2 Adaptive Routing: Router activates a subset of the fixed 5-role pipeline."""

    name = "L2"
    topology = L2_ROUTED_LINEAR
    roles = L1_ROLES

    def build_graph(self, task: Any, registry: Any, config: Mapping[str, Any]) -> LevelGraph:
        if task is None:
            raise ValueError("L2Routing requires a task")
        if registry is None:
            raise ValueError("L2Routing requires a registry")
        settings = validate_l2_config(config)
        registry.get(settings.router_role)
        for role_name in settings.roles:
            registry.get(role_name)

        async def router(state: CollaborationState) -> Mapping[str, Any]:
            engine = _require_engine(config)
            requirement = state.get("requirement", "")
            raw_output: Any = None
            agent_updates: dict[str, Any] = {}
            fallback_reason: str | None = None
            try:
                agent_updates = dict(
                    await engine.invoke_agent(
                        settings.router_role,
                        {**state, "rounds": 1},
                        incoming_message={"from": "caller", "requirement": requirement},
                        output_key="routing_raw",
                    )
                )
                raw_output = agent_updates.get("routing_raw")
                if isinstance(raw_output, str):
                    try:
                        raw_json = json.loads(raw_output)
                    except ValueError:
                        raw_json = raw_output
                else:
                    raw_json = raw_output
                decision = parse_router_decision(raw_json, settings)
                routing = {
                    "source": "router",
                    "selected_roles": list(decision.selected_roles),
                    "skipped": [item.model_dump(mode="json") for item in decision.skipped],
                    "reason": decision.reason,
                    "fallback_reason": None,
                }
            except Exception as exc:  # noqa: BLE001 -- invalid router output or call failure falls back to L1.
                fallback_reason = type(exc).__name__
                routing = _fallback_routing(settings, fallback_reason)
                engine._log(
                    {
                        "event": "routing_fallback",
                        "run_id": state.get("run_id"),
                        "level": settings.level,
                        "fallback": settings.fallback,
                        "fallback_reason": fallback_reason,
                        "selected_roles": routing["selected_roles"],
                    }
                )

            engine._log(
                {
                    "event": "routing_decided",
                    "run_id": state.get("run_id"),
                    "level": settings.level,
                    "source": routing["source"],
                    "selected_roles": routing["selected_roles"],
                    "skipped": routing["skipped"],
                    "reason": routing["reason"],
                    "fallback_reason": routing["fallback_reason"],
                }
            )
            for item in routing["skipped"]:
                engine._log(
                    {
                        "event": "agent_skipped",
                        "run_id": state.get("run_id"),
                        "level": settings.level,
                        "agent_id": item["role"],
                        "reason": item["reason"],
                    }
                )
            return {**agent_updates, "routing": routing, "rounds": 1}

        async def analyst(state: CollaborationState) -> Mapping[str, Any]:
            if state.get("status") == "failed" or state.get("error"):
                return {}
            engine = _require_engine(config)
            requirement = state.get("requirement", "")
            return await engine.invoke_agent(
                "Analyst",
                state,
                incoming_message={"from": "Router", "requirement": requirement},
                output_key="analysis",
            )

        async def planner(state: CollaborationState) -> Mapping[str, Any]:
            if state.get("status") == "failed" or state.get("error"):
                return {}
            engine = _require_engine(config)
            analysis = state.get("analysis")
            sender = "Analyst" if analysis is not None else "Router"
            content = analysis if analysis is not None else state.get("requirement", "")
            artifacts = {"spec": analysis} if analysis is not None else {}
            return await engine.invoke_agent(
                "Planner",
                state,
                incoming_message={"from": sender, "content": content},
                artifacts=artifacts,
                output_key="plan",
            )

        async def coder(state: CollaborationState) -> Mapping[str, Any]:
            if state.get("status") == "failed" or state.get("error"):
                return {}
            engine = _require_engine(config)
            analysis = state.get("analysis")
            plan = state.get("plan")
            if plan is not None:
                sender, content = "Planner", plan
            elif analysis is not None:
                sender, content = "Analyst", analysis
            else:
                sender, content = "Router", state.get("requirement", "")
            artifacts: dict[str, Any] = {}
            if analysis is not None:
                artifacts["spec"] = analysis
            if plan is not None:
                artifacts["plan"] = plan
            return await engine.invoke_agent(
                "Coder",
                state,
                incoming_message={"from": sender, "content": content},
                artifacts=artifacts,
                output_key="patch",
            )

        async def tester(state: CollaborationState) -> Mapping[str, Any]:
            if state.get("status") == "failed" or state.get("error"):
                return {}
            engine = _require_engine(config)
            analysis = state.get("analysis")
            plan = state.get("plan")
            patch = state.get("patch") if isinstance(state.get("patch"), str) else ""
            artifacts: dict[str, Any] = {"patch": patch}
            if analysis is not None:
                artifacts["spec"] = analysis
            if plan is not None:
                artifacts["plan"] = plan
            return await engine.invoke_agent(
                "Tester",
                state,
                incoming_message={"from": "Coder", "patch": patch},
                artifacts=artifacts,
                output_key="test_results",
            )

        async def reviewer(state: CollaborationState) -> Mapping[str, Any]:
            if state.get("status") == "failed" or state.get("error"):
                return {}
            engine = _require_engine(config)
            analysis = state.get("analysis")
            plan = state.get("plan")
            patch = state.get("patch") if isinstance(state.get("patch"), str) else ""
            test_results = state.get("test_results")
            artifacts: dict[str, Any] = {"patch": patch, "test_report": test_results}
            if analysis is not None:
                artifacts["spec"] = analysis
            if plan is not None:
                artifacts["plan"] = plan
            return await engine.invoke_agent(
                "Reviewer",
                state,
                incoming_message={"from": "Tester", "content": test_results},
                artifacts=artifacts,
                output_key="review",
            )

        def finish(state: CollaborationState) -> Mapping[str, Any]:
            routing = state.get("routing") or _fallback_routing(settings, "missing_routing")
            selected = routing.get("selected_roles") or list(settings.roles)
            if state.get("status") == "failed" or state.get("error"):
                error = state.get("error") or {}
                reason = error.get("kind") or "agent_failed"
                return {
                    "status": "failed",
                    "result": {
                        "run_id": state.get("run_id"),
                        "level": settings.level,
                        "topology": L2_ROUTED_LINEAR.topology_id,
                        "topology_version": L2_ROUTED_LINEAR.version,
                        "roles": list(selected),
                        "routing": routing,
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
                    },
                }

            test_results = state.get("test_results")
            review = state.get("review")
            tests_ok = _tests_passed(test_results)
            if "Reviewer" in selected:
                review_verdict, stop_reason = _review_passed(review)
                if review_verdict == "PASS" and tests_ok:
                    verdict, status, reason = "PASS", "completed", "tests_and_review_passed"
                else:
                    verdict, status = "REJECT", "rejected"
                    reason = stop_reason if review_verdict != "PASS" else "tests_failed"
            else:
                if tests_ok:
                    verdict, status, reason = "PASS", "completed", "tests_passed_no_review"
                else:
                    verdict, status, reason = "REJECT", "rejected", "tests_failed"

            result = {
                "run_id": state.get("run_id"),
                "level": settings.level,
                "topology": L2_ROUTED_LINEAR.topology_id,
                "topology_version": L2_ROUTED_LINEAR.version,
                "roles": list(selected),
                "routing": routing,
                "status": status,
                "verdict": verdict,
                "stop_reason": reason,
                "rounds_completed": state.get("rounds", 1),
                "analysis": state.get("analysis"),
                "plan": state.get("plan"),
                "patch": state.get("patch"),
                "test_results": test_results,
                "review": review,
            }
            return {"status": status, "result": result}

        nodes = {
            "router": router,
            "analyst": analyst,
            "planner": planner,
            "coder": coder,
            "tester": tester,
            "reviewer": reviewer,
            "finish": finish,
        }
        conditional_edges = {
            "router": (
                lambda s: _next_node_after(None, s),
                {"analyst": "analyst", "planner": "planner", "coder": "coder", "finish": "finish"},
            ),
            "analyst": (
                lambda s: _next_node_after("Analyst", s),
                {"planner": "planner", "coder": "coder", "finish": "finish"},
            ),
            "planner": (
                lambda s: _next_node_after("Planner", s),
                {"coder": "coder", "finish": "finish"},
            ),
            "coder": (
                lambda s: _next_node_after("Coder", s),
                {"tester": "tester", "finish": "finish"},
            ),
            "tester": (
                lambda s: _next_node_after("Tester", s),
                {"reviewer": "reviewer", "finish": "finish"},
            ),
        }
        return LevelGraph(
            nodes=nodes,
            edges=(("reviewer", "finish"),),
            entry_node="router",
            exit_node="finish",
            conditional_edges=conditional_edges,
        )
