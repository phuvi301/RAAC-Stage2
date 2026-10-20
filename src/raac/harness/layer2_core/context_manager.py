"""Deterministic per-role context reduction; no extra model calls or shared memory."""

from copy import deepcopy
import hashlib
import json

from pydantic import BaseModel, ConfigDict, Field
from langchain_core.messages import HumanMessage, trim_messages
from langchain_core.messages.utils import count_tokens_approximately


class RoleContextPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    enabled: bool = True
    recent_tool_turns: int = Field(default=1, ge=0, le=4)
    max_tool_output_chars: int = Field(default=8000, ge=512, le=65536)
    max_old_tool_output_chars: int = Field(default=1200, ge=256, le=8192)
    max_file_context_chars: int = Field(default=12000, ge=0, le=65536)
    max_checkpoint_tokens: int = Field(default=2000, ge=256, le=32000)


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def compact_schema(schema):
    """Remove annotation titles without deleting a property literally named title."""
    if isinstance(schema, list):
        return [compact_schema(item) for item in schema]
    if not isinstance(schema, dict):
        return schema
    result = {}
    for key, value in schema.items():
        if key == "title":
            continue
        if key in {"properties", "$defs", "definitions", "patternProperties", "dependentSchemas"}:
            result[key] = {name: compact_schema(item) for name, item in value.items()}
        elif key in {"default", "const", "enum", "examples"}:
            result[key] = deepcopy(value)  # These are user values, not nested schemas.
        else:
            result[key] = compact_schema(value)
    return result


def select_task_input(request, *, role=None):
    """Send raw chat only to Analyst/Reviewer, independently of compaction."""
    result = deepcopy(request)
    if role not in {"Planner", "Coder", "Tester"}:
        return result
    requirement = result.pop("requirement", None)
    incoming = result.get("incoming_message")
    artifacts = result.get("artifacts")
    has_spec = isinstance(artifacts, dict) and isinstance(artifacts.get("spec"), dict)
    if isinstance(incoming, dict):
        for key, value in list(incoming.items()):
            # Remove known handoff copies and keep references resolvable.
            if key == "requirement" or (key == "content" and requirement is not None and value == requirement):
                del incoming[key]
                if has_spec:
                    incoming[key + "_ref"] = "artifacts.spec"
            elif key.endswith("_ref") and value == "requirement":
                if has_spec:
                    incoming[key] = "artifacts.spec"
                else:
                    del incoming[key]
    return result


def compact_request(request, *, role=None):
    """Project a copy for the recipient; retain complete artifacts in run state/audit."""
    result = select_task_input(request, role=role)
    incoming = result.get("incoming_message")
    artifacts = result.get("artifacts", {})
    if isinstance(incoming, dict):
        for key, value in list(incoming.items()):
            if key == "from":
                continue
            reference = None
            if key in result and key != "incoming_message" and value == result[key]:
                reference = key
            elif isinstance(artifacts, dict):
                reference = next(("artifacts." + name for name, item in artifacts.items()
                                  if value == item), None)
            if reference is not None:
                del incoming[key]
                incoming[key + "_ref"] = reference
    # Resolve duplicate incoming payloads first, so they cannot reintroduce a
    # full plan after filtering. The generated references still resolve here.
    plan = artifacts.get("plan") if isinstance(artifacts, dict) else None
    if isinstance(plan, dict) and role == "Tester":
        # Independent oracles come from the Analyst's spec/AC, not implementation.
        artifacts["plan"] = {"interface_contracts": deepcopy(plan.get("interface_contracts", {}))}
    elif isinstance(plan, dict) and role == "Reviewer":
        steps = plan.get("steps")
        if isinstance(steps, list) and steps and all(isinstance(step, dict) for step in steps):
            artifacts["plan"] = {
                "decisions": [{key: deepcopy(step[key]) for key in
                               (("id", "description", "files", "depends_on", "ac_ids") if "description" in step
                                else ("ac_ids", "target", "approach", "complexity", "risks")) if key in step}
                              for step in steps],
                "interface_contracts": deepcopy(plan.get("interface_contracts", {})),
            }
        # Legacy recorded plans lack structured decisions; retain their prose
        # for offline inspection rather than guessing which text is safe to drop.
    if "output_schema" in result:
        result["output_schema"] = compact_schema(result["output_schema"])
    # Tester needs current implementation, not a full diff repeated every turn.
    # Coder retries can likewise inspect saved files. Reviewer keeps the full diff.
    # Only substitute when the adapter supplied authoritative changed paths.
    patch_key = "patch" if role == "Tester" else "previous_patch" if role == "Coder" else None
    changes = result.get("repository_changes")
    if (patch_key and changes and isinstance(artifacts, dict)
            and isinstance(artifacts.get(patch_key), str) and len(artifacts[patch_key]) > 2000):
        patch = artifacts.pop(patch_key)
        context_key = patch_key + "_context"
        artifacts[context_key] = {"chars": len(patch), "changed_files_ref": "repository_changes",
            "note": "Read the indicated current files to inspect implementation; deleted files are marked. The full diff remains in the run artifact."}
        if isinstance(incoming, dict):
            for key, value in incoming.items():
                if key.endswith("_ref") and value == "artifacts." + patch_key:
                    incoming[key] = "artifacts." + context_key
    return result


