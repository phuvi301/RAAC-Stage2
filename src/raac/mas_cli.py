"""Small runnable Chain demo. Benchmark/pilot commands are not implemented."""

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import sys
import uuid

import yaml

from raac.collaboration.levels.l3_collaboration import (
    ChainLimits, ChainNodes, WorkItem, build_collaboration_graph, coordinate_chain, initial_chain_state,
)
from raac.common.provenance import collect_provenance
from raac.harness.demo_agent import DemoAgentRunner
from raac.harness.layer2_core.context_manager import RoleContextPolicy
from raac.harness.layer2_core.memory.short_term import ShortTermMemoryPolicy
from raac.harness.layer3_execution.local_executor import LocalExecutor, LocalExecutionPolicy
from raac.harness.layer3_execution.tool_orchestrator import ToolOrchestrator
from raac.harness.layer3_execution.tools.workspace import WorkspaceTools
from raac.llm.client import LLMClient, LLMError
from raac.llm.config import load_dotenv, load_llm_config
from raac.registry.loader import load_registry


class DemoError(RuntimeError):
    """Fixed actionable messages, without raw config/provider bodies."""
    

async def execute(args) -> int:
    #find framework RAAC root directory, load .env and configs, and prepare the MAS
    project = Path(__file__).resolve().parents[2] 
    load_dotenv(project / ".env")
    llm_path = project / "configs/llm.yaml"
    llm_config = load_llm_config(llm_path, model=args.model, base_url=args.base_url)
    
    # Nếu lệnh là models Nó chỉ lấy danh sách model rồi kết thúc.
    if args.command == "models":
        client = LLMClient(llm_config, log_path=project / "results/model-check.jsonl")
        try:
            print("\n".join(await client.list_models()))
        finally:
            await client.aclose()
        return 0

    # Nếu lệnh là run-chain, nó sẽ load các configs và chuẩn bị môi trường để chạy MAS.
    harness_path = project / "configs/harness.yaml"
    level_path = project / "configs/levels/l3_collaboration.yaml"
    harness = yaml.safe_load(harness_path.read_text(encoding="utf-8-sig"))
    level = yaml.safe_load(level_path.read_text(encoding="utf-8-sig"))
    decision = coordinate_chain(level, max_rounds=args.max_rounds)
    limits = ChainLimits(
        max_rounds=decision.max_rounds,
        hard_max_rounds=level["hard_max_rounds"],
        timeout_seconds=harness["timeout_seconds"],
        agent_timeout_seconds=harness["agent_timeout_seconds"],
    )
    policy = LocalExecutionPolicy.model_validate(harness["execution"])
    context_policy = RoleContextPolicy.model_validate(harness.get("context", {}))
    memory_policy = ShortTermMemoryPolicy.model_validate(harness.get("memory", {}))

    # Nạp registry và role models, chuẩn bị workspace và client LLM.
    registry = load_registry(project / "registry")
    registry.get("Environment")  # Required by Chain topology v3, before workspace or HTTP.
    try:
        role_models = llm_config.resolve_role_models({role.id: role.model.ref for role in registry.roles.values()})
    except ValueError as exc:
        raise DemoError(str(exc) + "; no task was sent") from None

    # Tao một run_id duy nhất cho lần chạy này, chuẩn bị thư mục output và executor.
    run_id = "chain-" + uuid.uuid4().hex
    output_dir = (project / "results" / run_id).resolve()

    # Tạo bộ chạy command và test local.
    executor = LocalExecutor(policy) 
    try:
        # thành phần thao tác repository của task.
        workspace = WorkspaceTools(Path(args.project_dir).resolve(), executor)
    except FileNotFoundError:
        raise DemoError("Missing project input: --project-dir must contain a repo/ directory "
                        "and requirement/user_chat.txt. No previous result was loaded.") from None


    # Lưu trữ các thông tin cấu hình và trạng thái hiện tại của MAS để phục vụ cho việc thu thập provenance.
    effective = {"llm": llm_config.model_dump(),
                 "role_models": {role_id: settings.model_dump() for role_id, settings in role_models.items()},
                 "chain_limits": limits.model_dump(),
                 "coordinator_decision": decision.model_dump(),
                 "trace_content": harness["trace_content"],
                 "execution": policy.model_dump(), "context": context_policy.model_dump(),
                 "token_limit": harness["token_limit"], "memory": memory_policy.model_dump()}
    provenance = collect_provenance(project, registry, [llm_path, harness_path, level_path], effective)
    provenance["task_snapshot"] = workspace.baseline_ref
    provenance["workspace"] = {"mode": "in_place", "project_dir": str(workspace.project),
                               "repo_dir": str(workspace.root),
                               "requirement_files": sorted(workspace.requirement_files)}

    # Tạo HTTP client, bộ đếm token và logger.
    client = LLMClient(llm_config, log_path=output_dir / "llm.jsonl")
    if type(harness["trace_content"]) is not bool:
        await client.aclose()
        raise DemoError("trace_content in configs/harness.yaml must be true or false")
    client.trace_content = harness["trace_content"]
    
    # Tạo bộ kiểm tra quyền và điều phối tool.
    tools = ToolOrchestrator(
        workspace, 
        timeout_seconds=policy.timeout_seconds + 15, 
        trace=client.trace_event # gửi log tool về client để ghi vào llm.jsonl
    )

    # Ghép registry → Layer 2 → LLM client → tool.
    runner = DemoAgentRunner(
        registry, # Registry chứa các mô hình và công cụ
        client, # Client LLM để giao tiếp với mô hình ngôn ngữ
        tools, # thực thi tool
        provenance=provenance, # Thông tin tin đi kèm log
        token_limit=harness["token_limit"], # Giới hạn token cho mỗi lần gọi LLM
        context_policy=context_policy,  # quản lý ngữ cảnh cho các vai trò
        memory_policy=memory_policy
    )

    # Tạo đầu vào của Chain: ID, yêu cầu và baseline.
    task_id = "task-" + hashlib.sha256(workspace.requirement.encode("utf-8")).hexdigest()[:16]
    work = WorkItem(
        run_id=run_id, 
        task_id=task_id, 
        work_id=task_id,            
        scope="task", 
        requirement=workspace.requirement, 
        baseline_ref=workspace.baseline_ref
    )

    try:
        # Dựng graph và thực sự chạy
        trace_context = {"run_id": run_id, "task_id": task_id, "level": "L3"}
        client.trace_event(trace_context, "run_started", decision=decision.model_dump(),
                           workspace=provenance["workspace"])

        graph = build_collaboration_graph(
            decision, 
            ChainNodes(runner, budget_guard=runner.budget_guard)
        )

        initial_state = initial_chain_state(work, limits)
        initial_state["events"] = [{"node": "coordinator", "event": "topology_selected",
                                    "round": 0, "topology": decision.topology,
                                    "topology_version": decision.topology_version, "level": "L3",
                                    "run_id": run_id, "task_id": work.task_id,
                                    "work_id": work.work_id, "scope": work.scope,
                                    "decision": decision.model_dump()}]

        # Thực hiện graph và thu thập kết quả
        state = await graph.ainvoke(initial_state, {
            "recursion_limit": 6 * limits.max_rounds + 16,
        })
        # Ghi lại kết quả, trạng thái, provenance, quyết định của coordinator, thông tin thực thi và usage vào llm.jsonl
        record = {**state["result"], "events": state["events"], "provenance": provenance,
                  "coordinator_decision": decision.model_dump(),
                  "execution": {"mode": "local", "repo_dir": str(workspace.root),
                                "python_executable": (workspace.environment.configuration or {}).get("python_executable", sys.executable),
                                "prepared_environment": workspace.environment.configuration},
                  "usage": client.usage.snapshot(run_id=run_id)}
        client.trace_event(trace_context, "run_completed", status=record["status"],
                           stop_reason=record["stop_reason"], error=record["error"], usage=record["usage"])
        record["trace"] = {"content_enabled": client.trace_content, "file": "llm.jsonl",
                           "event_count": client.trace.sequence, "version": "1"}
        (output_dir / "result.json").write_text(
            json.dumps(client.trace.redact(record), indent=2, ensure_ascii=False), encoding="utf-8")
        print(json.dumps({"run_id": run_id, "status": record["status"],
                          "stop_reason": record["stop_reason"], "output": str(output_dir),
                          "workspace": str(workspace.root)}, ensure_ascii=False))
        return 0 if record["verdict"] == "PASS" else 1
    finally:
        await client.aclose()


