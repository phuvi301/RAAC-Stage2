"""Executable Chain nodes using an injected shared-Engine agent runner.

The runner must resolve roles via Registry, call the shared LLM client, and
execute tools/tests through the configured backend. These nodes do none of those
operations directly, and never treat a model's unstructured text as a report.
"""

import asyncio
import hashlib
import time
from copy import deepcopy
from typing import Any, Callable, Protocol

from raac.collaboration.topologies import CHAIN
from raac.common.errors import AgentExecutionError, RunGuardError

from .chain_state import (
    ChainState, CodeOutput, ReviewReport, RoleHandoff, execution_report, parse_role_output, role_output_model,
)


class AgentRunner(Protocol):
    async def __call__(self, role: str, request: dict[str, Any]) -> dict[str, Any] | str | RoleHandoff:
        """Return structured output; enforce tool timeouts inside the adapter."""
        ...


def _ac_ids(state: ChainState) -> set[str]:
    return set(state["ac_catalog"])


def _tests_pass(state: ChainState) -> bool:
    report = state["test_report"]
    return bool(
        report and state["patch_hash"]
        and report["patch_hash"] == state["patch_hash"]
        and report["status"] == "passed" and report["verdict"] == "pass"
        and report["tests_run"] > 0 and report["failed"] == 0
        and report["test_cases"] and not report["blocking_issues"]
        and set(report["covered_ac_ids"]) == _ac_ids(state)
    )


