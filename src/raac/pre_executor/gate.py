"""Cổng duyệt giữa các flow.

- Một flow chỉ chạy khi mọi flow đứng trước đã approve VÀ file được duyệt chưa bị sửa sau đó (so sha256).
  clarify luôn chạy được.
- approve(flow): ghi sha256 của file được duyệt (artifacts.APPROVED_FILE) + thời điểm.
- invalidate_from(flow): chạy lại/revise một flow thì flow đó về nháp và các flow sau mất duyệt.
"""

import hashlib
from datetime import datetime, timezone

from .artifacts import APPROVED_FILE, FLOWS, Artifacts


class GateError(RuntimeError):
    pass


def _sha256(artifacts: Artifacts, flow: str) -> str | None:
    target = artifacts.path(APPROVED_FILE[flow])
    return hashlib.sha256(target.read_bytes()).hexdigest() if target.is_file() else None


def state(artifacts: Artifacts, flow: str) -> str:
    """missing | draft | approved | stale (đã duyệt nhưng file bị sửa sau đó)."""
    current = _sha256(artifacts, flow)
    if current is None:
        return "missing"
    approval = (artifacts.read_json("approvals.json") or {}).get(flow)
    if approval is None:
        return "draft"
    return "approved" if approval["sha256"] == current else "stale"


def can_run(artifacts: Artifacts, flow: str) -> bool:
    return all(state(artifacts, previous) == "approved" for previous in FLOWS[:FLOWS.index(flow)])


def require_runnable(artifacts: Artifacts, flow: str) -> None:
    for previous in FLOWS[:FLOWS.index(flow)]:
        if state(artifacts, previous) != "approved":
            raise GateError(f"Flow {flow} cần flow {previous} được approve trước (hiện: {state(artifacts, previous)})")


def approve(artifacts: Artifacts, flow: str) -> str:
    require_runnable(artifacts, flow)
    digest = _sha256(artifacts, flow)
    if digest is None:
        raise GateError(f"Flow {flow} chưa có bản nháp để approve")
    approvals = artifacts.read_json("approvals.json") or {}
    approvals[flow] = {"sha256": digest, "approved_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    artifacts.write_json("approvals.json", approvals)
    return digest


def invalidate_from(artifacts: Artifacts, flow: str) -> None:
    approvals = artifacts.read_json("approvals.json") or {}
    later = set(FLOWS[FLOWS.index(flow):])
    kept = {name: value for name, value in approvals.items() if name not in later}
    if kept != approvals:
        artifacts.write_json("approvals.json", kept)


def status(artifacts: Artifacts) -> dict[str, str]:
    return {flow: state(artifacts, flow) for flow in FLOWS}
