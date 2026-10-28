"""Strict conversion of standalone DSML calls to the shared tool protocol."""
import json
import re


def _invalid_constant(value):
    raise ValueError("Non-finite JSON value")


def normalize_message(message, tools, call_id):
    calls = message.get("tool_calls")
    content = message.get("content") or ""
    marker = bool(re.search(r"<[/|\uff5c]*DSML", content))
    if calls is not None and calls != []:
        if marker or not isinstance(calls, list):
            raise ValueError("Invalid tool calls")
        ids = set()
        for call in calls:
            fn = call.get("function") if isinstance(call, dict) else None
            if (not isinstance(call, dict) or call.get("type") != "function"
                    or not isinstance(call.get("id"), str) or not call["id"] or call["id"] in ids
                    or not isinstance(fn, dict) or not isinstance(fn.get("name"), str)
                    or not fn["name"] or not isinstance(fn.get("arguments"), str)):
                raise ValueError("Invalid tool call")
            ids.add(call["id"])
        return message, "native"
    # A handoff JSON may quote protocol documentation without requesting a tool.
    if marker and not calls:
        try:
            json.loads(content)
        except ValueError:
            pass
        else:
            return message, "native"
    if not marker:
        return message, "native"
    text = re.sub(r"<(/?)[|\uff5c]{1,2}DSML[|\uff5c]{1,2} ", r"<\1DSML ", content.strip())
    outer = re.fullmatch(r"<DSML calls>\s*(.*?)\s*</DSML calls>", text, re.DOTALL)
    if not outer or not tools:
        raise ValueError("Invalid DSML envelope")
    allowed = {t["function"]["name"] for t in tools}
    body = outer[1]
    converted = []
    while body.strip():
        match = re.match(r'\s*<DSML invoke name="([a-zA-Z0-9_-]+)">\s*(.*?)\s*</DSML invoke>', body, re.DOTALL)
        if not match or match[1] not in allowed or len(converted) >= 4:
            raise ValueError("Invalid DSML invoke")
        args = {}
        params = match[2]
        while params.strip():
            param = re.match(r'\s*<DSML parameter name="([a-zA-Z0-9_-]+)" string="(true|false)">(.*?)</DSML parameter>', params, re.DOTALL)
            if not param or param[1] in args or "<DSML" in param[3] or "</DSML" in param[3]:
                raise ValueError("Invalid DSML parameter")
            args[param[1]] = param[3] if param[2] == "true" else json.loads(param[3], parse_constant=_invalid_constant)
            params = params[param.end():]
        converted.append({"id": f"{call_id}_{len(converted)}", "type": "function",
                          "function": {"name": match[1], "arguments": json.dumps(args, ensure_ascii=False)}})
        body = body[match.end():]
    if not converted:
        raise ValueError("Empty DSML calls")
    return {**message, "content": None, "tool_calls": converted}, "dsml"
