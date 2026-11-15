"""Dựng hạ tầng MAS cho một lần chạy flow: registry, LLMClient, AgentRunner, ToolOrchestrator, trace.

Một workspace dùng MỘT run_id cho mọi flow và mọi lần góp ý (lưu ở .raac/pre_executor/run.json): log nối tiếp vào
cùng results/<run_id>/llm.jsonl. Lần đầu ghi run_started; mỗi lần chạy flow ghi flow_started/flow_completed (không ghi
run_completed để observe terminal của MAS không dừng giữa chừng). Nếu lần trước bị dừng giữa chừng (checkpoint chưa
xong), runner của MAS sẽ đòi resume; khi đó chuyển sang run_id mới (`replaced_run_id` ghi lý do).

Giống run-local của mas_cli: workspace là repo đích (WorkspaceTools(..., requirement=...)), log ghi vào
results/<run_id>/llm.jsonl nên xem được bằng `python -m raac.mas_cli show-log --run-id <run_id>`.
Lệnh shell của role (run_command/run_commands) cần approval_handler; không truyền thì bị từ chối.
"""

import hashlib
import uuid
from dataclasses import dataclass
from pathlib import Path

import yaml

from . import contracts

PROJECT = Path(__file__).resolve().parents[3]  # gốc RAAC: configs/, registry/, results/
APPROVAL_TOOLS = frozenset({"run_command", "run_commands", "run_tests", "configure_environment", "provision_environment"})


@dataclass
class FlowRuntime:
    """Một lần chạy flow: run_id riêng, chạy role bằng AgentRunner của harness."""
    run_id: str
    task_id: str
    log_path: Path
    runner: object
    client: object
    flow: str = ""
    usage_baseline: dict | None = None  # token đã có của run trước lần chạy này
    replaced_run_id: str | None = None  # run cũ bị bỏ vì dừng giữa chừng

    async def run(self, role_name: str, request: dict):
        return await self.runner(role_name, request)

    def usage(self) -> dict:
        """Token của RIÊNG lần chạy này (runner khôi phục tổng của run, nên trừ phần đã có)."""
        snapshot = dict(self.client.usage.snapshot(run_id=self.run_id))
        for key, value in (self.usage_baseline or {}).items():
            if isinstance(snapshot.get(key), int):
                snapshot[key] = max(0, snapshot[key] - value)
        return snapshot

    def note(self, event: str, **data) -> None:
        """Ghi một sự kiện của flow (vd. vi phạm luật, chạy lại) vào cùng trace để show-log/TUI thấy."""
        self.client.trace.emit({"run_id": self.run_id, "task_id": self.task_id, "level": "PRE"}, event=event, **data)

    async def aclose(self, *, status: str = "completed") -> None:
        try:
            self.client.trace.emit({"run_id": self.run_id, "task_id": self.task_id, "level": "PRE"},
                                   event="flow_completed", flow=self.flow, status=status, usage=self.usage())
        finally:
            await self.client.aclose()


async def _deny(request):
    from raac.harness.layer1_lifecycle import ApprovalDecision

    return ApprovalDecision(**{key: request[key] for key in
                               ("invocation_id", "revision", "operation_id", "arguments_hash")}, approve=False)


