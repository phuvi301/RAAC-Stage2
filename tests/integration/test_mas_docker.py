"""Real isolated MAS tool execution; opt in explicitly, never simulate Docker."""

import asyncio
import io
import os
import time
from uuid import uuid4

import pytest

import docker
from raac.harness.layer3_execution.docker_execution import (
    DockerExecutionPolicy,
    DockerExecutor,
    DockerRunWorkspace,
)

pytestmark = pytest.mark.skipif(os.getenv("RAAC_DOCKER_TESTS") != "1", reason="Set RAAC_DOCKER_TESTS=1")


@pytest.fixture(scope="module")
def image():
    client = docker.from_env()
    image, _ = client.images.build(fileobj=io.BytesIO(
        b"FROM python:3.11-slim-bookworm\n"
        b"RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*\n"
        b"RUN python -m pip install pytest==8.3.5\n"
        b"RUN mkdir /workspace && chown 1000:1000 /workspace\n"
    ), rm=True, use_config_proxy=False)
    yield image.id
    client.images.remove(image.id, noprune=True)
    client.close()


@pytest.fixture
def workspace(image):
    client = docker.from_env()
    run_id = "mas-fixture-" + uuid4().hex
    with DockerRunWorkspace(client, image, run_id) as session:
        yield session.container
    assert not client.containers.list(all=True, filters={"label": "raac.mas.run_id=" + run_id})
    client.close()


@pytest.mark.asyncio
async def test_snapshot_excludes_generated_compiled_extensions(workspace):
    from raac.harness.layer3_execution.tools.docker_workspace import (
        DockerWorkspaceTools,
    )

    workspace.exec_run(["python", "-c", "open('/workspace/module.cpython-39-x86_64-linux-gnu.so','wb').truncate(5000000); open('/workspace/module.py','w').write('VALUE=1')"], user="1000:1000")
    tools = DockerWorkspaceTools("fixture", DockerExecutor(workspace, DockerExecutionPolicy()))
    assert tools.baseline_files == {"module.py": b"VALUE=1"}


@pytest.mark.asyncio
async def test_repository_cannot_shadow_trusted_helpers(workspace):
    from raac.harness.layer3_execution.tools.docker_workspace import (
        DockerWorkspaceTools,
    )

    workspace.exec_run(["python", "-c", "open('/workspace/json.py','w').write(\"print('[]'); raise SystemExit(0)\"); open('/workspace/hidden.py','w').write('VALUE=1')"], user="1000:1000")
    executor = DockerExecutor(workspace, DockerExecutionPolicy())
    tools = DockerWorkspaceTools("fixture", executor)
    assert set(tools.baseline_files) == {"json.py", "hidden.py"}
    result = await executor.run_command("/workspace", command=["/bin/echo", "trusted-wrapper"])
    assert result["exit_code"] == 0 and result["stdout"].strip() == "trusted-wrapper"
    result = await executor.run_command("/workspace", command=["/bin/echo", "safe-env"], env={"PATH": "/workspace", "PYTHONPATH": "/workspace"})
    assert result["exit_code"] == 0 and result["stdout"].strip() == "safe-env"


@pytest.mark.asyncio
async def test_command_really_runs_in_restricted_container(workspace, monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "host-only-fixture-secret")
    executor = DockerExecutor(workspace, DockerExecutionPolicy())
    result = await executor.run_command("/workspace", command=["python", "-c",
        "import os; print(os.getuid()); print(os.getenv('LLM_API_KEY')); open('value.txt','w').write('container-only')"])
    assert result["status"] == "completed" and result["exit_code"] == 0
    assert result["stdout"].splitlines() == ["1000", "None"]
    assert executor.read_file("/workspace/value.txt", 1024) == b"container-only"
    workspace.reload()
    assert workspace.attrs["HostConfig"]["NetworkMode"] == "none"
    assert not workspace.attrs["Mounts"]


