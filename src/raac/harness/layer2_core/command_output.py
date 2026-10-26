"""Conservative pytest stdout filtering on model-bound copies only."""

import json
import re
from copy import deepcopy

ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
PROGRESS = re.compile(r"[.s]+(?:\s+\[\s*\d+%\])?")


def filter_observation(observation, name):
    result = deepcopy(observation)
    if not isinstance(result, dict) or not result.get("ok"):
        return result
    data = result.get("result", {})
    if name == "run_commands":
        for item in data.get("results", []):
            item["observation"] = filter_observation(item.get("observation"), "run_command")
        return result
    if name != "run_command" or data.get("status") != "completed" or data.get("exit_code") != 0:
        return result
    argv = data.get("command", [])
    if not isinstance(argv, list) or not argv or not all(isinstance(part, str) for part in argv):
        return result
    executable = argv[0].replace("\\", "/").rsplit("/", 1)[-1].lower()
    recognized = executable in {"pytest", "pytest.exe"} or (
        re.fullmatch(r"python(?:\d+(?:\.\d+)*)?(?:\.exe)?", executable)
        and argv[1:3] == ["-m", "pytest"])
    text = data.get("stdout")
    if not recognized or not isinstance(text, str) or data.get("stderr"):
        return result
    if any(marker in text for marker in ("Traceback", "traceback", "diff --git", "@@ ", "FAILED", "ERROR", "+++ ", "--- ")):
        return result
    clean = ANSI.sub("", text)
    if re.search(r"(?im)traceback|\b(?:failed|error)\b|^\s*(?:def |async def |class |from \S+ import |import |#include|function |public |const |let |var )", clean):
        return result
    lines, index = clean.splitlines(keepends=True), 0
    output = []
    while index < len(lines):
        line = lines[index]
        end = index + 1
        if not line.strip() or PROGRESS.fullmatch(line.strip()):
            while end < len(lines) and lines[end] == line:
                end += 1
        output.append(line)
        if end - index > 1:
            kind = "progress" if line.strip() else "blank"
            output.append(f"[previous {kind} line repeated {end - index - 1} more times]\n")
        index = end
    filtered = "".join(output)
    if len(filtered) < len(text):
        data["stdout"] = filtered
    return result


def filter_messages(messages):
    result, names = deepcopy(messages), {}
    for message in result:
        for call in message.get("tool_calls", []):
            names[call["id"]] = call.get("function", {}).get("name")
        if message.get("role") != "tool":
            continue
        try:
            observation = json.loads(message.get("content", ""))
        except (ValueError, TypeError):
            continue
        filtered = filter_observation(observation, names.get(message.get("tool_call_id")))
        if filtered != observation:
            message["content"] = json.dumps(filtered, ensure_ascii=False, separators=(",", ":"))
    return result
