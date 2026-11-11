"""L1 Fixed collaboration level: a fixed workflow with declared feedback loops.

Requirement Analyst -> Architect -> Planner run once. Developer -> Tester -> Code Reviewer
then repeats for at most ``max_rounds`` Developer rounds: failing tests go back to the
Developer, a rejecting review goes back to the Developer, and the last round always passes
through the Code Reviewer before the run ends. Roles, edges, loop conditions and the round
cap are fixed before the run, so this stays L1 (no routing, topology or team changes).
"""
from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from raac.collaboration.levels.base import BaseLevel, LevelGraph
from raac.collaboration.state import CollaborationState
from raac.collaboration.topologies import TopologySpec, TopologyStep
from raac.common.report_context import compact_test_report

L1_ROLES: tuple[str, ...] = (
    "Requirement Analyst", "Architect", "Planner", "Developer", "Tester", "Code Reviewer",
)
# L1 labels are workflow positions; each one runs the Registry role that owns the matching
# contract, tool permissions and adapter handling (e.g. Coder diff export, Tester evidence).
L1_REGISTRY_ROLES: Mapping[str, str] = {
    "Requirement Analyst": "Analyst",
    "Architect": "Architect",
    "Planner": "Planner",
    "Developer": "Coder",
    "Tester": "Tester",
    "Code Reviewer": "Reviewer",
}
L1_MAX_ROUNDS = 3

L1_FIXED_WORKFLOW = TopologySpec(
    topology_id="fixed-workflow",
    version="2",
    steps=(
        TopologyStep("requirement_analyst", "Analyst"),
        TopologyStep("architect", "Architect"),
        TopologyStep("planner", "Planner"),
        TopologyStep("developer", "Coder"),
        TopologyStep("tester", "Tester"),
        TopologyStep("code_reviewer", "Reviewer"),
        TopologyStep("finish", None),
    ),
    edges=(
        ("requirement_analyst", "architect"),
        ("architect", "planner"),
        ("planner", "developer"),
        ("developer", "tester"),
        ("tester", "code_reviewer"),
        ("tester", "developer"),
        ("code_reviewer", "developer"),
        ("code_reviewer", "finish"),
    ),
    entry_node="requirement_analyst",
    exit_node="finish",
)
# Kept for callers that imported the previous name; the layout is now the fixed workflow.
L1_LINEAR = L1_FIXED_WORKFLOW


class L1Config(BaseModel):
    """Fixed orchestration parameters for L1 (no routing, topology or team changes)."""

    model_config = ConfigDict(strict=True, extra="forbid")

    version: str = Field(default="l1-fixed-v2", min_length=1)
    level: Literal["L1"] = "L1"
    topology: Literal["fixed-workflow"] = "fixed-workflow"
    roles: tuple[str, ...] = L1_ROLES
    max_rounds: int = Field(default=L1_MAX_ROUNDS, ge=1, le=L1_MAX_ROUNDS)
    hard_max_rounds: int = Field(default=L1_MAX_ROUNDS, ge=1, le=L1_MAX_ROUNDS)

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
        if self.max_rounds > self.hard_max_rounds:
            raise ValueError("L1 max_rounds exceeds hard_max_rounds")
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


def _failed(state: CollaborationState) -> bool:
    return state.get("status") == "failed" or bool(state.get("error"))


def _patch(state: CollaborationState) -> str:
    patch = state.get("patch")
    return patch if isinstance(patch, str) else ""


def _patch_ref(patch: str) -> dict[str, Any]:
    return {"patch_sha256": hashlib.sha256(patch.encode("utf-8")).hexdigest(), "chars": len(patch)}


def _base_result(state: CollaborationState, settings: L1Config) -> dict[str, Any]:
    return {
        "run_id": state.get("run_id"),
        "level": settings.level,
        "topology": L1_FIXED_WORKFLOW.topology_id,
        "topology_version": L1_FIXED_WORKFLOW.version,
        "roles": list(settings.roles),
        "max_rounds": settings.max_rounds,
        "rounds_completed": state.get("rounds", 0),
        "analysis": state.get("analysis"),
        "architecture": state.get("architecture"),
        "plan": state.get("plan"),
        "patch": state.get("patch"),
        "test_results": state.get("test_results"),
        "review": state.get("review"),
        "iterations": list(state.get("iterations") or []),
    }