@pytest.mark.asyncio
async def test_unittest_report_comes_from_container(workspace):
    executor = DockerExecutor(workspace, DockerExecutionPolicy())
    command = ["python", "-c", "open('test_value.py','w').write('import unittest\\nclass TestValue(unittest.TestCase):\\n def test_value(self): self.assertEqual(2+2,4)\\n')"]
    assert (await executor.run_command("/workspace", command=command))["exit_code"] == 0
    report = await executor.run_tests("/workspace", command=["python", "-m", "unittest", "discover"])
    assert report["status"] == "passed", report
    assert report["tests_run"] == 1


@pytest.mark.asyncio
async def test_timeout_kills_real_container(workspace):
    executor = DockerExecutor(workspace, DockerExecutionPolicy(timeout_seconds=0.2))
    result = await executor.run_command("/workspace", command=["python", "-c", "import time; time.sleep(30)"])
    assert result["status"] == "error" and result["error_kind"] == "TimeoutError"
    workspace.reload()
    assert workspace.status != "running"


@pytest.mark.asyncio
async def test_streaming_output_precedes_completion(workspace):
    events = []
    executor = DockerExecutor(workspace, DockerExecutionPolicy(), trace=lambda event, data: events.append((event, data)))
    task = asyncio.create_task(executor.run_command("/workspace", command=["python", "-u", "-c",
        "import time; print('started',flush=True); time.sleep(2)"]))
    deadline = time.monotonic() + 1.5
    while not events and time.monotonic() < deadline:
        await asyncio.sleep(0.02)
    assert events and not task.done()
    assert (await task)["exit_code"] == 0


@pytest.mark.asyncio
async def test_file_tools_and_shell_share_container_source(workspace):
    from raac.harness.layer3_execution.tools.docker_workspace import (
        DockerWorkspaceTools,
    )

    executor = DockerExecutor(workspace, DockerExecutionPolicy())
    tools = DockerWorkspaceTools("Fix value", executor)
    tools.edit_file(role="Coder", path="value.py", content="VALUE = 1\n")
    assert tools.read_file("value.py")["content"] == "VALUE = 1\n"
    tools.active_role = "Coder"
    result = await tools.run_command(command=["python", "-c", "open('new.py','w').write('NEW = 2\\n')"])
    assert result["exit_code"] == 0
    assert tools.coder_paths == {"value.py", "new.py"}
    assert tools.list_files()["total"] == 2
    assert tools.search_repo("NEW")["matches"][0]["path"] == "new.py"
    tools.replace_text(role="Coder", path="new.py", old_text="NEW = 2", new_text="NEW = 3")
    tools.apply_patch("--- a/new.py\n+++ b/new.py\n@@ -1 +1 @@\n-NEW = 3\n+NEW = 4\n")
    assert tools.read_file("new.py")["content"] == "NEW = 4\n"
    tools.delete_file(role="Coder", path="value.py")
    assert "value.py" not in tools.readable


@pytest.mark.asyncio
async def test_container_environment_uses_container_interpreter(workspace):
    from raac.harness.layer3_execution.tools.docker_workspace import (
        DockerWorkspaceTools,
    )

    executor = DockerExecutor(workspace, DockerExecutionPolicy())
    tools = DockerWorkspaceTools("Fix value", executor)
    tools.edit_file(role="Tester", path="test_value.py", content="import unittest\nclass TestValue(unittest.TestCase):\n def test_ok(self): self.assertTrue(True)\n")
    configured = await tools.environment.configure(python_executable="python", cwd=".",
        test_command=["python", "-m", "unittest", "discover"], check_command=["python", "-c", "import unittest"])
    assert configured["ready"] is True
    assert configured["configuration"]["python_executable"].startswith("/usr/")
    tools.active_role = "Tester"
    report = await tools.run_tests()
    assert report["status"] == "passed", report


