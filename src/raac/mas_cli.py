"""Small runnable Chain demo. Benchmark/pilot commands are not implemented."""

import argparse
import asyncio
import hashlib
import json
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import yaml

from raac.benchmark.observe import BenchmarkObserver
from raac.collaboration.levels.l3_collaboration import (
    ChainLimits,
    ChainNodes,
    WorkItem,
    build_collaboration_graph,
    coordinate_chain,
    initial_chain_state,
)
from raac.common.provenance import collect_provenance
from raac.common.tracing import JSONLTrace
from raac.harness.layer1_lifecycle import AgentRunner
from raac.harness.layer2_core.context_manager import RoleContextPolicy
from raac.harness.layer2_core.memory.short_term import ShortTermMemoryPolicy
from raac.harness.layer3_execution.local_executor import (
    LocalExecutionPolicy,
    LocalExecutor,
)
from raac.harness.layer3_execution.tool_orchestrator import ToolOrchestrator
from raac.harness.layer3_execution.tools.workspace import WorkspaceTools
from raac.llm.client import LLMClient, LLMError
from raac.llm.config import load_dotenv, load_llm_config
from raac.llm.usage import UsageTracker
from raac.registry.loader import load_registry


class DemoError(RuntimeError):
    """Fixed actionable messages, without raw config/provider bodies."""
    

