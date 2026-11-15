"""Khung chạy role chung cho mọi flow.

run_role(runtime, flow, role_name, schema, artifacts, check):
  1. Dựng request cho AgentRunner (run_id/task_id của runtime, output_schema của flow, artifacts là đầu vào
     đã duyệt của flow trước cùng góp ý của người dùng).
  2. Chạy role (tool đọc repo, memory, giới hạn token, log xem bằng show-log/TUI).
  3. Kiểm schema (pydantic) rồi kiểm luật bằng code (`check`). Vi phạm luật thì chạy lại, đưa vào artifacts bản
     trả lời trước và danh sách lỗi, tối đa `retries` lần; vẫn sai thì báo FlowError (chỉ tên luật, không chép nội dung).
"""

from collections.abc import Callable

from pydantic import BaseModel, ValidationError


class FlowError(RuntimeError):
    pass


async def run_role(runtime, flow: str, role_name: str, schema: type[BaseModel], artifacts: dict,
                   check: Callable[[BaseModel], list[str]], *, retries: int = 2, round_number: int = 0):
    problems: list[str] = []
    previous = None
    for attempt in range(1, retries + 2):
        payload = dict(artifacts)
        if problems:
            payload["previous_output"] = previous
            payload["rule_violations"] = problems
        request = {
            "run_id": runtime.run_id, "task_id": runtime.task_id, "work_id": f"{runtime.task_id}:{flow}",
            "scope": "task", "level": "PRE", "round": round_number,
            "incoming_message": {"from": "User", "flow": flow},
            "artifacts": payload, "output_format": "json", "output_schema": schema.model_json_schema(),
        }
        result = await runtime.run(role_name, request)
        raw = getattr(result, "output", result)
        try:
            output = schema.model_validate(raw)
        except ValidationError:
            raise FlowError(f"{flow}: role {role_name} trả về đầu ra sai schema") from None
        problems = check(output)
        if not problems:
            return output
        note = getattr(runtime, "note", None)
        if callable(note):
            note("flow_rule_violation", flow=flow, attempt=attempt, problems=len(problems), first=problems[0])
        previous = output.model_dump()
    raise FlowError(f"{flow}: đầu ra vẫn vi phạm luật sau {retries + 1} lần: " + "; ".join(problems))