class ChainNodes:
    """Bound node functions; no mutable state is shared between invocations.

    ``budget_guard`` returns True while the run-wide budget permits a call.
    Its implementation belongs to the shared Engine/cost controller; it must
    account for all work scopes rather than reset for each subtask.
    """

    def __init__(
        self, runner: AgentRunner,
        *, budget_guard: Callable[[ChainState], bool] | None = None,
    ):
        self.runner = runner
        self.budget_guard = budget_guard

    def _event(self, state, node, event, **details):
        work = state["work"]
        return {
            "run_id": work["run_id"], "task_id": work["task_id"],
            "work_id": work["work_id"], "scope": work["scope"],
            "level": "L3", "topology": CHAIN.topology_id, "topology_version": CHAIN.version,
            "node": node, "round": state["round_number"],
            "event": event, **details,
        }

    def _abort(self, state, node, reason, *, error_kind=None, events=()):
        return {
            "next_node": "result", "stop_reason": reason, "verdict": None,
            "error": {"node": node, "kind": error_kind or reason},
            "events": [*events, self._event(state, node, "chain_aborted", reason=reason)],
        }

    def _guard(self, state, node):
        if time.time() >= state["deadline_at"]:
            return self._abort(state, node, "timeout")
        if self.budget_guard is not None:
            try:
                allowed = self.budget_guard(deepcopy(state))
            except Exception as exc:
                return self._abort(state, node, "guard_error", error_kind=type(exc).__name__)
            if allowed is not True:
                return self._abort(state, node, "budget_exceeded")
        return None

    async def _call(self, state, node, role, incoming, artifacts):
        interrupted = self._guard(state, node)
        if interrupted:
            return None, interrupted, []
        round_number = state["round_number"] + (node == "coder")
        request = deepcopy({
            **state["work"], "topology": CHAIN.topology_id, "topology_version": CHAIN.version,
            "round": round_number, "max_rounds": state["limits"]["max_rounds"],
            "incoming_message": incoming, "artifacts": {"ac_catalog": state["ac_catalog"], **artifacts},
            "output_format": "unified_diff" if role == "Coder" else "json",
            "output_schema": role_output_model(role).model_json_schema(),
        })
        events = [self._event(state, node, "agent_started", role=role, round=round_number)]
        try:
            timeout = min(
                state["limits"]["agent_timeout_seconds"],
                max(0, state["deadline_at"] - time.time()),
            )
            raw = await asyncio.wait_for(self.runner(role, request), timeout=timeout)
            runtime = deepcopy(raw.runtime) if isinstance(raw, RoleHandoff) else {}
            payload = raw.output if isinstance(raw, RoleHandoff) else raw
            output = RoleHandoff(parse_role_output(role, payload).model_dump(), runtime)
        except RunGuardError as exc:
            return None, self._abort(state, node, exc.reason, events=events), []
        except AgentExecutionError as exc:
            return None, self._abort(state, node, exc.code, events=events), []
        except TimeoutError:
            return None, self._abort(state, node, "timeout", events=events), []
        except (ValueError, TypeError):
            return None, self._abort(state, node, "invalid_output", events=events), []
        except Exception as exc:
            # Do not copy exception messages/raw model output into logs: an
            # adapter might include credentials or sensitive task content.
            return None, self._abort(
                state, node, "agent_error", error_kind=type(exc).__name__, events=events,
            ), []
        interrupted = self._guard(state, node)
        if interrupted:
            interrupted["events"] = events + interrupted["events"]
            return None, interrupted, []
        events.append(self._event(state, node, "agent_completed", role=role, round=round_number))
        return output, None, events

    async def analyst(self, state: ChainState) -> dict:
        output, failure, events = await self._call(
            state, "analyst", "Analyst",
            {"from": "caller", "requirement": state["work"]["requirement"]}, {},
        )
        if failure:
            return failure
        output = output.output
        inherited = state["work"]["acceptance_criteria"]
        criteria = set(output["acceptance_criteria"])
        if any(item["description"] not in criteria for item in inherited):
            return self._abort(state, "analyst", "changed_acceptance_criteria", events=events)
        if not set(state["work"]["constraints"]).issubset(output["constraints"]):
            return self._abort(state, "analyst", "changed_constraints", events=events)
        catalog = {item["id"]: item["description"] for item in inherited}
        index = 1
        for description in output["acceptance_criteria"]:
            if description not in catalog.values():
                while f"AC{index}" in catalog:
                    index += 1
                catalog[f"AC{index}"] = description
                index += 1
        return {"spec": output, "ac_catalog": catalog, "next_node": "planner", "events": events}

    async def planner(self, state: ChainState) -> dict:
        output, failure, events = await self._call(
            state, "planner", "Planner",
            {"from": "Analyst", "content": state["spec"]}, {"spec": state["spec"]},
        )
        if failure:
            return failure
        output = output.output
        step_criteria = {criterion for step in output["steps"] for criterion in step["ac_ids"]}
        if (set(output["covered_ac_ids"]) != _ac_ids(state)
                or step_criteria != _ac_ids(state)):
            return self._abort(state, "planner", "incomplete_plan", events=events)
        return {"plan": output, "next_node": "coder", "events": events}

    async def coder(self, state: ChainState) -> dict:
        if state["round_number"] >= state["limits"]["max_rounds"]:
            return self._abort(state, "coder", "max_rounds_reached")
        feedback = state["test_report"]
        output, failure, events = await self._call(
            state, "coder", "Coder",
            {"from": "Tester" if feedback else "Planner",
             "content": feedback or state["plan"]},
            {"spec": state["spec"], "plan": state["plan"],
             "previous_patch": state["patch"], "test_report": feedback},
        )
        if failure:
            return failure
        try:
            output = CodeOutput(patch=output.output,
                                summary=output.runtime.get("summary", "Coder supplied a cumulative patch"),
                                execution_evidence=output.runtime.get("execution_evidence", [])).model_dump()
        except (ValueError, TypeError):
            return self._abort(state, "coder", "invalid_output", events=events)
        patch_hash = hashlib.sha256(
            (state["work"]["baseline_ref"] + "\0" + output["patch"]).encode("utf-8")
        ).hexdigest()
        return {
            "patch": output["patch"], "patch_hash": patch_hash,
            "coder_evidence": output["execution_evidence"],
            "coder_summary": output["summary"],
            "round_number": state["round_number"] + 1,
            "test_report": None, "review_report": None, "tests_passed": False,
            "verdict": None, "stop_reason": None, "next_node": "tester", "events": events,
        }

    async def tester(self, state: ChainState) -> dict:
        output, failure, events = await self._call(
            state, "tester", "Tester",
            {"from": "Coder", "patch_hash": state["patch_hash"], "patch": state["patch"]},
            {key: state[key] for key in ("spec", "plan", "patch", "patch_hash", "coder_evidence", "coder_summary")},
        )
        if failure:
            return failure
        try:
            output = execution_report(output.output, output.runtime, state["patch_hash"]).model_dump()
        except ValueError:
            return self._abort(state, "tester", "invalid_output", events=events)
        if output["patch_hash"] != state["patch_hash"]:
            return self._abort(state, "tester", "stale_test_report", events=events)
        if output["status"] == "error":
            return {
                **self._abort(state, "tester", "test_execution_error", events=events),
                "test_report": output, "rounds_completed": state["round_number"],
            }
        return {
            "test_report": output, "rounds_completed": state["round_number"],
            "next_node": "check", "events": events,
        }

    def check(self, state: ChainState) -> dict:
        passed = _tests_pass(state)
        return {
            "tests_passed": passed, "next_node": "reviewer" if passed else "limit",
            "events": [self._event(state, "check", "tests_evaluated", passed=passed)],
        }

    def limit(self, state: ChainState) -> dict:
        repeat = state["rounds_completed"] < state["limits"]["max_rounds"]
        return {
            "next_node": "coder" if repeat else "reviewer",
            "stop_reason": None if repeat else "max_rounds_reached",
            "events": [self._event(
                state, "limit", "round_limit_evaluated", repeat=repeat,
                reason="retry" if repeat else "max_rounds_reached",
            )],
        }

    async def reviewer(self, state: ChainState) -> dict:
        output, failure, events = await self._call(
            state, "reviewer", "Reviewer",
            {"from": "Tester", "content": state["test_report"]},
            {key: state[key] for key in ("spec", "plan", "patch", "patch_hash", "test_report", "coder_evidence", "coder_summary")},
        )
        if failure:
            return failure
        payload = output.output
        try:
            output = ReviewReport.model_validate({**payload,
                "patch_hash": output.runtime.get("patch_hash", state["patch_hash"]),
                "verdict": "PASS" if payload["decision"] == "approve" else "REJECT",
                "blocking_issues": [item["message"] for item in payload["comments"] if item["severity"] == "blocker"],
            }).model_dump()
        except (ValueError, TypeError):
            return self._abort(state, "reviewer", "invalid_output", events=events)
        if output["patch_hash"] != state["patch_hash"]:
            return self._abort(state, "reviewer", "stale_review_report", events=events)
        passed = bool(
            _tests_pass(state) and output["verdict"] == "PASS"
            and not output["blocking_issues"]
            and set(output["covered_ac_ids"]) == _ac_ids(state)
        )
        verdict = "PASS" if passed else "REJECT"
        reason = "tests_and_review_passed" if passed else (
            state["stop_reason"] or "reviewer_rejected"
        )
        return {
            "review_report": output, "verdict": verdict, "stop_reason": reason,
            "next_node": "result", "events": [*events, self._event(
                state, "reviewer", "review_evaluated", verdict=verdict,
                reported_verdict=output["verdict"], reason=reason,
            )],
        }

    def result(self, state: ChainState) -> dict:
        result = {
            **{key: state["work"][key] for key in ("run_id", "task_id", "work_id", "scope")},
            "topology": CHAIN.topology_id, "topology_version": CHAIN.version,
            "status": "aborted" if state["error"] else (
                "completed" if state["verdict"] == "PASS" else "rejected"
            ),
            **{key: deepcopy(state[key]) for key in (
                "spec", "ac_catalog", "plan", "patch", "patch_hash", "coder_evidence", "coder_summary", "test_report", "review_report",
                "verdict", "stop_reason", "error", "round_number", "rounds_completed",
            )},
        }
        return {
            "result": result, "next_node": "END",
            "events": [self._event(state, "result", "chain_stopped", reason=state["stop_reason"])],
        }
