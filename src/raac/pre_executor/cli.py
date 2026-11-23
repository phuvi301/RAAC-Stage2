"""Lệnh dòng lệnh của pre_executor. Đầu vào giống TUI: repo đích + yêu cầu (nhập trực tiếp hoặc file).

    python -m raac.pre_executor tui     --workspace <repo>     # mỗi câu hỏi một cửa sổ, trả lời ngay
    python -m raac.pre_executor clarify --workspace <repo> --prompt "..." | --prompt-file request.md
    python -m raac.pre_executor status  --workspace <repo>
    python -m raac.pre_executor revise | approve | review <flow> ...   # các flow sau clarify (góp ý/duyệt)

Yêu cầu chỉ cần ở lần đầu (lưu ở .raac/pre_executor/request.md).
clarify chỉ hỏi và trả lời: ở dòng lệnh, sửa answers.md rồi chạy lại `clarify` để agent hỏi vòng tiếp; khi agent thấy
đủ (hoặc hết số vòng) clarify tự chốt. requirements, profile, solution_architect, project_manager có góp ý (revise) và
approve. Cả 5 flow đã triển khai; project_manager đã duyệt thì tasks.json là đầu vào của vòng lặp MAS.
"""

import argparse
import asyncio
import sys
from pathlib import Path

from . import gate
from .artifacts import FLOWS, Artifacts
from .config import load_config
from .flows import clarify, profile, project_manager, requirements, solution_architect
from .flows.base import FlowError
from .runtime import PROJECT

IMPLEMENTED = {"clarify": clarify, "requirements": requirements, "profile": profile,
               "solution_architect": solution_architect, "project_manager": project_manager}


def format_usage(usage: dict) -> str:
    """Như MAS: số token do provider (9router) báo; có thể thiếu phần cache."""
    text = f"token vào {usage.get('prompt_tokens', 0):,} / ra {usage.get('completion_tokens', 0):,}"
    if usage.get("calls") is not None:
        text += f", {usage['calls']} lượt gọi model"
    if usage.get("cached_tokens"):
        text += f", cache {usage['cached_tokens']:,}"
    return text


def _request_text(args) -> str | None:
    if getattr(args, "prompt_file", None):
        return Path(args.prompt_file).read_text(encoding="utf-8-sig")
    return getattr(args, "prompt", None)


def _prepare(args, *, require_request: bool = True) -> Artifacts:
    workspace = Path(args.workspace)
    if not workspace.is_dir():
        raise ValueError(f"Workspace không tồn tại: {workspace}")
    artifacts = Artifacts(workspace)
    text = _request_text(args)
    stored = artifacts.request()
    if text and text.strip():
        if stored and stored != text.strip():
            raise ValueError("Workspace này đã có yêu cầu khác (.raac/pre_executor/request.md); "
                             "dùng workspace khác hoặc xoá thư mục đó để bắt đầu lại")
        if not stored:
            artifacts.save_request(text)
    elif not stored and require_request:
        raise ValueError("Chưa có yêu cầu: truyền --prompt hoặc --prompt-file ở lần chạy đầu")
    return artifacts