def outcome(state: CollaborationState, settings: L1Config) -> dict[str, Any]:
    """Terminal status for a finished workflow. Failing tests never produce PASS."""
    if _failed(state):
        error = state.get("error") or {}
        return {"status": "aborted", "verdict": None,
                "stop_reason": error.get("kind") or "agent_failed", "error": error}
    tests_ok = _tests_passed(state.get("test_results"))
    review_verdict, review_reason = _review_passed(state.get("review"))
    final_round = state.get("rounds", 0) >= settings.max_rounds
    if tests_ok and review_verdict == "PASS":
        return {"status": "completed", "verdict": "PASS", "stop_reason": "tests_and_review_passed"}
    if not tests_ok:
        # The last round still went through the Code Reviewer; the workflow finished but
        # the patch is not accepted. Reviewer approval cannot waive failing tests.
        return {"status": "completed_with_failures", "verdict": "REJECT",
                "stop_reason": "max_rounds_reached" if final_round else "tests_failed"}
    return {"status": "rejected", "verdict": "REJECT",
            "stop_reason": "max_rounds_reached" if final_round else review_reason}


def route_after_tester(state: CollaborationState, settings: L1Config) -> str:
    if _failed(state):
        return "finish"
    if _tests_passed(state.get("test_results")):
        return "code_reviewer"
    # Failing tests retry the Developer; the final round must still be reviewed.
    return "developer" if state.get("rounds", 0) < settings.max_rounds else "code_reviewer"


def route_after_reviewer(state: CollaborationState, settings: L1Config) -> str:
    if _failed(state):
        return "finish"
    tests_ok = _tests_passed(state.get("test_results"))
    approved = _review_passed(state.get("review"))[0] == "PASS"
    if tests_ok and approved:
        return "finish"  # early stop: the Code Reviewer accepted a passing patch
    return "developer" if state.get("rounds", 0) < settings.max_rounds else "finish"