@pytest.mark.asyncio
async def test_source_symlink_cannot_be_read_as_a_file(workspace):
    from raac.harness.layer3_execution.tools.docker_workspace import (
        DockerWorkspaceTools,
    )

    executor = DockerExecutor(workspace, DockerExecutionPolicy())
    tools = DockerWorkspaceTools("Fix value", executor)
    await executor.run_command("/workspace", command=["python", "-c", "import os; os.symlink('/etc/passwd','escape.py')"])
    with pytest.raises(ValueError):
        tools.read_file("escape.py")


@pytest.mark.asyncio
async def test_hardlinks_fail_closed_in_file_tools_and_snapshot(workspace):
    from raac.harness.layer3_execution.tools.docker_workspace import (
        DockerWorkspaceTools,
    )

    executor = DockerExecutor(workspace, DockerExecutionPolicy())
    tools = DockerWorkspaceTools("fixture", executor)
    await executor.run_command("/workspace", command=["python", "-c",
        "import os; open('value.py','w').write('VALUE=1'); os.link('value.py','alias.py')"])
    with pytest.raises(ValueError):
        tools.read_file("alias.py")
    with pytest.raises(ValueError):
        tools._snapshot()


@pytest.mark.asyncio
async def test_implementation_only_contract_rejects_coder_test_shell(workspace, tmp_path):
    from raac.harness.layer3_execution.tools.docker_workspace import (
        DockerWorkspaceTools,
    )

    tools = DockerWorkspaceTools("fixture", DockerExecutor(workspace, DockerExecutionPolicy()))
    with pytest.raises(PermissionError, match="Coder"):
        tools.edit_file(role="Coder", path="test_value.py", content="assert True\n")
    tools.active_role = "Coder"
    with pytest.raises(PermissionError, match="Coder"):
        await tools.run_command(command=["python", "-c", "open('test_value.py','w').write('assert True')"])
    with pytest.raises(ValueError, match="attribution"):
        tools.export_candidate(tmp_path / "candidate.patch")


@pytest.mark.asyncio
async def test_git_head_and_index_cannot_hide_coder_changes(workspace, tmp_path):
    from raac.harness.layer3_execution.tools.docker_workspace import (
        DockerWorkspaceTools,
    )

    executor = DockerExecutor(workspace, DockerExecutionPolicy())
    await executor.run_command("/workspace", command=["python", "-c", "open('value.py','w').write('VALUE = 0\\n')"])
    tools = DockerWorkspaceTools("fixture", executor)
    tools.active_role = "Coder"
    result = await tools.run_command(command=["/bin/sh", "-c",
        ("git init -q && git config user.email fixture@example.invalid && git config user.name Fixture && "
        "git add value.py && git commit -qm base && printf 'VALUE = 1\\n' > value.py && "
        "git add value.py && git commit -qm edited && git diff --exit-code")])
    assert result["exit_code"] == 0
    path = tmp_path / "candidate.patch"
    tools.export_candidate(path)
    assert "-VALUE = 0" in path.read_text() and "+VALUE = 1" in path.read_text()


@pytest.mark.asyncio
async def test_environment_verification_cannot_mutate_source(workspace):
    from raac.harness.layer3_execution.tools.docker_workspace import (
        DockerWorkspaceTools,
    )

    executor = DockerExecutor(workspace, DockerExecutionPolicy())
    tools = DockerWorkspaceTools("fixture", executor)
    with pytest.raises(PermissionError, match="Environment"):
        await tools.environment.configure(python_executable="python", cwd=".",
            test_command=["python", "-m", "unittest", "discover"],
            check_command=["python", "-c", "open('value.py','w').write('VALUE=9')"])
    assert tools.integrity_error


