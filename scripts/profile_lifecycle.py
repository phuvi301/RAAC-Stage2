"""Profile an offline lifecycle against an already installed Docker image."""

import argparse
import asyncio
import json
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


async def profile(image, output):
    output.mkdir(parents=True, exist_ok=False)
    run_id = "profile-" + uuid4().hex
    final = {"summary": "Observed source", "requirements": [{"id": "R1", "description": "Read source", "priority": "medium"}],
             "acceptance_criteria": ["Read source"], "constraints": [], "open_questions": []}
    call = {"id": "read", "type": "function", "function": {
        "name": "read_files", "arguments": json.dumps({"files": [{"path": "profile_value.py"}]})}}
    replies = [{"tool_calls": [call]}, {"content": json.dumps(final)}]

    def respond(request):
        return httpx.Response(200, json={"choices": [{"message": replies.pop(0), "finish_reason": "stop"}],
                                            "usage": {"prompt_tokens": 10, "completion_tokens": 2}})

    connection = docker.from_env()
    try:
        connection.images.get(image)
        with DockerRunWorkspace(connection, image, run_id) as session:
            executor = DockerExecutor(session.container, DockerExecutionPolicy())
            seeded = session.container.exec_run(["python", "-c", "open('/workspace/profile_value.py','w').write('VALUE = 1\\n')"], user="1000:1000")
            assert seeded.exit_code == 0
            workspace = DockerWorkspaceTools("Read source", executor)
            metrics = {}
            client = LLMClient(LLMConfig(model="offline-fixture"), log_path=output / "llm.jsonl",
                               transport=httpx.MockTransport(respond), trace_content=True)
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
                        assert observed["ok"] and observed["result"]["failed"] == 0
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
                result = await runner("Analyst", {"run_id": run_id, "task_id": "profile", "work_id": "profile",
                                                   "scope": "task", "round": 0, "artifacts": {},
                                                   "incoming_message": {"from": "caller"}})
                assert result["summary"] == final["summary"]
                assert not replies
                assert client.usage.snapshot(run_id=run_id)["calls"] == 2
                assert metrics["tools"]["calls"] == 1
                metrics["total_seconds"] = perf_counter() - started
                metrics["baseline_files"] = len(workspace.baseline_files)
                metrics["baseline_bytes"] = sum(map(len, workspace.baseline_files.values()))
                events = [json.loads(line) for line in client.log_path.read_text(encoding="utf-8").splitlines()]
                metrics["model_seconds"] = sum(event.get("latency_ms", 0) for event in events if event["event"] == "llm_call") / 1000
            finally:
                await client.aclose()
            return metrics
    finally:
        connection.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    rows = [asyncio.run(profile(args.image, args.output / str(index))) for index in range(3)]
    result = {"image": args.image, "runs": rows,
              "median_total_seconds": median(row["total_seconds"] for row in rows),
              "median_checkpoint_seconds": median(row["_save"]["seconds"] for row in rows)}
    (args.output / "summary.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