def main(argv=None) -> int:
    # Configure before argparse: --help also contains Vietnamese on Windows.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="RAAC repository Chain demo via 9router")
    parser.add_argument("command", choices=["models", "run-chain", "show-log"])
    parser.add_argument("--run-id", help="show-log: ID run cần đọc")
    parser.add_argument("--log", help="show-log: đường dẫn llm.jsonl hoặc file JSONL được dán")
    parser.add_argument("--details", action="store_true", help="show-log: mở nội dung trao đổi/tool")
    parser.add_argument("--memory", action="store_true", help="show-log terminal: xem thêm snapshot và nhật ký memory Coder")
    parser.add_argument("--agent", help="show-log: lọc một role, ví dụ coder")
    parser.add_argument("--follow", action="store_true", help="show-log: theo dõi tới khi run kết thúc")
    parser.add_argument("--web", action="store_true", help="show-log: xem luồng trao đổi trên trình duyệt, tự cập nhật")
    parser.add_argument("--port", type=int, default=8765, help="show-log --web: cổng local, 0 để tự chọn")
    parser.add_argument("--no-open", action="store_true", help="show-log --web: chỉ in URL, không tự mở trình duyệt")
    parser.add_argument("--project-dir", "--task-dir", dest="project_dir",
                        help="Required for run-chain: project containing repo/ and requirement/user_chat.txt")
    parser.add_argument("--model", default=None, help="Override the default model; named role profiles stay unchanged")
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--max-rounds", type=int, default=None)
    args = parser.parse_args(argv)

    # Validate arguments for show-log command
    if args.command == "show-log":
        if bool(args.run_id) == bool(args.log):
            parser.error("show-log requires exactly one of --run-id ID or --log PATH")
        if args.run_id and (Path(args.run_id).name != args.run_id or not args.run_id.startswith(("chain-", "benchmark-"))):
            parser.error("--run-id must be a chain-* or benchmark-* run ID, not a path")
        if not 0 <= args.port <= 65535:
            parser.error("--port must be between 0 and 65535")
        from raac.common.trace_viewer import show_trace
        log = Path(args.log) if args.log else Path(__file__).resolve().parents[2] / "results" / args.run_id / "llm.jsonl"
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
        try:
            if args.web:
                from raac.common.trace_web import serve_trace
                serve_trace(log, port=args.port, agent=args.agent, open_browser=not args.no_open,
                            emit=lambda line: print(line, flush=True))
            else:
                show_trace(log, details=args.details, agent=args.agent, follow=args.follow, memory=args.memory,
                           emit=lambda line: print(line, flush=True))
        except KeyboardInterrupt:
            return 130
        except OSError:
            print("Cannot read log; check --run-id or --log path.", file=sys.stderr)
            return 2
        return 0

    # Validate arguments for run-chain command
    if args.command == "run-chain" and not args.project_dir:
        parser.error("run-chain requires --project-dir PATH (containing repo/ and requirement/user_chat.txt)")
    try:
        return asyncio.run(execute(args))
    except (DemoError, LLMError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except (ValueError, OSError):
        # Do not echo arbitrary exception strings: config/provider failures can
        # contain credentials. Print fixed actionable guidance instead.
        print("Demo could not start. Check .env model/base URL, configs, task files, "
              "and the project's local runtime/dependencies.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