@pytest.mark.asyncio
async def test_complete_chain_with_http_mock_uses_real_container_tools(workspace, tmp_path, monkeypatch):
    from pathlib import Path

    import httpx
    import yaml

    import raac.benchmark.chain_runner as module
    from raac.benchmark.dataset import Task
    from raac.harness.layer3_execution.tools.docker_workspace import (
        DockerWorkspaceTools,
    )
    from raac.llm.config import ModelProfile, load_llm_config
    from raac.registry import load_registry

    root = Path(__file__).resolve().parents[2]
    executor = DockerExecutor(workspace, DockerExecutionPolicy())
    await executor.run_command("/workspace", command=["python", "-c",
        "open('solution.py','w').write('VALUE = 0\\n'); open('test_value.py','w').write('import unittest\\nfrom solution import VALUE\\nclass TestValue(unittest.TestCase):\\n def test_value(self): self.assertEqual(VALUE, 1)\\n')"])
    tools = DockerWorkspaceTools("Make VALUE equal 1", executor)
    registry = load_registry(root / "registry")
    config = load_llm_config(root / "configs/llm.yaml")
    config.profiles = {r.model.ref: ModelProfile(model="mock") for r in registry.roles.values()}
    counts = {}

    handler = fixture_handler(counts)

    original = module.LLMClient
    monkeypatch.setattr(module, "LLMClient", lambda *args, **kwargs: original(*args, transport=httpx.MockTransport(handler), **kwargs))
    result = await module.run_chain(Task("fixture__repo-1", "fixture/repo", "https://github.com/fixture/repo", "Fix value", "Make VALUE equal 1", "a" * 40),
        run_id="benchmark-chain-fixture", workspace=tools, registry=registry, llm_config=config,
        harness=yaml.safe_load((root / "configs/harness.yaml").read_text()),
        level_config=yaml.safe_load((root / "configs/levels/l3_collaboration.yaml").read_text()), output_dir=tmp_path)
    assert result["verdict"] == "PASS", result
    assert set(counts) == {"Environment", "Analyst", "Planner", "Coder", "Tester", "Reviewer"}
    assert result["usage"]["total_tokens"] == sum(counts.values()) * 11
    artifact = tools.export_candidate(tmp_path / "candidate.patch")
    assert artifact["changed_paths"] == ["solution.py"]

    # Reuse the exact production evaluator, but seed a separate image with pristine source.
    from raac.harness.layer3_execution.sandbox.docker_sandbox import DockerSandbox
    from raac.harness.layer3_execution.sandbox.evaluation import grade, payload

    client = workspace.client
    image, _ = client.images.build(fileobj=io.BytesIO((
        "FROM " + workspace.image.id + "\n"
        "RUN python -c \"open('/workspace/solution.py','w').write('VALUE = 0\\\\n')\"\n"
        "RUN chown -R 1000:1000 /workspace\n"
    ).encode()), rm=True, use_config_proxy=False)
    try:
        hidden_patch = "--- /dev/null\n+++ b/test_official.py\n@@ -0,0 +1,5 @@\n+from solution import VALUE\n+def test_fix():\n+    assert VALUE == 1\n+def test_preserve():\n+    assert 2 + 2 == 4\n"
        row = {"repo": "fixture/repo", "FAIL_TO_PASS": ["test_official.py::test_fix"],
               "PASS_TO_PASS": ["test_official.py::test_preserve"], "test_patch": hidden_patch}
        profile = {"test_command": ["python", "-m", "pytest"]}
        evaluation = {"test_patch": hidden_patch, "selectors": payload(row, profile),
            "runner_source": (root / "src/raac/harness/layer3_execution/sandbox/issue_runner.py").read_bytes()}
        sandbox = DockerSandbox(client=client)
        command = ["python", "/tmp/raac-issue-runner.py", "/tmp/raac-selectors.json"]
        baseline = await asyncio.to_thread(sandbox.execute, image, command, tmp_path, "baseline", profile=profile, evaluation=evaluation)
        candidate = await asyncio.to_thread(sandbox.execute, image, command, tmp_path, "candidate", (tmp_path / "candidate.patch").read_bytes(), profile=profile, evaluation=evaluation)
        assert grade(row, baseline, baseline=True)["valid"], baseline
        assert grade(row, candidate)["valid"], candidate
        noop = await asyncio.to_thread(sandbox.execute, image, command, tmp_path, "empty-candidate", b"", profile=profile, evaluation=evaluation)
        assert noop["status"] == "test_failed", noop
        assert grade(row, noop, baseline=True)["valid"]
        assert not grade(row, noop)["valid"]
        assert "test_official.py" not in tools.readable
        assert executor.read_file("/workspace/solution.py", 1024) == b"VALUE = 1\n"
        assert not client.containers.list(all=True, filters={"label": "raac=" + tmp_path.name})
        assert not client.volumes.list(filters={"label": "raac=" + tmp_path.name})
    finally:
        client.images.remove(image.id, noprune=True)


