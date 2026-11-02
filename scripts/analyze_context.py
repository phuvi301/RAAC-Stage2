"""Replay recorded request representations offline; never call a provider or execute tools."""

import argparse
import hashlib
import json
import time
from pathlib import Path

from raac.harness.layer2_core.context_manager import (
    RoleContextPolicy,
    compact_tools,
    prepare_messages,
)
from raac.harness.layer2_core.context_metrics import encoded, request_metrics

POLICIES = {
    "baseline": RoleContextPolicy(enabled=False),
    "compact": RoleContextPolicy(),
    "filtered": RoleContextPolicy(command_output_filter=True),
    "concise": RoleContextPolicy(command_output_filter=True, concise_output=True),
}


def analyze(path):
    events = [json.loads(line) for line in Path(path).read_text(encoding="utf-8-sig").splitlines() if line.strip()]
    requests = [event for event in events if event.get("event") == "llm_request"
                and isinstance(event.get("request", {}).get("messages"), list)]
    report = {"trace": str(path), "source_sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest(), "requests": len(requests),
              "availability": "recorded_request_representation" if requests else "unavailable",
              "raw_before_preparation": "available_in_metrics_only" if any(e.get("event") == "context_metrics" for e in events) else "unavailable",
              "detail_reads": sum(e.get("event") == "tool_call_started" and e.get("tool_name") == "read_tool_result" for e in events),
              "provider_usage": [e.get("usage") for e in events if e.get("event") == "llm_call"],
              "run_usage": [e.get("usage") for e in events if e.get("event") == "run_completed"],
              "policy_settings": {name: policy.model_dump() for name, policy in POLICIES.items()},
              "policies": {}}
    for name, policy in POLICIES.items():
        calls, seen = [], set()
        for event in requests:
            request = event["request"]
            messages, tools = request["messages"], request.get("tools", [])
            started = time.perf_counter()
            prepared, _ = prepare_messages(messages, policy)
            declarations = compact_tools(tools) if policy.enabled else tools
            elapsed = (time.perf_counter() - started) * 1000
            repeated = 0
            current = set()
            for item in [*prepared, *declarations]:
                raw = encoded(item).encode("utf-8")
                digest = hashlib.sha256(raw).digest()
                if digest in seen:
                    repeated += len(raw)
                current.add(digest)
            seen.update(current)
            calls.append({"llm_call_id": event.get("llm_call_id"),
                          "run_id": event.get("run_id"), "agent_id": event.get("agent_id"),
                          "agent_call_id": event.get("agent_call_id"), "turn": event.get("turn"),
                          "before": request_metrics(messages, tools), "after": request_metrics(prepared, declarations),
                          "preparation_ms": round(elapsed, 3), "repeated_representation_bytes": repeated})
        report["policies"][name] = calls
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("traces", nargs="*", type=Path)
    parser.add_argument("--latest", type=int, default=0)
    parser.add_argument("--results", type=Path, default=Path("results"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    paths = args.traces
    if args.latest:
        candidates = sorted(args.results.glob("*/llm.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
        paths = []
        for path in candidates:
            if analyze(path)["requests"]:
                paths.append(path)
                if len(paths) >= args.latest:
                    break
    reports = [analyze(path) for path in paths]
    result = {"estimator": "json-chars-div4-v1", "requested_latest": args.latest,
              "found": len(reports), "reports": reports,
              "limits": "Replay starts from recorded request, not unavailable original context. Repetition can be cached. No billing or solve-rate claim."}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    print(json.dumps({"output": str(args.output), "traces": len(reports)}))


if __name__ == "__main__":
    main()