async def open_runtime(workspace: Path, request_text: str, flow: str, *, model: str | None = None,
                       approval_handler=None, stream: bool = True, event_handler=None,
                       run_id: str | None = None, usage_baseline: dict | None = None,
                       project: Path = PROJECT, registry_dir: Path | None = None, results_dir: Path | None = None,
                       llm_config=None, transport=None) -> FlowRuntime:
    """Các tham số sau dấu * (trừ approval_handler/stream) chỉ để test thay hạ tầng thật."""
    from raac.common.provenance import collect_provenance
    from raac.harness.layer1_lifecycle import AgentRunner
    from raac.harness.layer1_lifecycle.checkpoint import CheckpointStore
    from raac.harness.layer2_core.context_manager import RoleContextPolicy
    from raac.harness.layer2_core.memory.short_term import ShortTermMemoryPolicy
    from raac.harness.layer3_execution.local_executor import LocalExecutionPolicy, LocalExecutor
    from raac.harness.layer3_execution.tool_orchestrator import ToolOrchestrator
    from raac.harness.layer3_execution.tools.workspace import WorkspaceTools
    from raac.llm.client import LLMClient
    from raac.llm.config import load_dotenv, load_llm_config
    from raac.registry.loader import load_registry

    contracts.register()
    harness_path = project / "configs/harness.yaml"
    llm_path = project / "configs/llm.yaml"
    harness = yaml.safe_load(harness_path.read_text(encoding="utf-8-sig"))
    if llm_config is None:
        load_dotenv(project / ".env")
        llm_config = load_llm_config(llm_path, model=model, base_url=None)  # chỉ đổi model mặc định của lần chạy này
    registry = load_registry(registry_dir or project / "registry")
    policy = LocalExecutionPolicy.model_validate(harness["execution"])
    context_policy = RoleContextPolicy.model_validate(harness.get("context", {}))
    memory_policy = ShortTermMemoryPolicy.model_validate(harness.get("memory", {}))

    executor = LocalExecutor(policy)
    workspace_tools = WorkspaceTools(Path(workspace).resolve(), executor, requirement=request_text)
    results = results_dir or project / "results"
    replaced = None
    if run_id and (results / run_id / "lifecycle.sqlite3").is_file():
        previous = CheckpointStore(results / run_id / "lifecycle.sqlite3").latest_run(run_id)
        if previous and (previous.get("inflight") or previous["status"] not in {"COMPLETED", "FAILED"}):
            replaced, run_id = run_id, None  # lần trước dừng giữa chừng: không resume, mở run mới
    if not run_id:
        run_id = "chain-" + uuid.uuid4().hex  # cùng dạng tên với run MAS để show-log/TUI đọc được
        usage_baseline = None
    task_id = "task-" + hashlib.sha256(request_text.encode("utf-8")).hexdigest()[:16]
    log_path = results / run_id / "llm.jsonl"
    continuing = log_path.is_file()
    effective = {"llm": llm_config.model_dump(), "level": "PRE", "flow": flow, "stream": stream, "model": model,
                 "execution": policy.model_dump(), "context": context_policy.model_dump(),
                 "memory": memory_policy.model_dump(), "token_limit": harness["token_limit"]}
    provenance = collect_provenance(project, registry, [llm_path, harness_path, project / "configs/pre_executor.yaml"]
                                    if (project / "configs/pre_executor.yaml").is_file() else [llm_path, harness_path],
                                    effective)
    provenance["workspace"] = {"mode": "in_place", "repo_dir": str(workspace_tools.root)}

    client = LLMClient(llm_config, log_path=log_path, transport=transport, trace_content=harness["trace_content"],
                       stream=stream)
    client.trace.subscriber = event_handler  # TUI nhận từng sự kiện ngay khi agent làm (như TUI của MAS)
    executor.trace = client.trace
    tools = ToolOrchestrator(workspace_tools, timeout_seconds=policy.timeout_seconds + 15, trace=client.trace_event)
    runner = AgentRunner(registry, client, tools, provenance=provenance, token_limit=harness["token_limit"],
                         context_policy=context_policy, memory_policy=memory_policy, auto_approve=False,
                         approval_handler=approval_handler or _deny, approval_tools=APPROVAL_TOOLS)
    context = {"run_id": run_id, "task_id": task_id, "level": "PRE"}
    if continuing:
        runner._restore_trace_sequence()  # nối tiếp số thứ tự sự kiện của log đã có
    else:
        client.trace.emit(context, event="run_started", decision={"topology": "pre_executor"},
                          workspace=provenance["workspace"])
    client.trace.emit(context, event="flow_started", flow=flow, replaced_run_id=replaced)
    return FlowRuntime(run_id=run_id, task_id=task_id, log_path=log_path, runner=runner, client=client, flow=flow,
                       usage_baseline=usage_baseline, replaced_run_id=replaced)