@pytest.mark.asyncio
async def test_batch_uses_fresh_containers_memory_and_independent_evaluation(image, tmp_path, monkeypatch):
    import json
    from pathlib import Path

    import httpx
    import yaml

    import raac.benchmark.chain_runner as chain
    from raac.benchmark import runner
    from raac.benchmark.dataset import load_tasks
    from raac.benchmark.run_record import RunRecord
    from raac.harness.layer3_execution.sandbox.docker_sandbox import DockerSandbox
    from raac.harness.layer3_execution.sandbox.evaluation import grade, payload
    from raac.harness.layer3_execution.tools.docker_workspace import (
        DockerWorkspaceTools,
    )

    root = Path(__file__).resolve().parents[2]
    client = docker.from_env()
    seeded, _ = client.images.build(fileobj=io.BytesIO((
        "FROM " + image + "\n"
        "RUN echo 'VALUE = 0' > /workspace/solution.py\n"
        "RUN printf 'import unittest\\nfrom solution import VALUE\\nclass TestValue(unittest.TestCase):\\n def test_value(self): self.assertEqual(VALUE, 1)\\n' > /workspace/test_value.py\n"
        "RUN chown -R 1000:1000 /workspace\n"
    ).encode()), rm=True, use_config_proxy=False)
    containers, providers = [], []
    original_client = chain.LLMClient

    def make_client(*args, **kwargs):
        counts = {}
        providers.append(counts)
        return original_client(*args, transport=httpx.MockTransport(fixture_handler(counts)), **kwargs)

    def prepare(task, config, output, policy, cancel, deadline):
        connection = docker.from_env()
        session = DockerRunWorkspace(connection, seeded.id, output.name, policy=policy)
        session.__enter__()
        containers.append(session.container.id)
        tools = DockerWorkspaceTools("Make VALUE equal 1", DockerExecutor(session.container, policy, deadline=deadline))
        assert tools.baseline_files["solution.py"] == b"VALUE = 0\n"
        return session, tools, {"image_id": seeded.id}

    hidden_patch = "--- /dev/null\n+++ b/test_official.py\n@@ -0,0 +1,5 @@\n+from solution import VALUE\n+def test_fix():\n+    assert VALUE == 1\n+def test_preserve():\n+    assert 2 + 2 == 4\n"
    row = {"repo": "fixture/repo", "FAIL_TO_PASS": ["test_official.py::test_fix"],
           "PASS_TO_PASS": ["test_official.py::test_preserve"], "test_patch": hidden_patch}
    profile = {"test_command": ["python", "-m", "pytest"]}
    evaluation = {"test_patch": hidden_patch, "selectors": payload(row, profile),
                  "runner_source": (root / "src/raac/harness/layer3_execution/sandbox/issue_runner.py").read_bytes()}

    def evaluate(task, *, patch_path, **kwargs):
        connection = docker.from_env()
        directory = Path(patch_path).parent
        sandbox = DockerSandbox(client=connection)
        try:
            command = ["python", "/tmp/raac-issue-runner.py", "/tmp/raac-selectors.json"]
            baseline = sandbox.execute(seeded, command, directory, "baseline", profile=profile, evaluation=evaluation)
            candidate = sandbox.execute(seeded, command, directory, "candidate", Path(patch_path).read_bytes(), profile=profile, evaluation=evaluation)
            assert grade(row, baseline, baseline=True)["valid"], baseline
            verdict = grade(row, candidate)
            return {"status": "resolved" if verdict["valid"] else "unresolved", "candidate_validation": verdict}
        finally:
            connection.close()

    monkeypatch.setattr(chain, "LLMClient", make_client)
    monkeypatch.setattr(runner, "prepare_workspace", prepare)
    monkeypatch.setattr(runner, "evaluate_candidate", evaluate)
    config = yaml.safe_load((root / "configs/benchmark.yaml").read_text())
    config["results"], config["dataset"] = str(tmp_path / "results"), str(root / config["dataset"])
    config_path = tmp_path / "benchmark.yaml"
    config_path.write_text(yaml.safe_dump(config))
    ids = list(load_tasks(Path(config["dataset"])))[:2]
    args = {"config_path": config_path, "task_ids": ids, "levels": ["L3"], "repetitions": 2, "batch_id": "docker-matrix"}
    try:
        result = await runner.run_benchmark_batch(**args)
        assert result["completed"] == 4 and len(set(containers)) == 4
        records = [RunRecord.model_validate_json(path.read_text()) for path in (tmp_path / "results").glob("benchmark-*/record.json")]
        assert len(records) == 4 and all(record.evaluation_status == "resolved" for record in records)
        assert len(providers) == 4 and all(counts == providers[0] for counts in providers)
        for record in records:
            output = tmp_path / "results" / record.run_id
            memories = list((output / "memory").glob("*.json"))
            assert memories
            for path in memories:
                assert json.loads(path.read_text())["identity"]["run_id"] == record.run_id
            events = [json.loads(line) for line in (output / "llm.jsonl").read_text().splitlines()]
            assert not any(event["event"] == "memory_loaded" for event in events)
            assert not client.containers.list(all=True, filters={"label": "raac=" + record.run_id})
            assert not client.volumes.list(filters={"label": "raac=" + record.run_id})
        await runner.run_benchmark_batch(**args, resume=True)
        assert len(containers) == 4
        assert not client.containers.list(all=True, filters={"label": "raac.mas.run_id"})
    finally:
        client.images.remove(seeded.id, noprune=True)
        client.close()


