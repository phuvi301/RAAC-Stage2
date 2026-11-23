"""Lệnh dòng lệnh của Requirement Analyzer.

    python -m raac.ra analyze --workspace <repo> --task <task-id> [--model ...] [--observe terminal|web|none]
    python -m raac.ra show    --workspace <repo> [--task <task-id>]

Đầu vào lấy trong <workspace>/.raac/pre_executor/: tasks.json, requirement_spec.json, architecture.md.
Đầu ra: <workspace>/.raac/ra/requirement_profile.json (+ .md), mỗi task một mục; chấm lại thì ghi đè mục của task đó.
Log ghi vào cùng run của workspace với pre_executor (show-log --run-id <run_id> xem được cả chuỗi).
"""

import argparse
import asyncio
import sys
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

from raac.pre_executor.artifacts import Artifacts
from raac.pre_executor.cli import format_usage
from raac.pre_executor.flows.base import FlowError
from raac.pre_executor.runtime import PROJECT

from . import analyze as analyzer
from . import contracts


class RaConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str = "ra-v1"
    max_flow_retries: int = Field(default=2, ge=0)
    model: str = ""  # trống = model mặc định trong configs/llm.yaml


def load_config(path: Path = PROJECT / "configs/ra.yaml") -> RaConfig:
    path = Path(path)
    if not path.is_file():
        return RaConfig()
    return RaConfig.model_validate(yaml.safe_load(path.read_text(encoding="utf-8-sig")) or {})


class RaArtifacts(Artifacts):
    """Kết quả của ra nằm ở <workspace>/.raac/ra/, cạnh kết quả của pre_executor."""

    def __init__(self, workspace: Path):
        super().__init__(workspace)
        self.folder = self.workspace / ".raac" / "ra"


async def run_analyze(workspace: Path, task_id: str, *, model=None, observe="none", open_runtime=None,
                      write=print, rubric_path: Path = PROJECT / contracts.RUBRIC_FILE) -> dict:
    sources, store = Artifacts(workspace), RaArtifacts(workspace)
    config = load_config()
    contracts.register()
    if open_runtime is None:
        from raac.pre_executor.runtime import open_runtime
    extra = {}
    run_id = sources.run_id()
    if run_id:  # dùng chung run của workspace với pre_executor
        extra.update(run_id=run_id, usage_baseline=sources.usage_for_run(run_id))
    request_text = sources.request() or f"Analyze task {task_id}"
    runtime = await open_runtime(Path(workspace), request_text, "analyze", model=model or config.model or None, **extra)
    if getattr(runtime, "run_id", None):
        sources.set_run_id(runtime.run_id, "previous run was interrupted" if getattr(runtime, "replaced_run_id", None)
                           else "")
    observer = None
    if observe != "none" and getattr(runtime, "log_path", None) is not None:
        from raac.benchmark.observe import BenchmarkObserver

        observer = BenchmarkObserver(observe, label="ra analyze")
        observer.ready(runtime.log_path)
    status, usage = "failed", None
    try:
        result = await analyzer.run(sources, store, runtime, config, task_id, rubric_path=rubric_path)
        status = "completed"
    finally:
        snapshot = getattr(runtime, "usage", None)
        usage = snapshot() if callable(snapshot) else None
        await runtime.aclose(status=status)
        if observer is not None:
            if observe == "web":
                await observer.finish()
            observer.close()
        if usage and usage.get("calls"):
            store.add_usage("analyze", runtime.run_id, usage)
            sources.add_usage("analyze", runtime.run_id, usage)  # để token của run chung được tách đúng
    write(result["message"])
    if usage and usage.get("calls"):
        write(f"  Token: {format_usage(usage)}")
    write(f"  Log: python -m raac.mas_cli show-log --run-id {result['run_id']}")
    return result


def show(workspace: Path, task_id: str | None = None, write=print) -> None:
    sources, store = Artifacts(workspace), RaArtifacts(workspace)
    profiles = (store.read_json("requirement_profile.json") or {}).get("profiles", {})
    if task_id:
        if task_id not in profiles:
            raise ValueError(f"Task {task_id} chưa được chấm: chạy analyze --task {task_id}")
        profiles = {task_id: profiles[task_id]}
    if not profiles:
        write("Chưa có task nào được chấm.")
        return
    for tid, entry in sorted(profiles.items()):
        c, t, s, a = entry["profile"]
        flag = "  (đầu vào đã đổi: nên chấm lại)" if analyzer.stale(sources, entry, tid) else ""
        write(f"{tid}: C={c} T={t} S={s} A={a}{flag}")
    write(f"Chi tiết: {store.path('requirement_profile.md')}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m raac.ra", description="Requirement Analyzer (rubric C/T/S/A)")
    commands = parser.add_subparsers(dest="command", required=True)
    sub = commands.add_parser("analyze", help="chấm (C, T, S, A) cho một task")
    sub.add_argument("--workspace", required=True)
    sub.add_argument("--task", required=True, help="id task trong tasks.json, ví dụ T3")
    sub.add_argument("--model", help="model cho lần chạy này (ghi đè configs/ra.yaml)")
    sub.add_argument("--observe", choices=["terminal", "web", "none"], default="terminal")
    sub = commands.add_parser("show", help="xem profile đã chấm")
    sub.add_argument("--workspace", required=True)
    sub.add_argument("--task")
    return parser


def main(argv=None, *, open_runtime=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    try:
        if not Path(args.workspace).is_dir():
            raise ValueError(f"Workspace không tồn tại: {args.workspace}")
        if args.command == "analyze":
            asyncio.run(run_analyze(Path(args.workspace), args.task, model=args.model, observe=args.observe,
                                    open_runtime=open_runtime))
        else:
            show(Path(args.workspace), args.task)
    except (ValueError, FlowError, OSError) as exc:
        print(f"Lỗi: {exc}", file=sys.stderr)
        return 2
    except RuntimeError as exc:
        print(f"Lỗi khi chạy agent ({type(exc).__name__}: {exc}). Kiểm tra 9router/model rồi chạy lại.",
              file=sys.stderr)
        return 3
    except KeyboardInterrupt:
        return 130
    return 0
