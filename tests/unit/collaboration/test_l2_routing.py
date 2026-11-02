"""Unit tests for L2 Adaptive Routing collaboration level and 3-task mock runs."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml

from raac.benchmark.dataset import load_tasks
from raac.collaboration import Engine
from raac.collaboration.levels import (
    L1_ROLES,
    L2_OPTIONAL_ROLES,
    L2_REQUIRED_ROLES,
    L2Routing,
    parse_router_decision,
    validate_l2_config,
)
from raac.common.agent_contracts import OutputFormatError
from raac.harness.layer3_execution.local_executor import (
    LocalExecutionPolicy,
    LocalExecutor,
)
from raac.harness.layer3_execution.tool_orchestrator import ToolOrchestrator
from raac.harness.layer3_execution.tools.workspace import WorkspaceTools
from raac.llm.client import LLMClient
from raac.llm.config import LLMConfig
from raac.registry import load_registry

ROOT = Path(__file__).resolve().parents[3]


def _role_payload(role_id: str) -> Any:
    if role_id == "analyst":
        return {
            "summary": "Analyzed task requirement",
            "requirements": [
                {"id": "R1", "description": "Fix target behavior", "priority": "high"}
            ],
            "constraints": ["Preserve existing API"],
            "acceptance_criteria": ["Target behavior passes tests"],
            "open_questions": [],
            "interface_contracts": {},
        }
    if role_id == "planner":
        return {
            "steps": [
                {
                    "id": 1,
                    "description": "Update module implementation",
                    "files": ["app.py"],
                    "depends_on": [],
                    "ac_ids": ["AC1"],
                }
            ],
            "covered_ac_ids": ["AC1"],
            "interface_contracts": {},
        }
    if role_id == "coder":
        return (
            "--- a/app.py\n"
            "+++ b/app.py\n"
            "@@ -1,1 +1,1 @@\n"
            "-VALUE = 0\n"
            "+VALUE = 1\n"
        )
    if role_id == "tester":
        return {
            "tests_to_run": ["tests/test_app.py::test_value"],
            "results": [
                {"test": "tests/test_app.py::test_value", "status": "passed", "details": ""}
            ],
            "verdict": "pass",
            "covered_ac_ids": ["AC1"],
            "blocking_issues": [],
        }
    if role_id == "reviewer":
        return {
            "decision": "approve",
            "summary": "Patch satisfies AC1 and tests pass.",
            "comments": [],
            "covered_ac_ids": ["AC1"],
        }
    raise AssertionError(f"Unexpected role {role_id!r}")


def test_l2_config_enforces_required_coder_and_tester_and_fixed_catalog():
    raw = yaml.safe_load((ROOT / "configs/levels/l2_routing.yaml").read_text(encoding="utf-8-sig"))
    cfg = validate_l2_config(raw)
    assert cfg.version == "l2-routing-v1"
    assert cfg.level == "L2"
    assert cfg.topology == "linear-routed"
    assert cfg.roles == L1_ROLES
    assert cfg.required_roles == L2_REQUIRED_ROLES
    assert cfg.optional_roles == L2_OPTIONAL_ROLES
    assert cfg.fallback == "L1"
    assert cfg.max_rounds == 1

    with pytest.raises(ValueError, match="Coder and Tester"):
        validate_l2_config({"required_roles": ["Coder"], "optional_roles": ["Analyst", "Planner", "Tester", "Reviewer"]})
    with pytest.raises(ValueError, match="L2 catalog is fixed"):
        validate_l2_config({"roles": ["Coder", "Tester"]})
    with pytest.raises(ValueError):
        validate_l2_config({"max_rounds": 2})


def test_router_decision_validation_rejects_missing_mandatory_or_unexplained_skips():
    cfg = validate_l2_config({})
    valid = parse_router_decision(
        {
            "selected_roles": ["Analyst", "Coder", "Tester"],
            "skipped": [
                {"role": "Planner", "reason": "Single-file fix needs no multi-step plan"},
                {"role": "Reviewer", "reason": "Direct unit assertion covers behavior"},
            ],
            "reason": "Keep Analyst, Coder and Tester only",
        },
        cfg,
    )
    assert valid.selected_roles == ["Analyst", "Coder", "Tester"]

    for invalid in (
        {"selected_roles": ["Analyst", "Planner"], "skipped": [], "reason": "missing coder/tester"},
        {"selected_roles": ["Tester", "Coder"], "skipped": [
            {"role": "Analyst", "reason": "skip"},
            {"role": "Planner", "reason": "skip"},
            {"role": "Reviewer", "reason": "skip"},
        ], "reason": "wrong order"},
        {"selected_roles": ["Coder", "Tester"], "skipped": [{"role": "Analyst", "reason": "only one"}], "reason": "missing skip entries"},
        "not valid json {",
    ):
        with pytest.raises(OutputFormatError):
            parse_router_decision(invalid, cfg)


@pytest.mark.asyncio
async def test_l2_routing_skips_unselected_roles_and_logs_skip_reasons():
    registry = load_registry(ROOT / "registry")
    called_roles: list[str] = []

    class SelectiveRunner:
        def __init__(self):
            self.tools = None
            self.provenance = {}

        async def __call__(self, role, request):
            called_roles.append(role)
            if role == "Router":
                return {
                    "selected_roles": ["Coder", "Tester"],
                    "skipped": [
                        {"role": "Analyst", "reason": "Requirement is already a one-line bug report"},
                        {"role": "Planner", "reason": "Trivial one-line constant change"},
                        {"role": "Reviewer", "reason": "Unit test directly verifies constant"},
                    ],
                    "reason": "Minimal Coder+Tester path for trivial fix",
                }
            return _role_payload(role.lower())

    engine = Engine(registry=registry, runner=SelectiveRunner())
    result = await engine.run(
        run_id="l2-minimal-run",
        task={"task_id": "t-min", "requirement": "Set VALUE = 1"},
        level=L2Routing(),
        config={"version": "l2-routing-v1"},
    )
    assert called_roles == ["Router", "Coder", "Tester"]
    assert result.state["status"] == "completed"
    assert result.result["verdict"] == "PASS"
    assert result.result["stop_reason"] == "tests_passed_no_review"
    assert result.result["roles"] == ["Coder", "Tester"]
    assert result.state["routing"]["source"] == "router"
    skipped_events = [e for e in engine.events if e.get("event") == "agent_skipped"]
    assert [(e["agent_id"], e["reason"]) for e in skipped_events] == [
        ("Analyst", "Requirement is already a one-line bug report"),
        ("Planner", "Trivial one-line constant change"),
        ("Reviewer", "Unit test directly verifies constant"),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_router_response",
    [
        "invalid json {{{",
        {"selected_roles": ["Analyst", "Planner"], "skipped": [], "reason": "dropped Coder and Tester"},
        RuntimeError("router LLM error"),
    ],
)
async def test_l2_routing_falls_back_to_full_l1_when_router_output_is_invalid(bad_router_response):
    registry = load_registry(ROOT / "registry")
    called_roles: list[str] = []

    class FallbackRunner:
        def __init__(self):
            self.tools = None
            self.provenance = {}

        async def __call__(self, role, request):
            called_roles.append(role)
            if role == "Router":
                if isinstance(bad_router_response, Exception):
                    raise bad_router_response
                return bad_router_response
            return _role_payload(role.lower())

    engine = Engine(registry=registry, runner=FallbackRunner())
    result = await engine.run(
        run_id="l2-fallback-run",
        task={"task_id": "t-fb", "requirement": "Fix bug"},
        level=L2Routing(),
        config={"version": "l2-routing-v1"},
    )
    assert called_roles == ["Router", *L1_ROLES]
    assert result.state["status"] == "completed"
    assert result.result["verdict"] == "PASS"
    assert result.state["routing"]["source"] == "fallback_l1"
    assert result.state["routing"]["selected_roles"] == list(L1_ROLES)
    assert any(e.get("event") == "routing_fallback" for e in engine.events)


@pytest.mark.asyncio
async def test_l2_routing_runs_three_sample_tasks_through_llm_client_and_saves_logs(tmp_path):
    registry = load_registry(ROOT / "registry")
    all_tasks = list(load_tasks(ROOT / "data/tasks/cleaned_benchmark.json").values())[:3]
    assert len(all_tasks) == 3
    level_cfg = yaml.safe_load((ROOT / "configs/levels/l2_routing.yaml").read_text(encoding="utf-8-sig"))

    for idx, sample in enumerate(all_tasks, 1):
        project = tmp_path / f"task_{idx}"
        (project / "repo" / "tests").mkdir(parents=True)
        (project / "requirement").mkdir()
        (project / "repo" / "app.py").write_text("VALUE = 0\n", encoding="utf-8")
        (project / "repo" / "tests" / "test_app.py").write_text(
            "import sys\nfrom pathlib import Path\n"
            "sys.path.insert(0, str(Path(__file__).resolve().parents[1]))\n"
            "import unittest\nimport app\n\n"
            "class AppTest(unittest.TestCase):\n"
            "    def test_value(self):\n"
            "        self.assertEqual(app.VALUE, 1)\n",
            encoding="utf-8",
        )
        requirement_text = f"{sample.title}\n\n{sample.body}"
        (project / "requirement" / "user_chat.txt").write_text(requirement_text, encoding="utf-8")

        def make_transport():
            def handler(request: httpx.Request) -> httpx.Response:
                body = json.loads(request.content.decode("utf-8"))
                user_msg = json.loads(body["messages"][1]["content"])
                if user_msg.get("output_format") == "unified_diff":
                    content = _role_payload("coder")
                else:
                    props = (user_msg.get("output_schema") or {}).get("properties") or {}
                    if "selected_roles" in props:
                        content = json.dumps(
                            {
                                "selected_roles": ["Analyst", "Coder", "Tester", "Reviewer"],
                                "skipped": [
                                    {
                                        "role": "Planner",
                                        "reason": "Direct single-file fix after Analyst specification",
                                    }
                                ],
                                "reason": "Skip Planner for localized defect",
                            }
                        )
                    elif "requirements" in props:
                        content = json.dumps(_role_payload("analyst"))
                    elif "tests_to_run" in props:
                        content = json.dumps(_role_payload("tester"))
                    elif "decision" in props:
                        content = json.dumps(_role_payload("reviewer"))
                    else:
                        raise AssertionError(f"Unexpected schema properties: {list(props)}")
                return httpx.Response(
                    200,
                    json={
                        "choices": [{"message": {"content": content}}],
                        "usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30},
                    },
                )

            return httpx.MockTransport(handler)

        log_dir = tmp_path / "logs" / sample.id
        llm_log = log_dir / "llm.jsonl"
        engine_log = log_dir / "engine.jsonl"
        client = LLMClient(
            LLMConfig(model="mock-l2"),
            log_path=llm_log,
            transport=make_transport(),
        )
        workspace = WorkspaceTools(project, LocalExecutor(LocalExecutionPolicy()))
        engine = Engine.from_runtime(
            registry=registry,
            client=client,
            tools=ToolOrchestrator(workspace),
            provenance={"test": True, "task_id": sample.id},
            log_path=engine_log,
        )
        try:
            run_id = f"l2-sample-{idx}"
            result = await engine.run(
                run_id=run_id,
                task={"task_id": sample.id, "requirement": requirement_text},
                level=L2Routing(),
                config=level_cfg,
            )
            assert result.state["status"] == "completed"
            assert result.result["verdict"] == "PASS"
            assert result.result["roles"] == ["Analyst", "Coder", "Tester", "Reviewer"]
            assert "+VALUE = 1" in result.state["patch"]
            started = [
                e["agent_id"]
                for e in engine.events
                if e.get("event") == "agent_call_started"
            ]
            assert started == ["Router", "Analyst", "Coder", "Tester", "Reviewer"]
            skipped = [e for e in engine.events if e.get("event") == "agent_skipped"]
            assert len(skipped) == 1 and skipped[0]["agent_id"] == "Planner"
            usage = client.usage.snapshot(run_id=run_id)
            assert usage["calls"] == 5
            assert usage["total_tokens"] == 150
            assert llm_log.is_file() and llm_log.stat().st_size > 0
            assert engine_log.is_file() and engine_log.stat().st_size > 0
        finally:
            await client.aclose()
