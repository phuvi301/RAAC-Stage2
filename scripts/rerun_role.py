"""Chạy lại MỘT role của một run cũ, dùng đúng đầu vào role đó đã nhận lúc ấy.

    python scripts/rerun_role.py --run chain-30425902... --role coder [--project-dir currentProject] [--dry-run]

Các role đứng trước không chạy lại: đầu vào (spec, plan, báo cáo test...) lấy từ `llm.jsonl` của run cũ.
Role chạy trên workspace HIỆN TẠI. Với Coder hãy đặt lại repo về trạng thái ban đầu trước (git checkout -- .),
nếu không baseline của run mới đã chứa sẵn các sửa đổi cũ.
"""

import argparse
import asyncio
import json
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

# Các khoá do runner tự thêm vào tin nhắn task; build lại từ đầu mỗi lần chạy.
RUNNER_KEYS = ("repository_files", "repository_listing_truncated", "execution_environment",
               "previous_attempt", "role_memory", "runtime_progress")


def find_log(run: str) -> Path:
    path = Path(run)
    if path.is_file():
        return path
    for candidate in (path / "llm.jsonl", ROOT / "results" / run / "llm.jsonl"):
        if candidate.is_file():
            return candidate
    raise SystemExit(f"Không thấy llm.jsonl của run: {run}")


def rebuild_request(log_path: Path, role_id: str, round_number: int | None = None) -> dict:
    """Đầu vào role nhận ở lượt LLM đầu tiên của round đó (mặc định round cuối role từng chạy)."""
    first = {}
    for line in log_path.read_text(encoding="utf-8").splitlines():
        event = json.loads(line)
        if event.get("event") != "llm_request" or event.get("agent_id") != role_id:
            continue
        key = int(event.get("round", 0))
        if key not in first or int(event.get("turn", 1)) < int(first[key].get("turn", 1)):
            first[key] = event
    if not first:
        raise SystemExit(f"Run này không có lượt LLM nào của role '{role_id}'")
    chosen = round_number if round_number is not None else max(first)
    if chosen not in first:
        raise SystemExit(f"Role '{role_id}' không chạy ở round {chosen}; có các round: {sorted(first)}")
    for message in first[chosen]["request"]["messages"]:
        if message.get("role") != "user" or not isinstance(message.get("content"), str):
            continue
        try:
            request = json.loads(message["content"])
        except ValueError:
            continue
        if isinstance(request, dict) and "incoming_message" in request:
            for key in RUNNER_KEYS:
                request.pop(key, None)
            return request
    raise SystemExit("Không dựng lại được đầu vào: log thiếu nội dung (cần trace_content: true)")


def seed_previous_attempt(old_log: Path, new_dir: Path, role_name: str, run_id: str, work_id, round_number) -> bool:
    """Cho role nhận lại lượt lỗi trước của chính nó, như một lần thử lại: chép transcript cũ sang run mới."""
    folder = old_log.parent / "memory"
    best = None
    for path in folder.glob(f"{role_name.lower()}-r*.transcript.json") if folder.is_dir() else ():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if round_number is not None and data.get("round") != round_number:
            continue
        rank = (data.get("round", 0), path.stat().st_mtime)
        if best is None or rank > best[0]:
            best = (rank, path, data)
    if best is None:
        return False
    _, path, data = best
    target = new_dir / "memory"
    target.mkdir(parents=True, exist_ok=True)
    (target / path.name).write_text(
        json.dumps({**data, "run_id": run_id, "work_id": work_id}, ensure_ascii=False, indent=1), encoding="utf-8")
    return True