async def _run_flow(flow: str, artifacts: Artifacts, *, note=None, open_runtime=None, write=print,
                    model: str | None = None, observe: str = "none", event_handler=None) -> dict:
    module = IMPLEMENTED.get(flow)
    if module is None:
        raise ValueError(f"Flow {flow} chưa triển khai")
    gate.require_runnable(artifacts, flow)
    config = load_config(PROJECT / "configs/pre_executor.yaml")
    if open_runtime is None:
        from .runtime import open_runtime
    extra = {"event_handler": event_handler} if event_handler is not None else {}
    run_id = artifacts.run_id()
    if run_id:  # cả workspace dùng chung một run: log nối tiếp, show-log xem được cả chuỗi
        extra.update(run_id=run_id, usage_baseline=artifacts.usage_for_run(run_id))
    runtime = await open_runtime(artifacts.workspace, artifacts.request(), flow, model=config.model_for(flow, model),
                                 **extra)
    if getattr(runtime, "run_id", None):
        replaced = getattr(runtime, "replaced_run_id", None)
        artifacts.set_run_id(runtime.run_id, "previous run was interrupted" if replaced else "")
        if replaced:
            write(f"  Run {replaced} bị dừng giữa chừng lần trước; chuyển sang run mới {runtime.run_id}")
    observer = None
    if observe != "none" and getattr(runtime, "log_path", None) is not None:
        from raac.benchmark.observe import BenchmarkObserver  # viewer của MAS: terminal hoặc web

        observer = BenchmarkObserver(observe, label=f"pre_executor {flow}")
        observer.ready(runtime.log_path)
    status, usage = "failed", None
    try:
        result = await module.run(artifacts, runtime, config, note=note)
        status = "completed"
    finally:
        snapshot = getattr(runtime, "usage", None)
        usage = snapshot() if callable(snapshot) else None
        await runtime.aclose(status=status)
        if observer is not None:
            if observe == "web":
                await observer.finish()  # như MAS: viewer vẫn mở tới khi Ctrl+C
            observer.close()
        if usage and usage.get("calls"):
            artifacts.add_usage(flow, runtime.run_id, usage)
            if status != "completed":
                write(f"{flow}: {format_usage(usage)} (lần chạy lỗi)")
    if usage:
        result["usage"] = usage
    if result.get("message"):
        write(result["message"])
    elif result.get("status") == "done":
        write(f"{flow}: xong sau {result['round']} vòng ({result['reason']}): {result['answered']} câu trả lời, "
              f"{result['assumed']} câu dùng khuyến nghị (giả định). Kết quả: {result['clarification']}")
        if result.get("coverage"):
            write("  Độ phủ: " + ", ".join(f"{status} {count}" for status, count in result["coverage"].items())
                  + f" (chi tiết: {artifacts.path('coverage.json')})")
    else:
        write(f"{flow}: vòng {result['round']}, {result['questions']} câu hỏi. Trả lời tại: {result['answers']}")
        write(f"  Trả lời xong thì chạy lại `clarify --workspace ...` để agent hỏi tiếp.")
    if usage and usage.get("calls"):
        total = artifacts.usage_total()
        write(f"  Token lần này: {format_usage(usage)} · cộng dồn workspace: {format_usage(total)}")
    if result.get("run_id"):
        write(f"  Log: python -m raac.mas_cli show-log --run-id {result['run_id']}")
    return result


NO_REVIEW = {"clarify"}  # chỉ hỏi và trả lời, tự chốt khi xong


def _reviewable(flow: str) -> None:
    if flow in NO_REVIEW:
        raise ValueError(f"{flow} chỉ hỏi và trả lời, không có góp ý/approve: chạy `{flow}` (hoặc `tui`) để trả lời tiếp")


def _approve(flow: str, artifacts: Artifacts, write=print) -> None:
    _reviewable(flow)
    gate.approve(artifacts, flow)
    write(f"{flow}: đã duyệt")


def _status(artifacts: Artifacts, write=print) -> None:
    usage = artifacts.usage_by_flow()
    for flow, value in gate.status(artifacts).items():
        note = "" if flow in IMPLEMENTED else "  (chưa triển khai)"
        feedback = len(artifacts.feedback_history(flow))
        tokens = f"  {format_usage(usage[flow])}" if flow in usage else ""
        write(f"{flow:<18} {value:<9}{f'  {feedback} góp ý' if feedback else ''}{tokens}{note}")
    if usage:
        write(f"Tổng: {format_usage(artifacts.usage_total())}")