async def execute(args, *, approval_handler=None, return_record=False, event_handler=None, run_handler=None):
    #find framework RAAC root directory, load .env and configs, and prepare the MAS
    if args.command == "run-local":
        if not isinstance(args.prompt, str) or not args.prompt.strip() or len(args.prompt.encode("utf-8")) > WorkspaceTools.max_file_bytes:
            raise DemoError("Requirement must be nonempty UTF-8 text of at most 4 MiB")
        if not Path(args.workspace).is_dir():
            raise DemoError("Workspace must be an existing directory")
    project = Path(__file__).resolve().parents[2] 
    selected_level = getattr(args, "level", "L3") if args.command == "run-local" else "L3"
    if selected_level not in {"L1", "L2", "L3", "L4"}:
        raise DemoError("Local level must be L1, L2, L3 or L4")
    if selected_level == "L4":
        return await _mock_local(args, project, event_handler=event_handler, run_handler=run_handler)
    load_dotenv(project / ".env")
    llm_path = project / "configs/llm.yaml"
    llm_config = load_llm_config(llm_path, model=args.model, base_url=args.base_url)
    
    # Nếu lệnh là models Nó chỉ lấy danh sách model rồi kết thúc.
    if args.command in {"models", "probe-tools"}:
        client = LLMClient(llm_config, log_path=project / "results/model-check.jsonl")
        try:
            if args.command == "probe-tools":
                result = await client.probe_tools()
                print(json.dumps(result, ensure_ascii=False))
                return 0 if result["status"] == "passed" else 1
            print("\n".join(await client.list_models()))
        finally:
            await client.aclose()
        return 0

    # Nếu lệnh là run-chain, nó sẽ load các configs và chuẩn bị môi trường để chạy MAS.
    harness_path = project / "configs/harness.yaml"
    level_path = project / "configs/levels" / {
        "L1": "l1_fixed.yaml", "L2": "l2_routing.yaml", "L3": "l3_collaboration.yaml"}[selected_level]
    harness = yaml.safe_load(harness_path.read_text(encoding="utf-8-sig"))
    level = yaml.safe_load(level_path.read_text(encoding="utf-8-sig"))
    engine_config = level if selected_level != "L3" else None
    decision = coordinate_chain(level, max_rounds=args.max_rounds, topology=getattr(args, "topology", None),
                                max_review_rounds=getattr(args, "max_review_rounds", None),
                                hub_mode=getattr(args, "hub", None),
                                max_dispatches=getattr(args, "max_dispatches", None)) if selected_level == "L3" else None
    limits = ChainLimits(
        max_rounds=decision.max_rounds if decision else level["max_rounds"],
        hard_max_rounds=level["hard_max_rounds"],
        max_review_rounds=(decision.max_review_rounds or 0) if decision else 0,
        max_dispatches=(decision.max_dispatches or 12) if decision else 12,
        timeout_seconds=harness["timeout_seconds"],
        agent_timeout_seconds=harness["agent_timeout_seconds"],
    )
    policy = LocalExecutionPolicy.model_validate(harness["execution"])
    context_policy = RoleContextPolicy.model_validate(harness.get("context", {}))
    memory_policy = ShortTermMemoryPolicy.model_validate(harness.get("memory", {}))

    # Nạp registry và role models, chuẩn bị workspace và client LLM.
    registry = load_registry(project / "registry")
    try:
        role_models = llm_config.resolve_role_models({role.id: role.model.ref for role in registry.roles.values()})
    except ValueError as exc:
        raise DemoError(str(exc) + "; no task was sent") from None

    # Tao một run_id duy nhất cho lần chạy này, chuẩn bị thư mục output và executor.
    run_id = "chain-" + uuid.uuid4().hex
    local = args.command == "run-local"
    output_root = project / "results"
    output_dir = (output_root / run_id).resolve()
    # Thư mục luôn tên chain-*; in rõ level/topology thật để khỏi nhầm.
    shape = ""
    if decision is not None:  # chỉ L3 có decision
        hub = f" (hub {decision.hub_mode})" if decision.topology == "star" else ""
        shape = f", topology {decision.topology}{hub}, max_rounds {decision.max_rounds}"
    print(f"MAS run {run_id}: {selected_level}{shape}", file=sys.stderr, flush=True)

    # Tạo bộ chạy command và test local.
    executor = LocalExecutor(policy) 
    try:
        # thành phần thao tác repository của task.
        workspace = (WorkspaceTools(Path(args.workspace).resolve(), executor, requirement=args.prompt)
                     if local else WorkspaceTools(Path(args.project_dir).resolve(), executor))
    except FileNotFoundError:
        raise DemoError("Missing project input: --project-dir must contain a repo/ directory "
                        "and requirement/user_chat.txt. No previous result was loaded.") from None


    # Lưu trữ các thông tin cấu hình và trạng thái hiện tại của MAS để phục vụ cho việc thu thập provenance.
    effective = {"llm": llm_config.model_dump(),
                 "level": selected_level, "level_config": engine_config,
                 "role_models": {role_id: settings.model_dump() for role_id, settings in role_models.items()},
                 "chain_limits": limits.model_dump(),
                 "coordinator_decision": engine_config or decision.model_dump(),
                 "trace_content": harness["trace_content"],
                 "stream": getattr(args, "stream", True),
                 "execution": policy.model_dump(), "context": context_policy.model_dump(),
                 "token_limit": harness["token_limit"], "memory": memory_policy.model_dump()}
    provenance = collect_provenance(project, registry, [llm_path, harness_path, level_path], effective)
    provenance["task_snapshot"] = workspace.baseline_ref
    provenance["workspace"] = {"mode": "in_place", "project_dir": str(workspace.project),
                               "repo_dir": str(workspace.root),
                               "requirement_files": sorted(workspace.requirement_files)}

    # Tạo HTTP client, bộ đếm token và logger.
    client = LLMClient(llm_config, log_path=output_dir / "llm.jsonl")
    client.stream = getattr(args, "stream", True)
    client.trace.subscriber = event_handler
    executor.trace = client.trace
    if type(harness["trace_content"]) is not bool:
        await client.aclose()
        raise DemoError("trace_content in configs/harness.yaml must be true or false")
    client.trace_content = harness["trace_content"]
    if run_handler is not None:
        run_handler(client.trace.redact({"run_id": run_id, "log": str(client.log_path),
                                       "workspace": str(workspace.root), "level": selected_level,
                                       "topology": engine_config["topology"] if engine_config else decision.topology}))
    
    # Tạo bộ kiểm tra quyền và điều phối tool.
    tools = ToolOrchestrator(
        workspace, 
        timeout_seconds=policy.timeout_seconds + 15, 
        trace=client.trace_event # gửi log tool về client để ghi vào llm.jsonl
    )

    async def request_approval(request):
        # The UI receives this display copy through the post-redaction subscriber.
        client.trace.emit({"run_id": run_id, "agent_call_id": request["invocation_id"],
                           "role": request["role"]}, event="approval_requested", request=request)
        return await approval_handler(request)

    # Ghép registry → Layer 2 → LLM client → tool.
    runner = AgentRunner(
        registry, # Registry chứa các mô hình và công cụ
        client, # Client LLM để giao tiếp với mô hình ngôn ngữ
        tools, # thực thi tool
        provenance=provenance, # Thông tin tin đi kèm log
        token_limit=harness["token_limit"], # Giới hạn token cho mỗi lần gọi LLM
        context_policy=context_policy,  # quản lý ngữ cảnh cho các vai trò
        memory_policy=memory_policy,
        auto_approve=not local,
        approval_handler=request_approval if approval_handler is not None else None,
        approval_tools=frozenset({"run_command", "run_commands", "run_tests", "configure_environment", "provision_environment"}),
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

    # Theo dõi run (web/terminal) ngay khi log có; lỗi của viewer không bao giờ dừng run.
    observer = BenchmarkObserver(getattr(args, "observe", "none"), label="MAS run")
    try:
        # Dựng graph và thực sự chạy
        trace_context = {"run_id": run_id, "task_id": task_id, "level": selected_level}
        selected_decision = engine_config or decision.model_dump()
        client.trace.emit(trace_context, event="run_started", decision=selected_decision,
                           workspace=provenance["workspace"])
        observer.ready(output_dir / "llm.jsonl")

        if selected_level == "L3":
            graph = build_collaboration_graph(decision, ChainNodes(runner, budget_guard=runner.budget_guard))
            initial_state = initial_chain_state(work, limits, decision.topology, decision.hub_mode or "rules")
            initial_state["events"] = [{"node": "coordinator", "event": "topology_selected",
                                        "round": 0, "topology": decision.topology,
                                        "topology_version": decision.topology_version, "level": "L3",
                                        "run_id": run_id, "task_id": work.task_id,
                                        "work_id": work.work_id, "scope": work.scope,
                                        "decision": decision.model_dump()}]
            state = await graph.ainvoke(initial_state, {"recursion_limit": 6 * limits.max_rounds + 16})
        else:
            from raac.collaboration import Engine
            from raac.collaboration.levels.l1_fixed import L1Fixed
            from raac.collaboration.levels.l2_routing import L2Routing

            def routing_event(event):
                if event["event"] in {"routing_decided", "routing_fallback", "agent_skipped"}:
                    client.trace.emit(trace_context, **{k: v for k, v in event.items() if k not in trace_context})

            engine = Engine(registry=registry, runner=runner, logger=routing_event)
            result = await asyncio.wait_for(engine.run(run_id=run_id,
                task={"task_id": task_id, "requirement": workspace.requirement},
                level=L1Fixed() if selected_level == "L1" else L2Routing(), config=engine_config),
                timeout=limits.timeout_seconds)
            state = result.state
            if not state.get("result"):
                state["result"] = {"run_id": run_id, "status": "aborted", "error": state.get("error"),
                                   "stop_reason": (state.get("error") or {}).get("kind", "agent_failed")}
        # Ghi lại kết quả, trạng thái, provenance, quyết định của coordinator, thông tin thực thi và usage vào llm.jsonl
        record = {"error": None, "stop_reason": None, "verdict": None, **state["result"],
                  "level": selected_level, "events": state["events"], "provenance": provenance,
                  "coordinator_decision": selected_decision,
                  "execution": {"mode": "local", "repo_dir": str(workspace.root),
                                "python_executable": (workspace.environment.configuration or {}).get("python_executable", sys.executable),
                                "prepared_environment": workspace.environment.configuration},
                  "usage": client.usage.snapshot(run_id=run_id)}
        client.trace.emit(trace_context, event="run_completed", status=record["status"],
                           stop_reason=record["stop_reason"], error=record["error"], usage=record["usage"])
        if local:
            record["workspace"] = str(workspace.root)
            record["diff"] = workspace.current_patch()
        record["trace"] = {"content_enabled": client.trace_content, "file": "llm.jsonl",
                           "event_count": client.trace.sequence, "version": "1"}
        (output_dir / "result.json").write_text(
            json.dumps(client.trace.redact(record), indent=2, ensure_ascii=False), encoding="utf-8")
        if return_record:
            return record
        print(json.dumps({"run_id": run_id, "status": record["status"],
                          "stop_reason": record["stop_reason"], "output": str(output_dir),
                          "workspace": str(workspace.root)}, ensure_ascii=False))
        code = 0 if record["verdict"] == "PASS" else 1
        await observer.finish()  # web viewer stays open until Ctrl+C
        return code
    except asyncio.CancelledError:
        client.trace.emit(trace_context, event="run_cancelled", status="cancelled")
        raise
    except Exception:
        client.trace.emit(trace_context, event="run_failed", status="failed")
        raise
    finally:
        observer.close()
        await client.aclose()


async def terminal_approval(request):
    from raac.harness.layer1_lifecycle import ApprovalDecision
    print(json.dumps(request, ensure_ascii=False), file=sys.stderr, flush=True)
    try:
        approved = (await asyncio.to_thread(input, "Execute this operation? [y/N] ")).strip().lower() == "y"
    except EOFError:
        approved = False
    return ApprovalDecision(**{key: request[key] for key in
                              ("invocation_id", "revision", "operation_id", "arguments_hash")}, approve=approved)


async def run_local(workspace, requirement, topology="chain", approval_handler=None, *, level="L3", stream=False, event_handler=None, run_handler=None):
    """Run one local request. No handler means process execution is denied."""
    async def deny(request):
        from raac.harness.layer1_lifecycle import ApprovalDecision
        return ApprovalDecision(**{key: request[key] for key in
                                  ("invocation_id", "revision", "operation_id", "arguments_hash")}, approve=False)
    args = SimpleNamespace(command="run-local", workspace=str(workspace), prompt=requirement,
                           topology=topology, level=level, model=None, base_url=None, max_rounds=None, stream=stream)
    return await execute(args, approval_handler=approval_handler or deny, return_record=True,
                         event_handler=event_handler, run_handler=run_handler)


async def _mock_local(args, project, *, event_handler=None, run_handler=None):
    """Preview L4 without constructing a client, executor or editable workspace."""
    run_id = "chain-" + uuid.uuid4().hex
    root = project / "results"
    output = (root / run_id).resolve()
    workspace = Path(args.workspace).resolve()
    trace = JSONLTrace(output / "llm.jsonl", subscriber=event_handler)
    metadata = {"run_id": run_id, "level": "L4", "topology": "mock", "mock": True,
                "workspace": str(workspace), "log": str(trace.path)}
    if run_handler is not None:
        run_handler(dict(metadata))
    context = {"run_id": run_id, "level": "L4"}
    trace.emit(context, event="run_started", mock=True, decision={"topology": "mock"},
               workspace={"repo_dir": str(workspace)})
    trace.emit({**context, "agent_call_id": "l4-mock", "role": "L4 Mock"}, event="mock_output",
               text="L4 is not implemented. Mock preview only: no model, tools or workspace changes.")
    record = {**metadata, "status": "mock", "verdict": None, "stop_reason": "l4_not_implemented",
              "error": None, "diff": "", "execution": {"mode": "mock"},
              "usage": UsageTracker().snapshot(run_id=run_id),
              "provenance": collect_provenance(project, load_registry(project / "registry"), [],
                                               {"level": "L4", "mock": True})}
    trace.emit(context, event="run_completed", status="mock", mock=True, usage=record["usage"])
    record["trace"] = {"content_enabled": False, "file": "llm.jsonl", "event_count": trace.sequence, "version": "1"}
    (output / "result.json").write_text(json.dumps(trace.redact(record), ensure_ascii=False, indent=2), encoding="utf-8")
    return record


def main(argv=None) -> int:
    # Configure before argparse: --help also contains Vietnamese on Windows.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="RAAC repository Chain demo via 9router")
    parser.add_argument("command", choices=["models", "probe-tools", "run", "run-chain", "run-local", "show-log", "tui"],
                        help="run/run-chain: L3 trên --project-dir (repo/ + requirement/); "
                             "run-local: chạy trên --workspace với --prompt/--prompt-file, chọn --level")
    parser.add_argument("--level", choices=["L1", "L2", "L3", "L4"], default="L3",
                        help="run-local: level cần chạy (L4 hiện chỉ là bản xem thử); run/run-chain luôn là L3")
    parser.add_argument("--tui", action="store_true", help="show-log: read-only Textual viewer")
    parser.add_argument("--workspace", help="run-local: directory to edit directly")
    prompts = parser.add_mutually_exclusive_group()
    prompts.add_argument("--prompt")
    prompts.add_argument("--prompt-file")
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
    parser.add_argument("--max-review-rounds", type=int, default=None,
                        help="run-chain with chain_review_loop: Reviewer -> Planner feedback loops (default from config)")
    parser.add_argument("--hub", choices=["rules", "llm"], default=None,
                        help="run-chain with star: how the Hub picks the next role (default from config)")
    parser.add_argument("--max-dispatches", type=int, default=None,
                        help="run-chain with star: maximum Hub dispatches (default from config)")
    parser.add_argument("--topology", choices=["chain", "chain_review_loop", "star"], default=None,
                        help="run-chain: collaboration topology; default is configs/levels/l3_collaboration.yaml")
    parser.add_argument("--observe", choices=["web", "terminal", "none"], default="web",
                        help="run-chain: tự mở log của run này trên trình duyệt (web), in ra terminal, hoặc tắt (none)")
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
            if args.tui:
                return launch_tui(log=log)
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

    # Validate arguments for run / run-chain (cấu trúc project-dir, chỉ L3)
    if args.command in {"run", "run-chain"}:
        if not args.project_dir:
            parser.error(f"{args.command} requires --project-dir PATH (containing repo/ and requirement/user_chat.txt)")
        if args.level != "L3":
            print(f"{args.level} không chạy bằng {args.command}. Dùng: python -m raac.mas_cli run-local "
                  f"--workspace <thư mục code> --prompt-file <file> --level {args.level}", file=sys.stderr)
            return 2
    if args.command in {"run-local", "tui"}:
        if not args.workspace or (args.command == "run-local" and args.prompt is None and args.prompt_file is None):
            parser.error("run-local requires --workspace and exactly one of --prompt or --prompt-file")
        if args.prompt_file:
            path = Path(args.prompt_file)
            if not path.is_file() or path.stat().st_size > WorkspaceTools.max_file_bytes:
                parser.error("Prompt file is missing or exceeds 4 MiB")
            try:
                args.prompt = path.read_text(encoding="utf-8-sig")
            except (OSError, UnicodeError):
                parser.error("Prompt file must be readable UTF-8 text")
        if (args.command == "run-local" and not (args.prompt or "").strip()) or not Path(args.workspace).is_dir():
            parser.error("Prompt must not be empty and workspace must be an existing directory")
        args.topology = args.topology or "chain"
        if args.command == "tui":
            return launch_tui(workspace=Path(args.workspace).resolve(), prompt=args.prompt or "", topology=args.topology)
    try:
        return asyncio.run(execute(args, approval_handler=terminal_approval if args.command == "run-local" else None))
    except KeyboardInterrupt:
        return 130
    except (DemoError, LLMError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except (ValueError, OSError):
        # Do not echo arbitrary exception strings: config/provider failures can
        # contain credentials. Print fixed actionable guidance instead.
        print("Demo could not start. Check .env model/base URL, configs, task files, "
              "and the project's local runtime/dependencies.", file=sys.stderr)
        return 2


def launch_tui(**options):
    try:
        import textual  # noqa: F401 -- check the optional extra before its transitive imports

        from raac.tui.app import MASApp
    except ModuleNotFoundError as exc:
        if exc.name != "textual":
            raise
        print('Install the TUI extra: python -m pip install -e ".[tui]"', file=sys.stderr)
        return 2
    MASApp(**options).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
