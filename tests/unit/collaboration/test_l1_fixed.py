"""Unit tests for the L1 fixed workflow (feedback loops, round cap, final review) and 3-task mock runs."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import pytest
import yaml
from langgraph.checkpoint.memory import InMemorySaver

from raac.benchmark.dataset import load_tasks
from raac.collaboration import Engine
from raac.collaboration.levels import (
    L1_FIXED_WORKFLOW,
    L1_REGISTRY_ROLES,
    L1_ROLES,
    L1Fixed,
    validate_l1_config,
)
from raac.collaboration.levels.l1_fixed import route_after_reviewer, route_after_tester
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
PREPARATION = ["Analyst", "Architect", "Planner"]


def _payload(role_id: str, *, test_pass: bool = True, approve: bool = True, failure_scope: str = "unknown"):
    if role_id == "analyst":
        return {"summary": "Analyzed task requirement",
                "requirements": [{"id": "R1", "description": "Fix target behavior", "priority": "high"}],
                "constraints": ["Preserve existing API"],
                "acceptance_criteria": ["Target behavior passes tests"],
                "open_questions": [], "interface_contracts": {}}
    if role_id == "architect":
        return {"summary": "Change stays inside app.py",
                "affected_components": [{"path": "app.py", "change": "update constant"}],
                "design_decisions": ["Keep the public name VALUE"], "interface_contracts": {},
                "constraints": [], "risks": []}
    if role_id == "planner":
        return {"steps": [{"id": 1, "description": "Update module implementation", "files": ["app.py"],
                           "depends_on": [], "ac_ids": ["AC1"]}],
                "covered_ac_ids": ["AC1"], "interface_contracts": {}}
    if role_id == "coder":
        return "--- a/app.py\n+++ b/app.py\n@@ -1,1 +1,1 @@\n-VALUE = 0\n+VALUE = 1\n"
    if role_id == "tester":
        status = "passed" if test_pass else "failed"
        return {"tests_to_run": ["tests/test_app.py::test_value"],
                "results": [{"test": "tests/test_app.py::test_value", "status": status,
                             "details": "" if test_pass else "AssertionError: 0 != 1"}],
                "verdict": "pass" if test_pass else "fail",
                "covered_ac_ids": ["AC1"], "blocking_issues": [],
                "failure_scope": failure_scope}
    if role_id == "reviewer":
        if approve:
            return {"decision": "approve", "summary": "Patch satisfies AC1.", "comments": [],
                    "covered_ac_ids": ["AC1"]}
        return {"decision": "reject", "summary": "Needs changes.",
                "comments": [{"file": "app.py", "line": 1, "severity": "blocker",
                              "message": "Handle the edge case required by AC1."}],
                "covered_ac_ids": ["AC1"]}
    raise AssertionError(f"Unexpected role {role_id!r}")


class ScriptedRunner:
    """Fake AgentRunner: per-round scripted Tester and Code Reviewer outcomes."""

    def __init__(self, *, tests=(True,), reviews=(True,), fail_role=None, fail_on_call=1):
        self.tests, self.reviews = list(tests), list(reviews)
        self.fail_role, self.fail_on_call = fail_role, fail_on_call
        self.calls: list[str] = []
        self.requests: list[tuple[str, dict]] = []
        self.tools = None
        self.provenance = {}

    def _pick(self, values, role):
        index = self.calls.count(role) - 1
        return values[min(index, len(values) - 1)]

    async def __call__(self, role, request):
        self.calls.append(role)
        self.requests.append((role, request))
        if role == self.fail_role and self.calls.count(role) == self.fail_on_call:
            raise RuntimeError(f"{role} boom")
        if role == "Tester":
            return _payload("tester", test_pass=self._pick(self.tests, role))
        if role == "Reviewer":
            return _payload("reviewer", approve=self._pick(self.reviews, role))
        return _payload(role.lower())


async def _run(runner, *, run_id="l1-run", config=None, **kwargs):
    engine = Engine(registry=load_registry(ROOT / "registry"), runner=runner, **kwargs)
    result = await engine.run(run_id=run_id, task={"task_id": "t1", "requirement": "fix bug"},
                              level=L1Fixed(), config=config or {})
    return engine, result


def _loop_cycle(*roles):
    return ["Coder", "Tester", "Reviewer"] if not roles else list(roles)


def test_l1_config_fixes_six_roles_and_caps_feedback_rounds_at_three():
    raw = yaml.safe_load((ROOT / "configs/levels/l1_fixed.yaml").read_text(encoding="utf-8-sig"))
    cfg = validate_l1_config(raw)
    assert cfg.version == "l1-fixed-v3" and cfg.level == "L1" and cfg.topology == "fixed-workflow"
    assert cfg.roles == L1_ROLES == ("Requirement Analyst", "Architect", "Planner",
                                     "Developer", "Tester", "Code Reviewer")
    assert cfg.max_rounds == 3 and cfg.hard_max_rounds == 3
    assert validate_l1_config({"max_rounds": 1, "hard_max_rounds": 1}).max_rounds == 1
    for invalid in ({"max_rounds": 4}, {"max_rounds": 3, "hard_max_rounds": 2},
                    {"roles": ["Requirement Analyst", "Developer", "Tester"]}, {"topology": "linear"}):
        with pytest.raises(ValueError):
            validate_l1_config(invalid)


def test_l1_topology_declares_only_fixed_feedback_edges_to_the_developer():
    graph = L1Fixed().build_graph(task={"task_id": "t"}, registry=load_registry(ROOT / "registry"), config={})
    assert tuple(graph.nodes) == ("requirement_analyst", "architect", "planner", "developer",
                                  "tester", "code_reviewer", "finish")
    assert graph.entry_node == "requirement_analyst" and graph.exit_node == "finish"
    loops = {(src, dst) for src, dst in L1_FIXED_WORKFLOW.edges if dst == "developer"}
    assert loops == {("planner", "developer"), ("tester", "developer"), ("code_reviewer", "developer")}
    for source, (_, targets) in graph.conditional_edges.items():
        declared = {dst for src, dst in L1_FIXED_WORKFLOW.edges if src == source} | {"finish"}
        assert set(targets.values()) <= declared, source
    assert [L1_REGISTRY_ROLES[label] for label in L1_ROLES] == [
        "Analyst", "Architect", "Planner", "Coder", "Tester", "Reviewer"]

def test_test_side_failure_never_routes_to_developer():
    state = {"test_results": {"verdict": "fail", "results": [{"status": "failed"}],
                              "failure_scope": "generated_test"}, "rounds": 1}
    settings = validate_l1_config({})
    assert route_after_tester(state, settings) == "code_reviewer"
    assert route_after_reviewer({**state, "review": {"decision": "reject"}}, settings) == "finish"


@pytest.mark.asyncio
async def test_pass_in_round_one_stops_early_after_review():
    runner = ScriptedRunner()
    engine, result = await _run(runner)
    assert runner.calls == [*PREPARATION, *_loop_cycle()]
    assert result.result["status"] == "completed" and result.result["verdict"] == "PASS"
    assert result.result["stop_reason"] == "tests_and_review_passed"
    assert result.result["rounds_completed"] == 1
    assert [item["stage"] for item in result.result["iterations"]] == ["developer", "tester", "code_reviewer"]
    assert not [e for e in engine.events if e.get("event") == "feedback_loop"]


@pytest.mark.asyncio
async def test_failed_tests_loop_back_to_developer_with_the_test_report():
    runner = ScriptedRunner(tests=(False, True))
    engine, result = await _run(runner)
    assert runner.calls == [*PREPARATION, "Coder", "Tester", "Coder", "Tester", "Reviewer"]
    assert result.result["verdict"] == "PASS" and result.result["rounds_completed"] == 2
    retry = [request for role, request in runner.requests if role == "Coder"][1]
    assert retry["incoming_message"]["from"] == "Tester"
    assert retry["artifacts"]["test_report"]["verdict"] == "fail"
    assert retry["artifacts"]["previous_patch"].startswith("--- a/app.py")
    assert retry["round"] == 2
    loops = [e for e in engine.events if e.get("event") == "feedback_loop"]
    assert [(e["source"], e["target"], e["reason"]) for e in loops] == [("Tester", "Developer", "tests_failed")]


@pytest.mark.asyncio
async def test_review_changes_loop_back_and_round_two_approval_ends_early():
    runner = ScriptedRunner(reviews=(False, True))
    engine, result = await _run(runner)
    assert runner.calls == [*PREPARATION, *_loop_cycle(), *_loop_cycle()]
    assert runner.calls.count("Coder") == 2  # approved in round 2: no third Developer call
    assert result.result["verdict"] == "PASS" and result.result["rounds_completed"] == 2
    retry = [request for role, request in runner.requests if role == "Coder"][1]
    assert retry["incoming_message"]["from"] == "Code Reviewer"
    assert retry["artifacts"]["review_report"]["decision"] == "reject"
    loops = [e for e in engine.events if e.get("event") == "feedback_loop"]
    assert [(e["source"], e["reason"]) for e in loops] == [("Code Reviewer", "review_changes_requested")]


@pytest.mark.asyncio
async def test_round_two_test_failure_reaches_a_third_developer_round():
    runner = ScriptedRunner(tests=(False, False, True))
    _, result = await _run(runner)
    assert runner.calls.count("Coder") == 3
    assert runner.calls[-3:] == ["Coder", "Tester", "Reviewer"]
    assert result.result["verdict"] == "PASS" and result.result["rounds_completed"] == 3


@pytest.mark.asyncio
async def test_final_round_with_failing_tests_is_reviewed_and_ends_without_pass():
    runner = ScriptedRunner(tests=(False,), reviews=(True,))
    engine, result = await _run(runner)
    # Rounds 1-2 loop from Tester; round 3 is forced through the Code Reviewer and then ends.
    assert runner.calls == [*PREPARATION, "Coder", "Tester", "Coder", "Tester", "Coder", "Tester", "Reviewer"]
    assert result.result["status"] == "completed_with_failures"
    assert result.result["verdict"] == "REJECT"
    assert result.result["stop_reason"] == "max_rounds_reached"
    assert result.result["rounds_completed"] == 3
    assert result.state["status"] == "completed_with_failures"
    finished = [e for e in engine.events if e.get("event") == "workflow_finished"]
    assert finished and finished[-1]["verdict"] == "REJECT"


@pytest.mark.asyncio
async def test_reviewer_rejection_in_final_round_ends_without_another_loop():
    runner = ScriptedRunner(reviews=(False,))
    _, result = await _run(runner)
    assert runner.calls == [*PREPARATION, *_loop_cycle(), *_loop_cycle(), *_loop_cycle()]
    assert runner.calls.count("Coder") == 3
    assert result.result["status"] == "rejected" and result.result["verdict"] == "REJECT"
    assert result.result["stop_reason"] == "max_rounds_reached"


@pytest.mark.asyncio
async def test_reviewer_approval_cannot_waive_failing_tests():
    runner = ScriptedRunner(tests=(False,), reviews=(True,))
    _, result = await _run(runner, config={"max_rounds": 1, "hard_max_rounds": 1})
    assert runner.calls == [*PREPARATION, "Coder", "Tester", "Reviewer"]
    assert result.result["status"] == "completed_with_failures" and result.result["verdict"] == "REJECT"


@pytest.mark.asyncio
async def test_agent_failure_inside_the_loop_aborts_and_keeps_iteration_history():
    runner = ScriptedRunner(tests=(False,), fail_role="Coder", fail_on_call=2)
    _, result = await _run(runner)
    assert runner.calls == [*PREPARATION, "Coder", "Tester", "Coder"]
    assert result.state["status"] == "failed"
    assert result.result["status"] == "aborted" and result.result["verdict"] is None
    assert result.result["stop_reason"] == "RuntimeError"
    assert [(item["round"], item["stage"]) for item in result.result["iterations"]] == [
        (1, "developer"), (1, "tester")]


@pytest.mark.asyncio
async def test_resume_in_round_two_does_not_rerun_preparation_roles():
    checkpointer = InMemorySaver()
    runner = ScriptedRunner(tests=(False, True))
    engine = Engine(registry=load_registry(ROOT / "registry"), runner=runner, checkpointer=checkpointer)
    task = {"task_id": "t1", "requirement": "fix bug"}
    first = await engine.run(run_id="l1-resume", task=task, level=L1Fixed(), config={},
                             interrupt_after=["tester"])
    assert first.state["rounds"] == 1 and runner.calls == [*PREPARATION, "Coder", "Tester"]
    resumed = await engine.run(run_id="l1-resume", task=task, level=L1Fixed(), config={}, resume=True)
    assert runner.calls[5:] == ["Coder", "Tester", "Reviewer"]
    assert all(runner.calls.count(role) == 1 for role in PREPARATION)
    assert resumed.result["verdict"] == "PASS" and resumed.result["rounds_completed"] == 2


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
        reviews = {"count": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content.decode("utf-8"))
            user_msg = json.loads(body["messages"][1]["content"])
            if user_msg.get("output_format") == "unified_diff":
                content = _payload("coder")
            else:
                props = (user_msg.get("output_schema") or {}).get("properties") or {}
                if "requirements" in props:
                    role_id = "analyst"
                elif "affected_components" in props:
                    role_id = "architect"
                elif "steps" in props:
                    role_id = "planner"
                elif "tests_to_run" in props:
                    role_id = "tester"
                elif "decision" in props:
                    role_id = "reviewer"
                else:
                    raise AssertionError(f"Unrecognized schema properties: {list(props)}")
                if role_id == "reviewer":
                    # Round 1 review requests changes; round 2 approves, exercising Reviewer -> Developer.
                    reviews["count"] += 1
                    content = json.dumps(_payload("reviewer", approve=reviews["count"] > 1))
                else:
                    content = json.dumps(_payload(role_id))
            return httpx.Response(200, json={
                "choices": [{"message": {"content": content}}],
                "usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30},
            })

        log_dir = tmp_path / "logs" / sample.id
        llm_log = log_dir / "llm.jsonl"
        engine_log = log_dir / "engine.jsonl"
        client = LLMClient(LLMConfig(model="mock-l1"), log_path=llm_log, transport=httpx.MockTransport(handler))
        workspace = WorkspaceTools(project, LocalExecutor(LocalExecutionPolicy()))
        # L1 has no Environment role; bind the suite the Tester adapter re-runs, as the benchmark does.
        workspace.test_spec = {"command": [sys.executable, "-m", "unittest", "discover", "-s", "tests"],
                               "cwd": ".", "env": {}, "report_path": None}
        engine = Engine.from_runtime(registry=registry, client=client, tools=ToolOrchestrator(workspace),
                                     provenance={"test": True, "task_id": sample.id}, log_path=engine_log)
        try:
            run_id = f"l1-sample-{idx}"
            result = await engine.run(run_id=run_id, task={"task_id": sample.id, "requirement": requirement_text},
                                      level=L1Fixed(), config=level_cfg)
            assert result.state["status"] == "completed", result.state.get("error")
            assert result.result["verdict"] == "PASS"
            assert result.result["stop_reason"] == "tests_and_review_passed"
            assert result.result["roles"] == list(L1_ROLES)
            assert result.result["rounds_completed"] == 2
            assert "+VALUE = 1" in result.state["patch"]
            started = [e["agent_id"] for e in engine.events if e.get("event") == "agent_call_started"]
            assert started == [*PREPARATION, "Coder", "Tester", "Reviewer", "Coder", "Tester", "Reviewer"]
            usage = client.usage.snapshot(run_id=run_id)
            assert usage["calls"] == 9 and usage["total_tokens"] == 270
            assert llm_log.is_file() and llm_log.stat().st_size > 0
            assert engine_log.is_file() and engine_log.stat().st_size > 0
            assert any(json.loads(line).get("event") == "feedback_loop"
                       for line in engine_log.read_text(encoding="utf-8").splitlines())
        finally:
            await client.aclose()

@pytest.mark.asyncio
@pytest.mark.parametrize('existing', [False, True])
async def test_workspace_handoff_from_tool_edits_reaches_tester_and_reviewer(tmp_path, existing):
    workspace_dir = tmp_path / 'workspace'
    workspace_dir.mkdir()
    if existing:
        (workspace_dir / 'app.py').write_text('VALUE = 0\n', encoding='utf-8')
    workspace = WorkspaceTools(workspace_dir, LocalExecutor(LocalExecutionPolicy()), requirement='Create app')
    assert bool(workspace.baseline_files) == existing
    edits = {
        'app.py': 'VALUE = 1',
        'tests/test_app.py': 'import unittest\nfrom app import VALUE\nclass AppTest(unittest.TestCase):\n def test_value(self):\n  self.assertEqual(VALUE, 1)\n',
        'empty.txt': '',
    }
    workspace.test_spec = {'command': [sys.executable, '-m', 'unittest', 'discover', '-s', 'tests'],
                           'cwd': '.', 'env': {}, 'report_path': None}
    sent = []
    pending = list(edits.items())
    outputs = {
        'analyst': {'product_goal': 'Create app', 'target_users': ['User'],
                    'user_flows': [{'id': 'UF1', 'title': 'Read value', 'actor': 'User', 'steps': ['Read VALUE']}],
                    'functional_requirements': [{'id': 'FR1', 'description': 'Expose VALUE = 1'}],
                    'acceptance_criteria': ['VALUE equals 1']},
        'architect': {'summary': 'One module', 'project_structure': list(edits),
                      'components': [{'id': 'COMP1', 'name': 'App', 'path': 'app.py', 'action': 'create',
                                      'responsibilities': 'Expose value', 'satisfies_requirements': ['FR1']}]},
        'planner': {**_payload('planner'), 'test_strategy': {'test_framework': 'unittest',
                    'test_command': 'python -m unittest discover -s tests'}},
        'tester': _payload('tester'),
        'reviewer': {**_payload('reviewer'), 'stop': True, 'completion_status': 'completed'},
    }

    def handler(request):
        body = json.loads(request.content)
        handoff = json.loads(body['messages'][1]['content'])
        props = handoff.get('output_schema', {}).get('properties', {})
        role = next((role for key, role in [('product_goal', 'analyst'), ('components', 'architect'),
                    ('steps', 'planner'), ('tests_to_run', 'tester'), ('decision', 'reviewer')]
                    if key in props), 'coder')
        sent.append(role)
        if role == 'coder' and pending:
            path, content = pending.pop(0)
            message = {'tool_calls': [{'id': f'edit-{len(pending)}', 'type': 'function', 'function': {
                'name': 'edit_file', 'arguments': json.dumps({'path': path, 'content': content})}}]}
        else:
            output = {'summary': 'Workspace saved', 'created_files': list(edits)} if role == 'coder' else outputs[role]
            message = {'content': json.dumps(output)}
        return httpx.Response(200, json={'choices': [{'message': message}],
                                       'usage': {'prompt_tokens': 10, 'completion_tokens': 5}})

    client = LLMClient(LLMConfig(model='fixture'), log_path=tmp_path / 'logs/llm.jsonl',
                       transport=httpx.MockTransport(handler))
    engine = Engine.from_runtime(registry=load_registry(ROOT / 'registry'), client=client,
                                 tools=ToolOrchestrator(workspace), provenance={})
    try:
        result = await engine.run(run_id='greenfield', task={'task_id': 'new-app', 'requirement': 'Create app'},
                                  level=L1Fixed(), config={})
        assert result.result['verdict'] == 'PASS', result.state.get('error')
        assert sent[-2:] == ['tester', 'reviewer']
        assert '/dev/null' in result.state['patch'] and 'empty.txt' in result.state['patch']
        assert result.state['test_results']['verdict'] == 'pass'
        assert len(result.state['test_results']['results']) == 1
        assert result.state['test_results']['results'][0]['status'] == 'passed'
        for path, content in edits.items():
            assert (workspace_dir / path).read_text(encoding='utf-8') == content
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_handoff_sends_each_artifact_once_and_summarises_earlier_rounds():
    runner = ScriptedRunner(tests=(False, False, True))
    await _run(runner)
    requests = {}
    for role, request in runner.requests:
        requests.setdefault(role, []).append(request)
    for role, request in runner.requests:
        assert "collaboration_state" not in request["artifacts"], role
    # A payload already carried as an artifact is referenced, never repeated.
    assert requests["Architect"][0]["incoming_message"] == {"from": "Requirement Analyst", "content_ref": "artifacts.spec"}
    assert requests["Coder"][0]["incoming_message"] == {"from": "Planner", "content_ref": "artifacts.plan"}
    assert requests["Tester"][0]["incoming_message"] == {"from": "Developer", "patch_ref": "artifacts.patch"}
    # Same role visibility as L3: only the Analyst, Architect and Reviewer read the raw request.
    assert "requirement" not in requests["Coder"][0] and "requirement" not in requests["Tester"][0]
    assert requests["Reviewer"][0]["requirement"] == "fix bug"
    assert set(requests["Tester"][0]["artifacts"]["plan"]) == {"interface_contracts"}
    assert "earlier_rounds" not in requests["Coder"][1]["artifacts"]
    earlier = requests["Coder"][2]["artifacts"]["earlier_rounds"]
    assert [(entry["round"], entry["stage"], entry["tests_passed"]) for entry in earlier] == [(1, "tester", False)]
