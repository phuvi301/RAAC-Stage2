"""Deterministic context representation and history reduction; no extra model calls or shared memory."""

from copy import deepcopy
import hashlib
import json

from pydantic import BaseModel, ConfigDict, Field
from raac.common.report_context import compact_test_report, compact_environment_report, clip_text
from langchain_core.messages import HumanMessage, trim_messages
from langchain_core.messages.utils import count_tokens_approximately


class RoleContextPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    enabled: bool = True
    command_output_filter: bool = False
    concise_output: bool = False
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


def compact_request(request):
    """Deduplicate a prepared request and compact schema annotations; preserve its role view."""
    result = deepcopy(request)
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
    if "output_schema" in result:
        result["output_schema"] = compact_schema(result["output_schema"])
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
    if tool_name == "run_tests":
        data = compact_test_report(data, max(256, limit - 100))
        result["result"] = data
    elif tool_name == "configure_environment":
        data = compact_environment_report(data, max(256, limit - 100))
        result["result"] = data
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

    Keep the latest test outcome using a bounded receipt if necessary. Full
    results stay in the audit. API usage remains authoritative; this library
    counter is only an approximation.
    """
    messages = [HumanMessage(content=_json(receipt), id=str(index)) for index, receipt in enumerate(receipts)]
    latest_test = next((index for index in range(len(receipts) - 1, -1, -1)
                        if receipts[index].get("tool") == "run_tests"), None)
    protected = [messages[latest_test]] if latest_test is not None else []
    if protected and count_tokens_approximately(protected) > maximum:
        receipt = receipts[latest_test]
        observation = receipt.get("observation", {})
        data = observation.get("result", observation) if isinstance(observation, dict) else {}
        data = data if isinstance(data, dict) else {"summary": str(data)}
        minimal = {key: deepcopy(data[key]) for key in
                   ("status", "tests_run", "failed", "patch_hash", "observation_ref") if key in data}
        minimal["summary"] = clip_text(str(data.get("summary", "")), 256)
        minimal["context_note"] = "Latest test outcome retained; full details in run audit/read_tool_result."
        failures = [item for item in data.get("results", []) if isinstance(item, dict) and item.get("status") in {"failed", "error"}]
        if failures:
            minimal["failure_sample"] = {key: clip_text(failures[0].get(key, ""), 120)
                                         for key in ("test", "status", "details")}
        if data.get("blocking_issues"):
            minimal["blocking_issue_count"] = len(data["blocking_issues"])
            minimal["blocking_issue_sample"] = clip_text(str(data["blocking_issues"][0]), 120)
        bounded = {"tool": "run_tests", "observation": {"ok": observation.get("ok", True) if isinstance(observation, dict) else True, "result": minimal}}
        protected = [HumanMessage(content=_json(bounded), id=str(latest_test))]
        while count_tokens_approximately(protected) > maximum:
            # Counts/status/ref remain protected even with pathological prose.
            for field in ("summary", "failure_sample", "blocking_issue_sample", "context_note"):
                minimal.pop(field, None)
                protected = [HumanMessage(content=_json(bounded), id=str(latest_test))]
                if count_tokens_approximately(protected) <= maximum:
                    break
            else:
                break
        messages[latest_test] = protected[0]
        receipts = deepcopy(receipts)
        receipts[latest_test] = bounded
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


def _prepare_messages(messages, policy: RoleContextPolicy, *, file_validator=None):
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


CONCISE_VERSION = "concise-v1"
CONCISE_GUIDANCE = (
    "\nBe concise: omit repetition and introductory prose. Preserve every required field, "
    "JSON key, acceptance criterion, constraint, API signature, code/diff and exact error string."
)


def prepare_messages(messages, policy: RoleContextPolicy, *, file_validator=None):
    from .command_output import filter_messages

    source = filter_messages(messages) if policy.command_output_filter else deepcopy(messages)
    if policy.enabled and len(source) > 1:
        try:
            task = json.loads(source[1]["content"])
        except (ValueError, TypeError, KeyError):
            task = None
        if isinstance(task, dict):
            compacted_task = compact_request(task)
            if compacted_task != task:
                source[1]["content"] = _json(compacted_task)
    prepared, metrics = _prepare_messages(source, policy, file_validator=file_validator)
    if policy.concise_output and prepared and CONCISE_GUIDANCE not in prepared[0]["content"]:
        prepared[0]["content"] += CONCISE_GUIDANCE
    metrics.update(before_chars=len(_json(messages)), after_chars=len(_json(prepared)))
    return prepared, metrics
