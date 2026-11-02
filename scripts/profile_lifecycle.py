"""Profile an offline lifecycle against an already installed Docker image."""

import argparse
import asyncio
import importlib
import json
import subprocess
from pathlib import Path
from statistics import median
from time import perf_counter
from uuid import uuid4

import httpx

import docker
from raac.harness.layer1_lifecycle import AgentRunner
from raac.harness.layer3_execution.docker_execution import (
    DockerExecutionPolicy,
    DockerExecutor,
    DockerRunWorkspace,
)
from raac.harness.layer3_execution.tool_orchestrator import ToolOrchestrator
from raac.harness.layer3_execution.tools.docker_workspace import DockerWorkspaceTools
from raac.llm.client import LLMClient
from raac.llm.config import LLMConfig
from raac.registry.loader import load_registry


def measure(target, name, metrics):
    original = getattr(target, name)
    record = metrics.setdefault(name, {"calls": 0, "seconds": 0.0})
    depth = 0

    def measured(*args, **kwargs):
        nonlocal depth
        depth += 1
        started = perf_counter()
        try:
            return original(*args, **kwargs)
        finally:
            depth -= 1
            if depth == 0:
                record["calls"] += 1
                record["seconds"] += perf_counter() - started

    setattr(target, name, measured)


async def profile(image, output, scenario="analyst"):
    output.mkdir(parents=True, exist_ok=False)
    run_id = "profile-" + uuid4().hex
    final = {"summary": "Observed source", "requirements": [{"id": "R1", "description": "Read source", "priority": "medium"}],
             "acceptance_criteria": ["Read source"], "constraints": [], "open_questions": []}
    call = {"id": "read", "type": "function", "function": {
        "name": "read_files", "arguments": json.dumps({"files": [{"path": "profile_value.py"}]})}}
    replies = [{"tool_calls": [call]}, {"content": json.dumps(final)}]
    role = "Analyst"
    if scenario == "coder":
        role = "Coder"
        def tool(name, args, identifier):
            return {"id": identifier, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}
        replies = [{"tool_calls": [tool("replace_text", {"path": "profile_value.py", "old_text": "VALUE = 1", "new_text": "VALUE = 2"}, "edit")]}]
        replies += [{"tool_calls": [
            tool("read_file", {"path": "profile_value.py"}, f"read-{i}"),
            tool("run_command", {"command": ["python", "-c", "print('offline smoke')"]}, f"command-{i}"),
        ]} for i in range(6)]
        replies += [{"content": ""}]
    expected_calls = len(replies)

    def respond(request):
        return httpx.Response(200, json={"choices": [{"message": replies.pop(0), "finish_reason": "stop"}],
                                            "usage": {"prompt_tokens": 10, "completion_tokens": 2}})

    connection = docker.from_env()
    try:
        connection.images.get(image)
        with DockerRunWorkspace(connection, image, run_id) as session:
            executor = DockerExecutor(session.container, DockerExecutionPolicy())
            seeded = session.container.exec_run(["python", "-c", "open('/workspace/profile_value.py','w').write('VALUE = 1\\n' + '# offline fixture history\\n' * 160)"], user="1000:1000")
            assert seeded.exit_code == 0
            workspace = DockerWorkspaceTools("Read source", executor)
            metrics = {}
            client = LLMClient(LLMConfig(model="offline-fixture"), log_path=output / "llm.jsonl",
                               transport=httpx.MockTransport(respond), trace_content=True)
            executor.trace = lambda event, data: client.trace_event({"run_id": run_id}, event, **data)
            tools = ToolOrchestrator(workspace)
            execute = tools.execute
            tool_depth = 0
            metrics["tools"] = {"calls": 0, "seconds": 0.0}

            async def measured_execute(*args, **kwargs):
                nonlocal tool_depth
                tool_depth += 1
                started = perf_counter()
                try:
                    observed = await execute(*args, **kwargs)
                    if tool_depth == 1:
                        assert observed["ok"] and observed["result"].get("failed", 0) == 0
                        assert observed["result"].get("exit_code", 0) == 0
                    return observed
                finally:
                    tool_depth -= 1
                    if tool_depth == 0:
                        metrics["tools"]["calls"] += 1
                        metrics["tools"]["seconds"] += perf_counter() - started

            tools.execute = measured_execute
            runner = AgentRunner(load_registry(Path(__file__).resolve().parents[1] / "registry"),
                                 client, tools, provenance={})
            for target, name in ((workspace, "_snapshot"), (runner, "_save"),
                                 (runner.checkpoints, "save"), (client.trace, "redact")):
                measure(target, name, metrics)
            started = perf_counter()
            try:
                result = await runner(role, {"run_id": run_id, "task_id": "profile", "work_id": "profile",
                                                   "scope": "task", "round": 0, "artifacts": {},
                                                   "incoming_message": {"from": "caller"}})
                if scenario == "coder":
                    assert "+VALUE = 2" in result.output
                else:
                    assert result["summary"] == final["summary"]
                assert not replies
                assert client.usage.snapshot(run_id=run_id)["calls"] == expected_calls
                assert metrics["tools"]["calls"] == (13 if scenario == "coder" else 1)
                metrics["total_seconds"] = perf_counter() - started
                metrics["baseline_files"] = len(workspace.baseline_files)
                metrics["baseline_bytes"] = sum(map(len, workspace.baseline_files.values()))
                events = [json.loads(line) for line in client.log_path.read_text(encoding="utf-8").splitlines()]
                metrics["model_seconds"] = sum(event.get("latency_ms", 0) for event in events if event["event"] == "llm_call") / 1000
                metrics["checkpoint_timings"] = [{key: event[key] for key in
                    ("snapshot_ms", "redact_ms", "serialize_ms", "sqlite_ms", "payload_bytes", "total_ms")}
                    for event in events if event["event"] == "checkpoint_timing"]
                metrics["command_timings"] = [{key: event[key] for key in
                    ("before_snapshot_ms", "execution_ms", "after_snapshot_ms")}
                    for event in events if event["event"] == "command_timing"]
            finally:
                await client.aclose()
            return metrics
    finally:
        connection.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--scenario", choices=["analyst", "coder"], default="analyst")
    parser.add_argument("--control-ref", help="Load the five lifecycle modules from this Git revision in memory only")
    args = parser.parse_args()
    if args.control_ref:
        root = Path(__file__).resolve().parents[1]
        for name in ("harness.layer3_execution.tools.workspace", "harness.layer3_execution.tools.docker_workspace",
                     "harness.layer3_execution.tool_orchestrator", "harness.layer1_lifecycle.checkpoint", "harness.layer1_lifecycle.agent"):
            module = importlib.import_module("raac." + name)
            path = "src/raac/" + name.replace(".", "/") + ".py"
            source = subprocess.run(["git", "show", args.control_ref + ":" + path], cwd=root,
                                    check=True, capture_output=True).stdout
            exec(compile(source, path, "exec"), module.__dict__)  # noqa: S102 -- explicit local Git control, in memory only.
        globals().update(AgentRunner=importlib.import_module("raac.harness.layer1_lifecycle.agent").AgentRunner,
                         DockerWorkspaceTools=importlib.import_module("raac.harness.layer3_execution.tools.docker_workspace").DockerWorkspaceTools,
                         ToolOrchestrator=importlib.import_module("raac.harness.layer3_execution.tool_orchestrator").ToolOrchestrator)
    rows = [asyncio.run(profile(args.image, args.output / str(index), args.scenario)) for index in range(3)]
    result = {"image": args.image, "scenario": args.scenario, "control_ref": args.control_ref, "runs": rows,
              "median_total_seconds": median(row["total_seconds"] for row in rows),
              "median_checkpoint_seconds": median(row["_save"]["seconds"] for row in rows)}
    (args.output / "summary.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