def fixture_handler(counts):
    import json

    import httpx

    patch = "--- a/solution.py\n+++ b/solution.py\n@@ -1 +1 @@\n-VALUE = 0\n+VALUE = 1\n"
    def handler(request):
        payload = json.loads(request.content)
        messages = payload["messages"]
        system = messages[0]["content"].replace("**", "")
        role = next(name for name in ("Environment", "Analyst", "Planner", "Coder", "Tester", "Reviewer") if system.startswith("You are the " + name))
        counts[role] = counts.get(role, 0) + 1
        work = json.loads(messages[1]["content"])
        assert work["execution_environment"]["mode"] == "docker"
        assert work["execution_environment"]["benchmark_contract"].startswith("implementation-only-v1")
        assert not work["execution_environment"]["python_executable"].startswith("C:")
        calls = None
        if role == "Environment" and counts[role] == 1:
            calls = [{"id": "env", "type": "function", "function": {"name": "configure_environment", "arguments": json.dumps({
                "python_executable": "python", "cwd": ".", "test_command": ["python", "-m", "unittest", "discover"],
                "check_command": ["python", "-c", "import unittest"]})}}]
            output = None
        elif role == "Environment":
            output = {"status": "ready", "summary": "Verified container", "blocking_issues": []}
        elif role == "Analyst":
            output = {"summary": "Fix value", "requirements": [{"id": "R1", "description": "Set value to one"}], "acceptance_criteria": ["Value equals one"]}
        elif role == "Planner":
            output = {"steps": [{"id": 1, "description": "Change value", "files": ["solution.py"], "depends_on": [], "ac_ids": ["AC1"]}], "covered_ac_ids": ["AC1"]}
        elif role == "Coder" and counts[role] == 1:
            assert "role_memory" not in work
            calls = [{"id": "write", "type": "function", "function": {"name": "edit_file", "arguments": json.dumps({"path": "solution.py", "content": "VALUE = 1\n"})}}]
            output = None
        elif role == "Coder":
            output = patch
        elif role == "Tester":
            output = {"tests_to_run": ["test_value.TestValue.test_value"], "results": [{"test": "test_value.TestValue.test_value", "status": "passed", "details": "Observed container test"}], "verdict": "pass", "covered_ac_ids": ["AC1"]}
        else:
            output = {"decision": "approve", "summary": "Correct value", "comments": [], "covered_ac_ids": ["AC1"]}
        message = {"role": "assistant", "content": output if isinstance(output, str) else json.dumps(output) if output else None}
        if calls:
            message["tool_calls"] = calls
        return httpx.Response(200, json={"model": "mock", "choices": [{"message": message, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 1, "prompt_tokens_details": {"cached_tokens": 0}}})
    return handler


@pytest.mark.asyncio
async def test_lifecycle_resume_on_same_container_preserves_baseline_and_attribution(workspace, tmp_path):
    import json
    from pathlib import Path
    import httpx
    from raac.harness.layer1_lifecycle import AgentRunner, AgentPaused, ApprovalDecision
    from raac.harness.layer3_execution.tool_orchestrator import ToolOrchestrator
    from raac.harness.layer3_execution.tools.docker_workspace import DockerWorkspaceTools
    from raac.llm.client import LLMClient
    from raac.llm.config import LLMConfig
    from raac.registry import load_registry

    root = Path(__file__).resolve().parents[2]
    executor = DockerExecutor(workspace, DockerExecutionPolicy())
    await executor.run_command("/workspace", command=["python", "-c", "open('value.py','w').write('VALUE = 1\\n')"])
    registry = load_registry(root / "registry")
    replies = [{"tool_calls": [
        {"id":"edit","type":"function","function":{"name":"edit_file","arguments":json.dumps({"path":"value.py","content":"VALUE = 2\n"})}},
        {"id":"read","type":"function","function":{"name":"read_file","arguments":json.dumps({"path":"value.py"})}},
    ]}]
    def handler(request):
        return httpx.Response(200,json={"choices":[{"message":replies.pop(0),"finish_reason":"stop"}],
            "usage":{"prompt_tokens":10,"completion_tokens":2}})
    def compose():
        client=LLMClient(LLMConfig(model="fixture"),log_path=tmp_path/"llm.jsonl",transport=httpx.MockTransport(handler))
        tools=ToolOrchestrator(DockerWorkspaceTools("fixture",DockerExecutor(workspace,DockerExecutionPolicy())))
        runner=AgentRunner(registry,client,tools,provenance={},auto_approve=False)
        return runner,client
    def decision(runner,pause):
        saved=runner.checkpoints.load(pause.invocation_id);op=saved["pending_approval"]
        return ApprovalDecision(pause.invocation_id,pause.revision,op["id"],op["arguments_hash"],True)
    runner,client=compose()
    request={"run_id":"docker-resume","task_id":"fixture","work_id":"fixture","scope":"task","artifacts":{}}
    try:
        with pytest.raises(AgentPaused) as first: await runner("Coder",request)
        with pytest.raises(AgentPaused) as second:
            await runner.resume(first.value.invocation_id,approval=decision(runner,first.value))
    finally: await client.aclose()
    restored,client=compose();replies.append({"content":""})
    try:
        result=await restored.resume(second.value.invocation_id,approval=decision(restored,second.value))
        assert "-VALUE = 1" in result.output and "+VALUE = 2" in result.output
        assert restored.tools.workspace.coder_paths=={"value.py"}
        assert restored.tools.workspace.code_edit_count==1
        assert client.usage.snapshot(run_id="docker-resume")["calls"]==2
        restored.tools.workspace.export_candidate(tmp_path/"candidate.patch")
        assert (tmp_path/"candidate.patch").read_text()==result.output
    finally:await client.aclose()
