"""Content-free representation estimates; never provider usage or billing estimates."""

import json
import math


def encoded(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def size(value):
    text = encoded(value)
    return {"chars": len(text), "bytes": len(text.encode("utf-8")),
            "estimated_tokens": math.ceil(len(text) / 4)}


def request_metrics(messages, tools):
    groups = {name: [] for name in ("system", "task_handoff", "assistant_history", "tool_results", "synthesized_context")}
    for index, message in enumerate(messages):
        role = message.get("role")
        name = ("system" if role == "system" else "task_handoff" if index == 1 and role == "user"
                else "assistant_history" if role == "assistant" else "tool_results" if role == "tool"
                else "synthesized_context")
        groups[name].append(message)
    return {"estimator": "json-chars-div4-v1", "groups": {name: size(value) for name, value in groups.items()},
            "tool_schemas": size(tools), "total": size({"messages": messages, "tools": tools})}
