"""Unit tests for L1Fixed collaboration level and 3-task mock runs."""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import yaml

from raac.benchmark.dataset import load_tasks
from raac.collaboration import Engine
from raac.collaboration.levels import L1_LINEAR, L1_ROLES, L1Fixed, validate_l1_config
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


def _role_response_payload(role_id: str) -> str:
    if role_id == "analyst":
        return json.dumps(
            {
                "summary": "Analyzed task requirement",
                "requirements": [
                    {"id": "R1", "description": "Fix target behavior", "priority": "high"}
                ],
                "constraints": ["Preserve existing API"],
                "acceptance_criteria": ["Target behavior passes tests"],
                "open_questions": [],
                "interface_contracts": {},
            }
        )
    if role_id == "planner":
        return json.dumps(
            {
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
        )
    if role_id == "coder":
        return (
            "--- a/app.py\n"
            "+++ b/app.py\n"
            "@@ -1,1 +1,1 @@\n"
            "-VALUE = 0\n"
            "+VALUE = 1\n"
        )
    if role_id == "tester":
        return json.dumps(
            {
                "tests_to_run": ["tests/test_app.py::test_value"],
                "results": [
                    {"test": "tests/test_app.py::test_value", "status": "passed", "details": ""}
                ],
                "verdict": "pass",
                "covered_ac_ids": ["AC1"],
                "blocking_issues": [],
            }
        )
    if role_id == "reviewer":
        return json.dumps(
            {
                "decision": "approve",
                "summary": "Patch satisfies AC1 and tests pass.",
                "comments": [],
                "covered_ac_ids": ["AC1"],
            }
        )
    raise AssertionError(f"Unexpected role {role_id!r}")


def test_l1_config_enforces_fixed_five_roles_and_one_round():
    raw = yaml.safe_load((ROOT / "configs/levels/l1_fixed.yaml").read_text(encoding="utf-8-sig"))
    cfg = validate_l1_config(raw)
    assert cfg.version == "l1-fixed-v1"
    assert cfg.level == "L1"
    assert cfg.topology == "linear"
    assert cfg.roles == L1_ROLES
    assert cfg.max_rounds == 1
    assert cfg.hard_max_rounds == 1

    with pytest.raises(ValueError, match="L1 roles are fixed"):
        validate_l1_config({"roles": ["Analyst", "Coder", "Tester"]})
    with pytest.raises(ValueError):
        validate_l1_config({"max_rounds": 2})


def test_l1_fixed_builds_linear_five_node_graph():
    registry = load_registry(ROOT / "registry")
    level = L1Fixed()
    graph = level.build_graph(
        task={"task_id": "t1", "requirement": "fix bug"},
        registry=registry,
        config={"version": "l1-fixed-v1"},
    )
    assert tuple(graph.nodes) == ("analyst", "planner", "coder", "tester", "reviewer")
    assert graph.edges == L1_LINEAR.edges
    assert graph.entry_node == "analyst"
    assert graph.exit_node == "reviewer"
    assert not graph.conditional_edges


@pytest.mark.asyncio
async def test_l1_fixed_stops_downstream_roles_when_earlier_role_fails():
    registry = load_registry(ROOT / "registry")
    called_roles: list[str] = []

    class FailingCoderRunner:
        def __init__(self):
            self.tools = None
            self.provenance = {}

        async def __call__(self, role, request):
            called_roles.append(role)
            if role == "Coder":
                raise RuntimeError("coder boom")
            return json.loads(_role_response_payload(role.lower()))

    engine = Engine(registry=registry, runner=FailingCoderRunner())
    result = await engine.run(
        run_id="l1-fail-run",
        task={"task_id": "t-fail", "requirement": "fix bug"},
        level=L1Fixed(),
        config={"version": "l1-fixed-v1"},
    )
    assert called_roles == ["Analyst", "Planner", "Coder"]
    assert result.state["status"] == "failed"
    assert result.state["result"]["status"] == "aborted"
    assert result.state["result"]["stop_reason"] == "RuntimeError"


@pytest.mark.asyncio
async def test_l1_fixed_runs_three_sample_tasks_through_llm_client_and_saves_logs(tmp_path):
    registry = load_registry(ROOT / "registry")
    all_tasks = list(load_tasks(ROOT / "data/tasks/cleaned_benchmark.json").values())[:3]
    assert len(all_tasks) == 3
    level_cfg = yaml.safe_load((ROOT / "configs/levels/l1_fixed.yaml").read_text(encoding="utf-8-sig"))

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
                    role_id = "coder"
                else:
                    props = (user_msg.get("output_schema") or {}).get("properties") or {}
                    if "requirements" in props:
                        role_id = "analyst"
                    elif "steps" in props:
                        role_id = "planner"
                    elif "tests_to_run" in props:
                        role_id = "tester"
                    elif "decision" in props:
                        role_id = "reviewer"
                    else:
                        raise AssertionError(f"Unrecognized schema properties: {list(props)}")
                return httpx.Response(
                    200,
                    json={
                        "choices": [{"message": {"content": _role_response_payload(role_id)}}],
                        "usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30},
                    },
                )

            return httpx.MockTransport(handler)

        log_dir = tmp_path / "logs" / sample.id
        llm_log = log_dir / "llm.jsonl"
        engine_log = log_dir / "engine.jsonl"
        client = LLMClient(
            LLMConfig(model="mock-l1"),
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
            run_id = f"l1-sample-{idx}"
            result = await engine.run(
                run_id=run_id,
                task={"task_id": sample.id, "requirement": requirement_text},
                level=L1Fixed(),
                config=level_cfg,
            )
            assert result.state["status"] == "completed"
            assert result.result["verdict"] == "PASS"
            assert result.result["stop_reason"] == "tests_and_review_passed"
            assert result.result["roles"] == list(L1_ROLES)
            assert "+VALUE = 1" in result.state["patch"]
            started = [
                e["agent_id"]
                for e in engine.events
                if e.get("event") == "agent_call_started"
            ]
            assert started == list(L1_ROLES)
            usage = client.usage.snapshot(run_id=run_id)
            assert usage["calls"] == 5
            assert usage["total_tokens"] == 150
            assert llm_log.is_file() and llm_log.stat().st_size > 0
            assert engine_log.is_file() and engine_log.stat().st_size > 0
        finally:
            await client.aclose()

