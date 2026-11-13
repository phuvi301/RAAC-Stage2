"""Command line: one command per phase, with approval points between them.

    python -m swflow new todo --request "Xây ứng dụng quản lý công việc ..."
    python -m swflow clarify todo         # writes runs/todo/answers.md; edit the answers
    python -m swflow spec todo            # writes spec.md
    python -m swflow approve todo spec
    python -m swflow architect todo       # writes architecture.md
    python -m swflow approve todo architecture
    python -m swflow env todo             # writes environment.md (prefers what is installed on this machine)
    python -m swflow setup todo           # shows install commands for what is missing; --apply runs them
    python -m swflow approve todo environment
    python -m swflow plan todo            # writes tasks.md (validated task graph)
    python -m swflow status todo
    python -m swflow auto todo            # every phase with default answers, no approval stops
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from . import phases
from .models import Spec, TaskGraph
from .store import Store, dumps
from .task_graph import problems, ready_tasks, topological_order

FLOW_ROOT = Path(__file__).resolve().parents[1]
GATES = {"architect": "spec", "env": "architecture", "plan": "environment"}  # phase -> approval it needs first
ARTIFACT_FILES = {"spec": "spec", "architecture": "architecture", "environment": "environment", "tasks": "tasks"}


def project_repo(project_dir: Path) -> Path | None:
    """The code of a MAS-style project: repo/ itself, or its only sub-folder (repo/<name>/)."""
    repo = project_dir / "repo"
    if not repo.is_dir():
        return None
    children = [p for p in repo.iterdir() if p.is_dir() and not p.name.startswith(".")]
    files = [p for p in repo.iterdir() if p.is_file()]
    return children[0] if len(children) == 1 and not files else repo


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="swflow", description="Software flow before the MAS")
    parser.add_argument("--runs-dir", default=str(FLOW_ROOT / "runs"), help="where project folders are kept")
    parser.add_argument("--model", default=None, help="override the default model of configs/llm.yaml")
    sub = parser.add_subparsers(dest="command", required=True)
    new = sub.add_parser("new", help="start a project from a request")
    new.add_argument("name")
    source = new.add_mutually_exclusive_group(required=True)
    source.add_argument("--request", help="the request text")
    source.add_argument("--request-file", help="a file with the request")
    source.add_argument("--from-project", help="a MAS project folder: requirement/user_chat.txt and repo/")
    new.add_argument("--repo", help="an existing codebase to take into account (read-only)")
    for name in ("clarify", "spec", "architect", "env", "plan", "status", "auto"):
        command = sub.add_parser(name)
        command.add_argument("name")
        if name in GATES:
            command.add_argument("--force", action="store_true", help="skip the approval check")
    approve = sub.add_parser("approve", help="approve an artifact after reading its .md")
    approve.add_argument("name")
    approve.add_argument("artifact", choices=list(ARTIFACT_FILES))
    setup = sub.add_parser("setup", help="install what environment.md marks as missing")
    setup.add_argument("name")
    setup.add_argument("--apply", action="store_true", help="run the install commands (otherwise only print them)")
    export = sub.add_parser("export", help="write one MAS request per task and a script that runs them in order")
    export.add_argument("name")
    export.add_argument("--mas-project", required=True, help="MAS project folder (gets requirement/ and repo/<name>/)")
    export.add_argument("--repo-name", default=None, help="folder under repo/ for the code (default: project name)")
    export.add_argument("--level", default="L3", choices=["L1", "L2", "L3"])
    export.add_argument("--topology", default=None, choices=["chain", "chain_review_loop", "star"])
    return parser


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    store = Store(Path(args.runs_dir) / args.name)
    if args.command == "new":
        return new_project(store, args)
    if not store.exists("request.md"):
        print(f"Không có project '{args.name}'. Tạo bằng: python -m swflow new {args.name} --request \"...\"")
        return 2
    if args.command == "status":
        return status(store)
    if args.command == "export":
        return export(store, args)
    if args.command == "setup":
        return setup(store, args)
    if args.command == "approve":
        state = store.state()
        if not store.exists(f"{ARTIFACT_FILES[args.artifact]}.json"):
            print(f"Chưa có {args.artifact} để duyệt.")
            return 2
        state.approved[args.artifact] = True
        store.save_state(state)
        print(f"Đã duyệt {args.artifact}.")
        return 0
    return asyncio.run(run_phases(store, args))


def new_project(store: Store, args) -> int:
    if store.exists("request.md"):
        print(f"Project đã tồn tại: {store.folder}")
        return 2
    repo = Path(args.repo).resolve() if args.repo else None
    if args.from_project:
        project = Path(args.from_project).resolve()
        request = (project / "requirement" / "user_chat.txt").read_text(encoding="utf-8")
        repo = repo or project_repo(project)
    elif args.request_file:
        request = Path(args.request_file).read_text(encoding="utf-8")
    else:
        request = args.request
    store.write_text("request.md", request.strip() + "\n")
    state = store.state()
    state.repo = str(repo) if repo else None
    store.save_state(state)
    print(f"Đã tạo {store.folder}" + (f" (codebase: {repo})" if repo else " (dự án mới)"))
    print(f"Tiếp theo: python -m swflow clarify {args.name}")
    return 0


async def run_phases(store: Store, args) -> int:
    from .llm_raac import RaacModel  # imported late so `new`, `status` and `approve` work without raac

    state = store.state()
    repo = Path(state.repo) if state.repo else None
    wanted = ["clarify", "spec", "architect", "env", "plan"] if args.command == "auto" else [args.command]
    if args.command != "auto":
        gate = GATES.get(args.command)
        if gate and not state.approved.get(gate) and not args.force:
            print(f"Cần duyệt {gate} trước: đọc {store.path(gate + '.md')}, rồi chạy "
                  f"python -m swflow approve {state.project} {gate} (hoặc thêm --force).")
            return 2
    model = RaacModel(log_path=store.path("llm.jsonl"), model=args.model)
    try:
        for phase in wanted:
            print(f"… {phase}", flush=True)
            if phase == "clarify":
                result = await phases.clarify(store, model, repo=repo)
                state.clarify_rounds += 1
                print(f"{len(result.questions)} câu hỏi. Sửa câu trả lời trong {store.path('answers.md')}")
            elif phase == "spec":
                spec = await phases.specify(store, model, repo=repo)
                functional = sum(r.kind == "functional" for r in spec.requirements)
                print(f"{functional} yêu cầu chức năng, {len(spec.requirements) - functional} phi chức năng: "
                      f"{store.path('spec.md')}")
            elif phase == "architect":
                architecture = await phases.architect(store, model, repo=repo)
                print(f"{len(architecture.decisions)} quyết định: {store.path('architecture.md')}")
            elif phase == "env":
                chosen = await phases.environment(store, model, repo=repo)
                missing = [r for r in chosen.runtimes if r.source == "install"]
                print(f"{len(chosen.runtimes)} runtime, {len(missing)} cần cài: {store.path('environment.md')}")
                if missing:
                    print(f"Xem lệnh cài: python -m swflow setup {state.project}")
            else:
                graph = await phases.plan(store, model, repo=repo)
                state.task_status = {task.id: "pending" for task in graph.tasks}
                print(f"{len(graph.tasks)} task, thứ tự: {' → '.join(topological_order(graph))}: "
                      f"{store.path('tasks.md')}")
            state.phase = {"spec": "specify", "env": "environment"}.get(phase, phase)
            if phase == "plan":
                state.phase = "done"
            store.save_state(state)
    except phases.PhaseError as error:
        print(f"Lỗi: {error}")
        return 1
    except Exception as error:  # noqa: BLE001 -- model/gateway failures from raac, reported without a traceback
        if type(error).__name__ not in {"LLMError", "RunGuardError", "CompletionFormatError"}:
            raise
        print(f"Không gọi được model ({type(error).__name__}: {error}). Kiểm tra 9router đang chạy và .env, "
              f"rồi chạy lại lệnh; các bước đã xong được giữ nguyên. Log: {store.path('llm.jsonl')}")
        return 1
    finally:
        usage = model.usage()
        print(f"Token: {usage.get('prompt_tokens')} vào / {usage.get('completion_tokens')} ra, "
              f"{usage.get('calls')} lần gọi")
        await model.aclose()
    return 0


def setup(store: Store, args) -> int:
    """Print, or with --apply run, the install commands of the runtimes that are not on this machine yet."""
    import os
    import subprocess

    from .machine import probe
    from .models import EnvironmentPlan

    if not store.exists("environment.json"):
        print(f"Chưa có môi trường. Chạy: python -m swflow env {store.folder.name}")
        return 2
    plan = store.load_model("environment.json", EnvironmentPlan)
    missing = [runtime for runtime in plan.runtimes if runtime.source == "install"]
    if not missing:
        print("Máy đã có đủ runtime cần thiết; không cần cài gì.")
        return 0
    for runtime in missing:
        print(f"- {runtime.name} {runtime.version}: {runtime.install_command}  ({runtime.reason})")
    if not args.apply:
        print("Chưa cài gì. Chạy lại với --apply để cài bằng các lệnh trên.")
        return 0
    shell = ["powershell", "-NoProfile", "-Command"] if os.name == "nt" else ["sh", "-c"]
    failed = 0
    for runtime in missing:
        print(f"… cài {runtime.name} {runtime.version}", flush=True)
        if subprocess.run([*shell, runtime.install_command]).returncode != 0:
            failed += 1
            print(f"Cài {runtime.name} thất bại.")
    inventory = probe()
    store.write_text("machine.json", dumps(inventory) + "\n")
    print("Sau khi cài, chạy lại để cập nhật đường dẫn và đánh dấu là đã có: "
          f"python -m swflow env {store.folder.name} --force")
    return 1 if failed else 0


def _ps(text: str) -> str:
    """A PowerShell single-quoted literal."""
    return "'" + text.replace("'", "''") + "'"


RUN_SCRIPT = """# Sinh bởi: python -m swflow export {project}. Chạy MAS lần lượt cho từng task theo thứ tự phụ thuộc.
#   powershell -ExecutionPolicy Bypass -File {script}              chạy hết, dừng ở task đầu tiên không PASS
#   powershell -ExecutionPolicy Bypass -File {script} -From T3     chạy tiếp từ T3
#   powershell -ExecutionPolicy Bypass -File {script} -Only T3     chỉ chạy T3
# Mỗi task PASS được commit vào repo, để task sau bắt đầu từ code đã có.
param([string]$From = "", [string]$Only = "")
$Mas = {mas}
$Repo = Join-Path $Mas {repo_rel}
$Here = $PSScriptRoot
New-Item -ItemType Directory -Force (Join-Path $Mas "requirement") | Out-Null
New-Item -ItemType Directory -Force $Repo | Out-Null
if (-not (Test-Path (Join-Path $Repo ".gitignore"))) {{
    Set-Content -Encoding utf8 (Join-Path $Repo ".gitignore") ".venv*/`nvenv/`n__pycache__/`nnode_modules/`n*.pyc"
}}
if (-not (Test-Path (Join-Path $Repo ".git"))) {{ git -C $Repo init -q }}
$Tasks = @(
{tasks}
)
$Running = ($From -eq "")
foreach ($Task in $Tasks) {{
    if ($Only -and $Task.Id -ne $Only) {{ continue }}
    if (-not $Running) {{ if ($Task.Id -eq $From) {{ $Running = $true }} else {{ continue }} }}
    Write-Host "===== $($Task.Id): $($Task.Title)"
    Copy-Item (Join-Path $Here "$($Task.Id).user_chat.txt") (Join-Path $Mas "requirement\\user_chat.txt") -Force
    python -m raac.mas_cli run --level {level}{topology} --project-dir $Mas --observe none
    if ($LASTEXITCODE -ne 0) {{
        Write-Host "$($Task.Id) không PASS (mã $LASTEXITCODE). Dừng ở đây. Xem kết quả rồi chạy lại: -From $($Task.Id)"
        exit 1
    }}
    git -C $Repo add -A
    git -C $Repo commit -q --allow-empty -m "$($Task.Id): $($Task.Title)"
    Write-Host "$($Task.Id) PASS, đã commit."
}}
Write-Host "Xong."
"""


def export(store: Store, args) -> int:
    from .handoff import task_request
    from .models import Architecture, EnvironmentPlan

    missing = [name for name in ("spec.json", "architecture.json", "tasks.json") if not store.exists(name)]
    if missing:
        print("Chưa đủ để xuất: thiếu " + ", ".join(missing) + ". Chạy spec / architect / plan trước.")
        return 2
    spec = store.load_model("spec.json", Spec)
    architecture = store.load_model("architecture.json", Architecture)
    environment = store.load_model("environment.json", EnvironmentPlan) if store.exists("environment.json") else None
    graph = store.load_model("tasks.json", TaskGraph)
    issues = problems(graph, spec)
    if issues:
        print("Kế hoạch chưa hợp lệ, không xuất: " + "; ".join(issues))
        return 2
    order = topological_order(graph)
    by_id = {task.id: task for task in graph.tasks}
    folder = store.path("mas")
    folder.mkdir(parents=True, exist_ok=True)
    request = store.request()
    for task_id in order:
        text = task_request(request=request, spec=spec, architecture=architecture, graph=graph, task=by_id[task_id],
                            environment=environment)
        (folder / f"{task_id}.user_chat.txt").write_text(text, encoding="utf-8")
    repo_name = args.repo_name or store.folder.name
    tasks = ",\n".join(f"    @{{Id={_ps(t)}; Title={_ps(by_id[t].title)}}}" for t in order)
    script = RUN_SCRIPT.format(project=store.folder.name, script=str(folder / "run_tasks.ps1"),
                               mas=_ps(str(Path(args.mas_project).resolve())), repo_rel=_ps(f"repo\\{repo_name}"),
                               tasks=tasks, level=args.level,
                               topology=f" --topology {args.topology}" if args.topology else "")
    # PowerShell 5.1 reads a script without a BOM as the ANSI code page; the BOM keeps Vietnamese text intact.
    (folder / "run_tasks.ps1").write_text(script, encoding="utf-8-sig")
    print(f"Đã xuất {len(order)} task vào {folder}")
    print("Thứ tự: " + " → ".join(order))
    print(f"Chạy: powershell -ExecutionPolicy Bypass -File \"{folder / 'run_tasks.ps1'}\"  (thêm -Only T1 để thử 1 task)")
    return 0


def status(store: Store) -> int:
    state = store.state()
    print(f"Project: {state.project}  |  bước: {state.phase}  |  codebase: {state.repo or '(dự án mới)'}")
    for name in ("answers.md", "spec.md", "architecture.md", "environment.md", "tasks.md"):
        key = name.removesuffix(".md")
        mark = "đã duyệt" if state.approved.get(key) else ("có" if store.exists(name) else "chưa có")
        print(f"  {name:18} {mark}")
    if store.exists("tasks.json"):
        graph = store.load_model("tasks.json", TaskGraph)
        spec = store.load_model("spec.json", Spec) if store.exists("spec.json") else None
        issues = problems(graph, spec)
        print("  Kế hoạch: " + ("hợp lệ" if not issues else "CHƯA hợp lệ: " + "; ".join(issues)))
        if not issues:
            print("  Task sẵn sàng: " + (", ".join(ready_tasks(graph, state.task_status)) or "-"))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
