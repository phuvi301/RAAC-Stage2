import pytest
import httpx
from langgraph.checkpoint.memory import InMemorySaver

from raac.collaboration import Engine
from raac.collaboration.levels.base import BaseLevel, LevelGraph
from raac.registry import load_registry
from raac.harness.layer3_execution.local_executor import LocalExecutionPolicy, LocalExecutor
from raac.harness.layer3_execution.tool_orchestrator import ToolOrchestrator
from raac.harness.layer3_execution.tools.workspace import WorkspaceTools
from raac.llm.client import LLMClient
from raac.llm.config import LLMConfig


class DemoLevel(BaseLevel):
    name = "demo"

    def build_graph(self, task, registry, config):
        assert task["requirement"] == "make a patch"
        assert registry.version
        assert config["version"] == "demo-v1"

        async def planner(state):
            assert state["active_agent"] == "planner"
            return {"plan": {"steps": ["edit"]}, "rounds": state["rounds"] + 1}

        async def coder(state):
            assert state["active_agent"] == "coder"
            return {"patch": "diff --git a/a b/a", "result": {"status": "completed"}}

        return LevelGraph(
            nodes={"planner": planner, "coder": coder},
            edges=(("planner", "coder"),),
            entry_node="planner",
            exit_node="coder",
        )


@pytest.mark.asyncio
async def test_engine_builds_graph_runs_nodes_and_logs_activations():
    engine = Engine(registry=load_registry(), checkpointer=InMemorySaver())
    result = await engine.run(
        run_id="engine-run-1", task={"requirement": "make a patch"}, level=DemoLevel(),
        config={"version": "demo-v1"},
    )
    assert result.state["plan"] == {"steps": ["edit"]}
    assert result.state["patch"].startswith("diff --git")
    assert result.state["active_agent"] is None
    assert "lifecycle" not in result.state
    assert [event["agent_id"] for event in engine.events if event["event"] == "agent_started"] == ["planner", "coder"]
    assert result.state["registry_version"] == load_registry().version
    assert len(result.state["events"]) == 4


class ResumeLevel(BaseLevel):
    name = "resume-demo"

    def build_graph(self, task, registry, config):
        def first(state):
            return {"messages": ["first"]}

        def second(state):
            return {"messages": ["second"], "result": "done"}

        return LevelGraph(
            nodes={"first": first, "second": second},
            edges=(("first", "second"),),
            entry_node="first",
            exit_node="second",
        )


@pytest.mark.asyncio
async def test_engine_resume_continues_same_graph_thread():
    checkpointer = InMemorySaver()
    engine = Engine(registry=load_registry(), checkpointer=checkpointer)
    first = await engine.run(
        run_id="resume-run", task={"requirement": "continue"}, level=ResumeLevel(),
        config={}, interrupt_after=["first"],
    )
    assert first.state["messages"] == ["first"]
    resumed = await engine.run(
        run_id="resume-run", task={"requirement": "ignored-on-resume"}, level=ResumeLevel(),
        config={}, resume=True,
    )
    assert resumed.resumed is True
    assert resumed.state["messages"] == ["first", "second"]
    assert resumed.state["result"] == "done"


class RuntimeLevel(BaseLevel):
    name = "runtime-demo"

    def build_graph(self, task, registry, config):
        async def agent_node(state):
            engine = config["engine"]
            return await engine.invoke_agent("Analyst", state, artifacts={"input": "fixture"})

        return LevelGraph(nodes={"agent": agent_node}, edges=(), entry_node="agent", exit_node="agent")


class FakeTools:
    def __init__(self):
        self.calls = []

    async def execute(self, role, name, arguments, **kwargs):
        self.calls.append((role.spec.id, name, arguments, kwargs))
        return {"ok": True, "result": {"content": "sandbox result"}}


class FakeRunner:
    def __init__(self, registry):
        self.registry = registry
        self.tools = FakeTools()
        self.provenance = {"source": "test"}
        self.calls = []

    async def __call__(self, role, request):
        self.calls.append((role, request))
        return {"summary": "LLM output"}


@pytest.mark.asyncio
async def test_engine_invokes_registry_runner_for_llm_output():
    registry = load_registry()
    runner = FakeRunner(registry)
    engine = Engine(registry=registry, runner=runner)
    result = await engine.run(
        run_id="runtime-run", task={"task_id": "task-1", "requirement": "inspect"},
        level=RuntimeLevel(), config={},
    )
    assert runner.calls[0][0] == "Analyst"
    assert runner.calls[0][1]["run_id"] == "runtime-run"
    assert result.state["agent_outputs"] == [{"role": "Analyst", "output": {"summary": "LLM output"}}]
    assert any(event["event"] == "agent_call_completed" for event in engine.events)


@pytest.mark.asyncio
async def test_engine_delegates_tools_to_orchestrator_runtime():
    registry = load_registry()
    runner = FakeRunner(registry)
    engine = Engine(registry=registry, runner=runner)
    state = {
        "run_id": "tool-run", "task": {"task_id": "task-2"}, "level": "runtime-demo",
    }
    update = await engine.execute_tool("Analyst", state, "read_file", {"path": "value.py"})
    assert update["tool_observations"][0]["observation"]["ok"] is True
    assert runner.tools.calls[0][1] == "read_file"
    assert runner.tools.calls[0][3]["origin"] == "engine"
    assert any(event["event"] == "tool_call_completed" for event in engine.events)


@pytest.mark.asyncio
async def test_from_runtime_uses_real_llm_client_path(tmp_path):
    project = tmp_path / "project"
    (project / "repo").mkdir(parents=True)
    (project / "requirement").mkdir()
    (project / "repo" / "value.py").write_text("VALUE = 1\n")
    (project / "requirement" / "user_chat.txt").write_text("Inspect source")
    requests = []

    def response(request):
        requests.append(request)
        return httpx.Response(200, json={
            "choices": [{"message": {"content": (
                '{"summary":"Observed source","requirements":[{"id":"R1",'
                '"description":"Inspect source","priority":"medium"}],'
                '"acceptance_criteria":["Inspect source"],"constraints":[],'
                '"open_questions":[],"interface_contracts":{}}')}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
        })

    registry = load_registry()
    client = LLMClient(
        LLMConfig(model="fixture"), log_path=tmp_path / "llm.jsonl",
        transport=httpx.MockTransport(response),
    )
    workspace = WorkspaceTools(project, LocalExecutor(LocalExecutionPolicy()))
    engine = Engine.from_runtime(
        registry=registry, client=client,
        tools=ToolOrchestrator(workspace), provenance={"test": True},
    )
    try:
        result = await engine.run(
            run_id="real-runtime-run", task={"task_id": "task-3", "requirement": "Inspect source"},
            level=RuntimeLevel(), config={},
        )
        assert result.state["agent_outputs"][0]["output"]["summary"] == "Observed source"
        assert requests and requests[0].url.path.endswith("/chat/completions")
        assert engine.runner is not None
        observation = await engine.execute_tool("Analyst", result.state, "read_file", {"path": "value.py"})
        assert observation["tool_observations"][0]["observation"]["result"]["content"] == "VALUE = 1\n"
    finally:
        await client.aclose()