class L1Fixed(BaseLevel):
    """Level 1 Fixed: six fixed roles with Tester/Code Reviewer feedback to the Developer."""

    name = "L1"
    topology = L1_FIXED_WORKFLOW
    roles = L1_ROLES

    def build_graph(self, task: Any, registry: Any, config: Mapping[str, Any]) -> LevelGraph:
        if task is None:
            raise ValueError("L1Fixed requires a task")
        if registry is None:
            raise ValueError("L1Fixed requires a registry")
        settings = validate_l1_config(config)
        for label in settings.roles:
            registry.get(L1_REGISTRY_ROLES[label])

        def log(state, event, **details):
            engine = config.get("engine")
            if engine is not None:
                engine._log({"event": event, "run_id": state.get("run_id"), "level": settings.level,
                             "round": state.get("rounds", 0), **details})

        async def invoke(label, state, **kwargs):
            return await _require_engine(config).invoke_agent(L1_REGISTRY_ROLES[label], state, **kwargs)

        async def requirement_analyst(state: CollaborationState) -> Mapping[str, Any]:
            if _failed(state):
                return {}
            return await invoke(
                "Requirement Analyst", state,
                incoming_message={"from": "caller", "requirement": state.get("requirement", "")},
                output_key="analysis",
            )

        async def architect(state: CollaborationState) -> Mapping[str, Any]:
            if _failed(state):
                return {}
            analysis = state.get("analysis")
            return await invoke(
                "Architect", state,
                incoming_message={"from": "Requirement Analyst", "content": analysis},
                artifacts={"spec": analysis},
                output_key="architecture",
            )

        async def planner(state: CollaborationState) -> Mapping[str, Any]:
            if _failed(state):
                return {}
            analysis, architecture = state.get("analysis"), state.get("architecture")
            return await invoke(
                "Planner", state,
                incoming_message={"from": "Architect", "content": architecture},
                artifacts={"spec": analysis, "architecture": architecture},
                output_key="plan",
            )

        async def developer(state: CollaborationState) -> Mapping[str, Any]:
            if _failed(state):
                return {}
            round_number = state.get("rounds", 0) + 1
            if round_number > settings.max_rounds:
                raise RuntimeError("L1 Developer round cap exceeded")
            feedback = state.get("feedback")
            artifacts: dict[str, Any] = {"spec": state.get("analysis"),
                                         "architecture": state.get("architecture"),
                                         "plan": state.get("plan")}
            if feedback:
                # Retry: the Developer receives the structured report that triggered the loop.
                artifacts["previous_patch"] = _patch(state)
                artifacts["test_report"] = compact_test_report(state.get("test_results"))
                if feedback.get("from") == "Code Reviewer":
                    artifacts["review_report"] = state.get("review")
                incoming = {"from": feedback["from"], "content": feedback.get("content")}
            else:
                incoming = {"from": "Planner", "content": state.get("plan")}
            update = dict(await invoke("Developer", {**state, "rounds": round_number},
                                       incoming_message=incoming, artifacts=artifacts,
                                       output_key="patch"))
            patch = update.get("patch") if isinstance(update.get("patch"), str) else ""
            return {**update, "rounds": round_number, "feedback": None,
                    "test_results": None, "review": None,
                    "iterations": [{"round": round_number, "stage": "developer",
                                    "trigger": (feedback or {}).get("from", "Planner"),
                                    **_patch_ref(patch)}]}

        async def tester(state: CollaborationState) -> Mapping[str, Any]:
            if _failed(state):
                return {}
            patch = _patch(state)
            update = dict(await invoke(
                "Tester", state,
                incoming_message={"from": "Developer", "patch": patch},
                artifacts={"spec": state.get("analysis"), "architecture": state.get("architecture"),
                           "plan": state.get("plan"), "patch": patch},
                output_key="test_results",
            ))
            report = update.get("test_results")
            passed = _tests_passed(report)
            update["iterations"] = [{"round": state.get("rounds", 0), "stage": "tester",
                                     "tests_passed": passed, "test_report": compact_test_report(report)}]
            next_state = {**state, **update}
            if route_after_tester(next_state, settings) == "developer":
                update["feedback"] = {"from": "Tester", "reason": "tests_failed", "content": report}
                log(state, "feedback_loop", source="Tester", target="Developer", reason="tests_failed")
            return update

        async def code_reviewer(state: CollaborationState) -> Mapping[str, Any]:
            if _failed(state):
                return {}
            patch = _patch(state)
            test_results = state.get("test_results")
            update = dict(await invoke(
                "Code Reviewer", state,
                incoming_message={"from": "Tester", "content": test_results},
                artifacts={"spec": state.get("analysis"), "architecture": state.get("architecture"),
                           "plan": state.get("plan"), "patch": patch,
                           "test_report": compact_test_report(test_results)},
                output_key="review",
            ))
            review = update.get("review")
            verdict, _ = _review_passed(review)
            update["iterations"] = [{"round": state.get("rounds", 0), "stage": "code_reviewer",
                                     "review_verdict": verdict, "review": review}]
            next_state = {**state, **update}
            if route_after_reviewer(next_state, settings) == "developer":
                update["feedback"] = {"from": "Code Reviewer", "reason": "review_changes_requested",
                                      "content": review}
                log(state, "feedback_loop", source="Code Reviewer", target="Developer",
                    reason="review_changes_requested")
            return update

        def finish(state: CollaborationState) -> Mapping[str, Any]:
            end = outcome(state, settings)
            log(state, "workflow_finished", status=end["status"], verdict=end["verdict"],
                stop_reason=end["stop_reason"])
            return {"status": "failed" if end["status"] == "aborted" else end["status"],
                    "result": {**_base_result(state, settings), **end}}

        def forward(target):
            return lambda state: "finish" if _failed(state) else target

        return LevelGraph(
            nodes={
                "requirement_analyst": requirement_analyst,
                "architect": architect,
                "planner": planner,
                "developer": developer,
                "tester": tester,
                "code_reviewer": code_reviewer,
                "finish": finish,
            },
            edges=(),
            entry_node=L1_FIXED_WORKFLOW.entry_node,
            exit_node=L1_FIXED_WORKFLOW.exit_node,
            conditional_edges={
                "requirement_analyst": (forward("architect"), {"architect": "architect", "finish": "finish"}),
                "architect": (forward("planner"), {"planner": "planner", "finish": "finish"}),
                "planner": (forward("developer"), {"developer": "developer", "finish": "finish"}),
                "developer": (forward("tester"), {"tester": "tester", "finish": "finish"}),
                "tester": (lambda state: route_after_tester(state, settings),
                           {"developer": "developer", "code_reviewer": "code_reviewer", "finish": "finish"}),
                "code_reviewer": (lambda state: route_after_reviewer(state, settings),
                                  {"developer": "developer", "finish": "finish"}),
            },
        )