async def run(args) -> int:
    import yaml

    from raac.harness.layer1_lifecycle import AgentRunner
    from raac.harness.layer2_core.context_manager import RoleContextPolicy
    from raac.harness.layer2_core.memory.short_term import ShortTermMemoryPolicy
    from raac.harness.layer3_execution.local_executor import LocalExecutionPolicy, LocalExecutor
    from raac.harness.layer3_execution.tool_orchestrator import ToolOrchestrator
    from raac.harness.layer3_execution.tools.workspace import WorkspaceTools
    from raac.llm.client import LLMClient
    from raac.llm.config import load_dotenv, load_llm_config
    from raac.registry.loader import load_registry

    role_id = args.role.lower()
    request = rebuild_request(find_log(args.run), role_id, args.round)
    run_id = "chain-" + uuid.uuid4().hex
    request["run_id"] = run_id
    old_log = find_log(args.run)
    if args.dry_run:
        print(json.dumps({key: (value if len(json.dumps(value)) < 200 else "...") for key, value in request.items()},
                         ensure_ascii=False, indent=2))
        return 0

    load_dotenv(ROOT / ".env")
    llm_config = load_llm_config(ROOT / "configs/llm.yaml", model=None, base_url=None)
    harness = yaml.safe_load((ROOT / "configs/harness.yaml").read_text(encoding="utf-8-sig"))
    registry = load_registry(ROOT / "registry")
    policy = LocalExecutionPolicy.model_validate(harness["execution"])
    project_dir = Path(args.project_dir)
    workspace = WorkspaceTools((project_dir if project_dir.is_absolute() else ROOT / project_dir).resolve(),
                               LocalExecutor(policy))
    request["baseline_ref"] = workspace.baseline_ref
    output_dir = ROOT / "results" / run_id
    role_name = registry.roles[role_id].role
    seeded = (not args.no_previous) and seed_previous_attempt(
        old_log, output_dir, role_name, run_id, request.get("work_id"), request.get("round"))
    print("Có nạp lượt trước của role từ run cũ" if seeded else "Không nạp lượt trước (không có transcript)",
          file=sys.stderr, flush=True)
    client = LLMClient(llm_config, log_path=output_dir / "llm.jsonl", trace_content=harness["trace_content"])
    client.stream = not args.no_stream
    tools = ToolOrchestrator(workspace, timeout_seconds=policy.timeout_seconds + 15, trace=client.trace_event)
    runner = AgentRunner(
        registry, client, tools, provenance={}, token_limit=harness["token_limit"],
        context_policy=RoleContextPolicy.model_validate(harness.get("context", {})),
        memory_policy=ShortTermMemoryPolicy.model_validate(harness.get("memory", {})), auto_approve=True)
    print(f"Chạy lại role {args.role} từ {args.run} -> {run_id}", file=sys.stderr, flush=True)
    try:
        client.trace.emit({"run_id": run_id, "task_id": request["task_id"], "level": "L3"},
                          event="run_started", decision={"topology": "single_role", "role": args.role},
                          workspace={"repo_dir": str(workspace.root)})
        result = await runner(role_name, request)
        output = getattr(result, "output", result)
        print(output if isinstance(output, str) else json.dumps(output, ensure_ascii=False, indent=2))
        usage = client.usage.snapshot(run_id=run_id)
        print(f"\nlog: {output_dir / 'llm.jsonl'}\ntoken (do 9router báo, có thể thiếu phần cache): "
              f"vào {usage['prompt_tokens']}, ra {usage['completion_tokens']}, {usage['calls']} lượt", file=sys.stderr)
        return 0
    finally:
        await client.aclose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run", required=True, help="tên thư mục run (chain-...), thư mục hoặc đường dẫn llm.jsonl")
    parser.add_argument("--role", required=True, help="role id: analyst, planner, coder, tester, reviewer...")
    parser.add_argument("--round", type=int, default=None, help="round cần lấy đầu vào (mặc định: round cuối của role)")
    parser.add_argument("--project-dir", default="currentProject", help="thư mục chứa repo/ và requirement/")
    parser.add_argument("--no-stream", action="store_true", help="tắt streaming (số token log kém tin cậy hơn)")
    parser.add_argument("--no-previous", action="store_true", help="không nạp lượt lỗi trước của chính role (mặc định có nạp)")
    parser.add_argument("--dry-run", action="store_true", help="chỉ in đầu vào dựng lại, không gọi LLM")
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