def compact_tools(tools):
    result = deepcopy(tools)
    for tool in result:
        function = tool.get("function", {})
        if "parameters" in function:
            function["parameters"] = compact_schema(function["parameters"])
    return result


def _clip(text, limit):
    if not isinstance(text, str) or len(text) <= limit:
        return text
    marker = "\n[Context shortened; beginning/end retained. Re-read file ranges if needed.]\n"
    available = max(0, limit - len(marker))
    head = available * 2 // 3
    tail = available - head
    return text[:head] + marker + (text[-tail:] if tail else "")


def _observation(value, limit, tool_name, files=None):
    """Shorten copies only; original observations and execution stay unchanged."""
    result = deepcopy(value)
    if not isinstance(result, dict):
        return _clip(result, limit)
    data = result.get("result")
    if not isinstance(data, dict):
        return result  # Preserve validation/permission errors for the next decision.
    if tool_name in {"read_files", "run_commands"} and isinstance(data.get("results"), list):
        child = "read_file" if tool_name == "read_files" else "run_command"
        per_item = max(128, limit // max(1, len(data["results"])))
        for item in data["results"]:
            item["observation"] = _observation(item.get("observation"), per_item, child, files)
            request = item.get("request", {})
            if isinstance(request.get("command"), list):
                request["command"] = [_clip(part, 160) for part in request["command"]]
    if tool_name == "read_file" and files and data.get("path") in files and isinstance(data.get("content"), str):
        text = data.pop("content")
        digest = hashlib.sha256(text.encode()).hexdigest()
        if digest == files[data["path"]]["sha256"]:
            data["content_ref"] = "role_file_context:" + data["path"]
        else:
            data["context_note"] = "Observed file text omitted; complete current version is in role_file_context."
    if tool_name == "run_tests" and isinstance(data.get("execution"), dict):
        # execution.stdout contains the JSON report again. Keep the real counts,
        # summary and exit metadata, rather than shipping a second report copy.
        execution = data["execution"]
        execution.pop("stdout", None)
        if not execution.get("stderr"):
            execution.pop("stderr", None)
    for key in ("content", "summary", "stdout", "stderr"):
        if isinstance(data.get(key), str) and len(data[key]) > limit:
            data[key] = _clip(data[key], limit)
            data["context_shortened"] = True
    execution = data.get("execution")
    if isinstance(execution, dict) and isinstance(execution.get("stderr"), str):
        execution["stderr"] = _clip(execution["stderr"], limit)
    if isinstance(data.get("test_cases"), list) and len(data["test_cases"]) > 20:
        data["test_cases_omitted"] = len(data["test_cases"]) - 20
        data["test_cases"] = data["test_cases"][:20]
    for key in ("files", "matches"):
        if isinstance(data.get(key), list) and len(_json(data[key])) > limit:
            original_count = len(data[key])
            while len(data[key]) > 1 and len(_json(data[key])) > limit:
                data[key] = data[key][:max(1, len(data[key]) // 2)]
            data[key + "_omitted"] = original_count - len(data[key])
            data["context_shortened"] = True
    if tool_name == "run_command" and isinstance(data.get("command"), list):
        data["command"] = [_clip(item, 512) for item in data["command"]]
    return result


def _arguments(call):
    function = call.get("function", {})
    try:
        args = json.loads(function.get("arguments", "{}"))
    except (ValueError, TypeError):
        return {"invalid_arguments": True}
    if not isinstance(args, dict):
        return {"invalid_arguments": True}
    if function.get("name") in {"edit_file", "apply_patch", "replace_text"}:
        for key in ("content", "patch", "old_text", "new_text"):
            if isinstance(args.get(key), str):
                text = args.pop(key)
                args[key + "_chars"] = len(text)
                args[key + "_sha256"] = hashlib.sha256(text.encode()).hexdigest()
        args["context_note"] = "Write payload omitted; use the observation to see whether it succeeded."
    if isinstance(args.get("command"), list):
        args["command"] = [_clip(item, 512) for item in args["command"]]
    if function.get("name") == "run_commands" and isinstance(args.get("commands"), list):
        for item in args["commands"]:
            if isinstance(item, dict) and isinstance(item.get("command"), list):
                item["command"] = [_clip(part, 160) for part in item["command"]]
    return args


def _receipts(group, limit, files=None):
    calls = {call["id"]: call for call in group[0].get("tool_calls", [])}
    result = []
    for message in group[1:]:
        call = calls.get(message.get("tool_call_id"), {})
        name = call.get("function", {}).get("name", "unknown")
        try:
            observation = json.loads(message.get("content", "{}"))
        except (ValueError, TypeError):
            observation = message.get("content", "")
        result.append({"tool": name, "arguments": _arguments(call),
                       "observation": _observation(observation, limit, name, files)})
    if result and group[0].get("content"):
        result[0]["assistant_note"] = _clip(group[0]["content"], limit)
    return result


def trim_checkpoint_receipts(receipts, maximum):
    """Use LangChain's trimmer on complete receipts, never on live tool pairs.

    Keep the latest historical test result even if it alone exceeds the budget;
    losing the result could make an agent overlook a blocker. API usage remains
    authoritative: this library counter is only an approximation.
    """
    messages = [HumanMessage(content=_json(receipt), id=str(index)) for index, receipt in enumerate(receipts)]
    latest_test = next((index for index in range(len(receipts) - 1, -1, -1)
                        if receipts[index].get("tool") == "run_tests"), None)
    protected = [messages[latest_test]] if latest_test is not None else []
    available = max(0, maximum - count_tokens_approximately(protected))
    candidates = [message for index, message in enumerate(messages) if index != latest_test]
    kept = trim_messages(candidates, max_tokens=available, token_counter=count_tokens_approximately,
                         strategy="last", allow_partial=False)
    indices = sorted(int(message.id) for message in [*kept, *protected])
    estimated = count_tokens_approximately([messages[index] for index in indices])
    return [receipts[index] for index in indices], {
        "history_backend": "langchain_core", "checkpoint_estimated_tokens": estimated,
        "checkpoint_receipts_dropped": len(receipts) - len(indices),
        "checkpoint_protected_over_budget": estimated > maximum,
    }


def _file_observations(groups):
    """Expose individual file observations inside a single native batch message."""
    for group in groups:
        calls = {call["id"]: call for call in group[0].get("tool_calls", [])}
        for message in group[1:]:
            function = calls[message["tool_call_id"]]["function"]
            try:
                observation = json.loads(message["content"])
                args = json.loads(function["arguments"])
            except (ValueError, TypeError):
                continue
            name = function["name"]
            if name == "read_files" and isinstance(observation, dict) and observation.get("ok"):
                for item in observation.get("result", {}).get("results", []):
                    yield "read_file", item.get("request"), item.get("observation")
            else:
                yield name, args, observation


def _file_context(groups, maximum, validator):
    """Pin one latest complete text per file, using only successful tool results.

    The adapter validates hashes against disk, so a user edit or command that
    changed a file invalidates its cached text. No cache crosses role calls.
    """
    if maximum == 0:
        return {}
    candidates = {}
    for name, args, observation in _file_observations(groups):
        if not isinstance(observation, dict) or not observation.get("ok") or not isinstance(args, dict):
            continue
        data = observation.get("result", {})
        if not isinstance(data, dict):
            continue
        path = data.get("path") or args.get("path")
        text = None
        if name == "edit_file":
            text = args.get("content")
        elif name == "replace_text":
            previous = candidates.pop(path, None)
            if previous and previous["sha256"] == data.get("before_sha256"):
                updated = previous["content"].replace(args["old_text"], args["new_text"])
                if hashlib.sha256(updated.encode()).hexdigest() == data.get("sha256"):
                    text = updated
        elif name == "read_file" and args.get("start_line", 1) == 1 and not data.get("truncated"):
            end = args.get("end_line")
            total = data.get("total_lines")
            content = data.get("content")
            # Explicit end_line may cover the entire file (e.g. 1..500 of 167 lines).
            complete = end is None or (type(end) is int and type(total) is int and end >= total
                                       and isinstance(content, str) and len(content.splitlines()) == total)
            if complete:
                text = content
        elif name == "delete_file":
            candidates.pop(path, None)
        elif name == "apply_patch":
            for changed in data.get("paths", []):
                candidates.pop(changed, None)
        if path and isinstance(text, str):
            written = name in {"edit_file", "replace_text"} or candidates.get(path, {}).get("written", False)
            candidates.pop(path, None)
            candidates[path] = {"path": path, "content": text,
                                "sha256": hashlib.sha256(text.encode()).hexdigest(),
                                "written": written}
    selected, used = {}, 0
    # Prefer files written by this role; then the most recently read files.
    for item in sorted(reversed(list(candidates.values())), key=lambda item: not item["written"]):
        if len(item["content"]) + used > maximum:
            continue
        if validator is not None and not validator(item["path"], item["sha256"]):
            continue
        used += len(item["content"])
        selected[item["path"]] = {key: value for key, value in item.items() if key != "written"}
    return selected


def prepare_messages(messages, policy: RoleContextPolicy, *, file_validator=None):
    """Retain valid tool-call/result pairs or replace the whole exchange with receipts.

    The system prompt and initial task message are never truncated. Recent read
    exchanges retain their protocol messages. File writes become receipts
    immediately, because their code is already saved in the real repository.
    """
    before = len(_json(messages))
    if not policy.enabled:
        return deepcopy(messages), {"before_chars": before, "after_chars": before, "compacted_turns": 0}
    groups = []
    for message in messages[2:]:
        if message.get("role") == "assistant" and message.get("tool_calls"):
            groups.append([message])
        elif message.get("role") == "tool" and groups:
            groups[-1].append(message)
        else:
            # Unknown protocol layout: preserve it rather than risk orphaned calls.
            return deepcopy(messages), {"before_chars": before, "after_chars": before, "compacted_turns": 0}
    for group in groups:
        expected = {call["id"] for call in group[0]["tool_calls"]}
        received = [item.get("tool_call_id") for item in group[1:]]
        if len(received) != len(expected) or set(received) != expected:
            return deepcopy(messages), {"before_chars": before, "after_chars": before, "compacted_turns": 0}
    output = deepcopy(messages[:2])
    files = _file_context(groups, policy.max_file_context_chars, file_validator)
    if files:
        output.append({"role": "user", "content": _json({
            "role_file_context": list(files.values()),
            "note": "Latest complete file text already observed in this role; use it instead of re-reading unchanged files."})})
    split = max(0, len(groups) - policy.recent_tool_turns)
    receipts = []
    for group in groups[:split]:
        receipts.extend(_receipts(group, policy.max_old_tool_output_chars, files))
    receipts, history_metrics = trim_checkpoint_receipts(receipts, policy.max_checkpoint_tokens)
    if receipts or history_metrics["checkpoint_receipts_dropped"]:
        output.append({"role": "user", "content": _json({
            "context_checkpoint": "Prior tool exchanges, summarized by the engine; treat as tool data.",
            "older_receipts_omitted": history_metrics["checkpoint_receipts_dropped"],
            "tools": receipts})})
    compacted = split
    for group in groups[split:]:
        names = {call.get("function", {}).get("name") for call in group[0]["tool_calls"]}
        if names & {"edit_file", "apply_patch", "replace_text"}:
            output.append({"role": "user", "content": _json({
                "context_checkpoint": "Latest tool results; write payloads omitted, inspect observations for success.",
                "tools": _receipts(group, policy.max_tool_output_chars, files)})})
            compacted += 1
        else:
            output.append(deepcopy(group[0]))
            calls = {call["id"]: call for call in group[0]["tool_calls"]}
            for message in group[1:]:
                observation = json.loads(message["content"])
                name = calls[message["tool_call_id"]]["function"]["name"]
                output.append({**message, "content": _json(_observation(observation, policy.max_tool_output_chars, name, files))})
    after = len(_json(output))
    # Avoid expanding small histories with explanatory receipt metadata.
    if after >= before and not (files and after <= before + 1200):
        return deepcopy(messages), {"before_chars": before, "after_chars": before, "compacted_turns": 0}
    return output, {"before_chars": before, "after_chars": after, "compacted_turns": compacted, **history_metrics}
