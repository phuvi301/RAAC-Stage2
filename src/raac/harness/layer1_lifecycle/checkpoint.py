"""Versioned JSON snapshots; intent and outcome writes are separate transactions."""

import hashlib
import json
import re
import sqlite3
import time
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from raac.harness.layer3_execution.sandbox.docker_sandbox import cache_lock

MAX_CHECKPOINT_BYTES = 256 * 1024 * 1024


class InvocationState(BaseModel):
    """Closed, versioned persisted shape; runtime objects never cross this boundary."""

    model_config = ConfigDict(extra="forbid", strict=True)
    schema_version: Literal[1]
    invocation_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    run_id: str = Field(min_length=1)
    revision: int = Field(ge=1)
    context: dict[str, Any]
    request: dict[str, Any]
    status: Literal[
        "IDLE",
        "PLANNING",
        "EXECUTING_TOOL",
        "WAITING_HUMAN_APPROVAL",
        "FAILED",
        "COMPLETED",
    ]
    phase: Literal["reason", "decide", "act", "observe", "finalize", "done"]
    turn: int = Field(ge=0)
    output_recoveries: int = Field(default=0, ge=0, le=1)
    completion_error: Literal["empty_completion", "truncated_completion"] | None = None
    messages: list[dict[str, Any]]
    code_edits_before: int = Field(ge=0)
    environment_revision: int = Field(ge=0)
    compatibility: str
    deadline_at: float | int | None = Field(allow_inf_nan=False)
    inflight: dict[str, Any] | None
    receipts: dict[str, Any]
    resumable: bool
    pending_approval: dict[str, Any] | None
    approved_operation: dict[str, Any] | None
    runtime: dict[str, Any]
    usage: list[dict[str, Any]]
    memory: dict[str, Any] | None
    message: dict[str, Any] | None = None
    call_context: dict[str, Any] | None = None
    calls: list[dict[str, Any]] = Field(default_factory=list, max_length=4)
    tool_index: int = Field(default=0, ge=0)
    observation: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    error: str | None = None

    @model_validator(mode="after")
    def phase_payload(self):
        if (
            self.phase in {"decide", "act", "observe", "finalize"}
            and not self.call_context
        ):
            raise ValueError("Missing call context")
        if self.phase in {"act", "observe"} and self.tool_index >= len(self.calls):
            raise ValueError("Missing pending tool")
        if self.status == "COMPLETED" and (self.phase != "done" or not self.result):
            raise ValueError("Missing completed result")
        return self


class LifecycleError(ValueError):
    """Fixed failure codes, never provider bodies or checkpoint content."""


def digest(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()
    ).hexdigest()


class CheckpointStore:
    def __init__(self, path: Path):
        self.path = Path(path)

    @contextmanager
    def _connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=0)
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute(
            "CREATE TABLE IF NOT EXISTS invocations (id TEXT PRIMARY KEY, revision INTEGER NOT NULL, payload TEXT NOT NULL)"
        )
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def require_latest(self, state):
        with self._connect() as db:
            rows = db.execute(
                "SELECT id,payload FROM invocations ORDER BY rowid DESC"
            ).fetchall()
        for identifier, payload in rows:
            if json.loads(payload).get("run_id") == state["run_id"]:
                if identifier != state["invocation_id"]:
                    raise LifecycleError("resume_superseded")
                return

    def latest_run(self, run_id):
        if not self.path.exists():
            return None
        with self._connect() as db:
            rows = db.execute(
                "SELECT payload FROM invocations ORDER BY rowid DESC"
            ).fetchall()
        return next(
            (
                state
                for (payload,) in rows
                if (state := json.loads(payload)).get("run_id") == run_id
            ),
            None,
        )

    @staticmethod
    def _id(value):
        if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{32}", value):
            raise LifecycleError("invalid_invocation_id")

    @contextmanager
    def lock(self, run_id):
        suffix = hashlib.sha256(run_id.encode()).hexdigest()[:24]
        with ExitStack() as stack:
            try:
                stack.enter_context(
                    cache_lock(
                        self.path.parent / ("lifecycle-" + suffix + ".lock"), timeout=0
                    )
                )
            except TimeoutError:
                raise LifecycleError("lifecycle_busy") from None
            yield

    def save(self, state: dict) -> dict:
        started = time.perf_counter()
        self._id(state["invocation_id"])
        updated = dict(state)
        updated["schema_version"] = 1
        updated["revision"] = state["revision"] + 1
        payload = json.dumps(updated, ensure_ascii=False, allow_nan=False)
        payload_bytes = len(payload.encode())
        if payload_bytes > MAX_CHECKPOINT_BYTES:
            raise LifecycleError("checkpoint_too_large")
        serialized = time.perf_counter()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT revision FROM invocations WHERE id=?", (state["invocation_id"],)
            ).fetchone()
            if (row[0] if row else 0) != state["revision"]:
                raise LifecycleError("stale_checkpoint")
            db.execute(
                "INSERT OR REPLACE INTO invocations VALUES (?,?,?)",
                (state["invocation_id"], updated["revision"], payload),
            )
        state["revision"] = updated["revision"]
        return {"payload_bytes": payload_bytes, "serialize_ms": round((serialized - started) * 1000, 3),
                "sqlite_ms": round((time.perf_counter() - serialized) * 1000, 3)}

    def load(self, invocation_id: str) -> dict:
        self._id(invocation_id)
        try:
            with self._connect() as db:
                row = db.execute(
                    "SELECT payload FROM invocations WHERE id=?", (invocation_id,)
                ).fetchone()
            if not row or len(row[0].encode()) > MAX_CHECKPOINT_BYTES:
                raise LifecycleError("checkpoint_missing_or_too_large")
            state = json.loads(row[0])
            if (
                state.get("schema_version") != 1
                or state.get("invocation_id") != invocation_id
            ):
                raise LifecycleError("checkpoint_identity_or_version")
            if "phase" in state:
                InvocationState.model_validate(state)
            return state
        except (
            sqlite3.Error,
            json.JSONDecodeError,
            TypeError,
            AttributeError,
            ValidationError,
        ):
            raise LifecycleError("checkpoint_corrupt") from None