async def review(flow: str, artifacts: Artifacts, *, read=input, write=print, open_runtime=None, model=None) -> None:
    """Hỏi đáp: in bản nháp, rồi lặp: nhập góp ý để agent chỉnh | a = approve | q = thoát."""
    _reviewable(flow)
    if gate.state(artifacts, flow) == "missing":
        await _run_flow(flow, artifacts, open_runtime=open_runtime, write=write, model=model)
    while True:
        write(artifacts.read_text(gate.APPROVED_FILE[flow]) or "")
        answer = read("Góp ý cho agent (hoặc sửa file rồi gõ 'a' để approve, 'q' để thoát): ").strip()
        if answer.lower() == "q":
            return
        if answer.lower() == "a":
            _approve(flow, artifacts, write)
            return
        if answer:
            await _run_flow(flow, artifacts, note=answer, open_runtime=open_runtime, write=write, model=model)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m raac.pre_executor", description="Các flow trước khi chạy MAS")
    commands = parser.add_subparsers(dest="command", required=True)

    def common(sub):
        sub.add_argument("--workspace", required=True, help="repo đích (trống = dự án mới)")
        group = sub.add_mutually_exclusive_group()
        group.add_argument("--prompt", help="yêu cầu nhập trực tiếp")
        group.add_argument("--prompt-file", help="đường dẫn file yêu cầu (vd. request.md)")
        sub.add_argument("--model", help="model cho lần chạy này (ghi đè models trong configs/pre_executor.yaml)")
        sub.add_argument("--observe", choices=["terminal", "web", "none"], default="terminal",
                         help="theo dõi agent khi chạy, như mas_cli run (mặc định terminal)")

    for flow in FLOWS:
        common(commands.add_parser(flow, help=f"chạy flow {flow}"))
    for name, help_text in (("revise", "góp ý để agent chỉnh lại bản nháp"), ("approve", "duyệt bản nháp"),
                            ("review", "hỏi đáp: góp ý / approve")):
        sub = commands.add_parser(name, help=help_text)
        sub.add_argument("flow", choices=FLOWS)
        common(sub)
        if name == "revise":
            sub.add_argument("--note", required=True, help="góp ý cho agent")
    common(commands.add_parser("status", help="trạng thái các flow"))
    common(commands.add_parser("tui", help="giao diện: mỗi câu hỏi một cửa sổ, trả lời ngay"))
    return parser


def main(argv=None, *, open_runtime=None) -> int:
    for stream in (sys.stdout, sys.stderr):  # như mas_cli: in được tiếng Việt trên console Windows
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    try:
        artifacts = _prepare(args, require_request=args.command != "tui")
        if args.command == "tui":
            from .tui import run_tui  # nạp muộn: CLI không cần textual

            run_tui(artifacts, model=args.model)
        elif args.command in FLOWS:
            asyncio.run(_run_flow(args.command, artifacts, open_runtime=open_runtime, model=args.model,
                                  observe=args.observe))
        elif args.command == "revise":
            _reviewable(args.flow)
            if len(args.note.strip()) < 3:
                raise ValueError("Góp ý quá ngắn (ít nhất 3 ký tự)")
            asyncio.run(_run_flow(args.flow, artifacts, note=args.note, open_runtime=open_runtime, model=args.model,
                                  observe=args.observe))
        elif args.command == "approve":
            _approve(args.flow, artifacts)
        elif args.command == "review":
            asyncio.run(review(args.flow, artifacts, open_runtime=open_runtime, model=args.model))
        else:
            _status(artifacts)
    except (ValueError, gate.GateError, FlowError, OSError) as exc:
        print(f"Lỗi: {exc}", file=sys.stderr)
        return 2
    except RuntimeError as exc:  # lỗi khi gọi model/agent (vd. 9router tắt): báo gọn, không in traceback
        print(f"Lỗi khi chạy agent ({type(exc).__name__}: {exc}). Kiểm tra 9router/model rồi chạy lại; "
              "kết quả các bước trước vẫn được giữ.", file=sys.stderr)
        return 3
    except KeyboardInterrupt:
        return 130
    return 0
