"""Persistent short-term action memory, scoped to one run/work/agent/baseline.

Compaction happens on writes that cross the configured threshold. Loading a
view never compacts or rewrites it. The append-only journal retains details.
"""

from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile

from langchain_core.messages import HumanMessage
from langchain_core.messages.utils import count_tokens_approximately
from pydantic import BaseModel, ConfigDict, Field, model_validator


class ShortTermMemoryPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    enabled: bool = True
    max_tokens: int = Field(default=6000, ge=1024, le=64000)
    target_tokens: int = Field(default=4000, ge=512, le=64000)

    @model_validator(mode="after")
    def valid_target(self):
        if self.target_tokens >= self.max_tokens:
            raise ValueError("memory.target_tokens must be smaller than memory.max_tokens")
        return self


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def estimated_tokens(entries):
    return count_tokens_approximately([HumanMessage(content=_json(entries))])


def _bounded(value, limit=512, width=16):
    if isinstance(value, str):
        if len(value) <= limit:
            return value
        return {"chars": len(value), "sha256": hashlib.sha256(value.encode()).hexdigest(),
                "preview": value[:limit // 2] + " ... " + value[-limit // 2:]}
    if isinstance(value, list):
        result = [_bounded(item, limit, width) for item in value[-width:]]
        return ([{"older_items_omitted": len(value) - width}] if len(value) > width else []) + result
    if isinstance(value, dict):
        items = list(value.items())
        result = {key: _bounded(item, limit, width) for key, item in items[:width]}
        if len(items) > width:
            result["fields_omitted"] = len(items) - width
        return result
    return value


def _failed(entry):
    if entry.get("kind") == "failure":
        return True
    data = entry.get("observation", {})
    if not isinstance(data, dict):
        return False
    result = data.get("result", {})
    return (data.get("ok") is False or (isinstance(result, dict) and
            (result.get("status") in {"failed", "error", "timeout"}
             or result.get("exit_code") not in (None, 0))))


def _summarize(summary, entry):
    result = deepcopy(summary)
    result["records"] = result.get("records", 0) + 1
    if entry.get("kind") == "tool":
        counts = Counter(result.get("tool_counts", {}))
        counts[entry.get("tool", "unknown")] += 1
        result["tool_counts"] = dict(counts)
        args = entry.get("arguments", {})
        if isinstance(args, dict) and entry.get("tool") in {"edit_file", "replace_text", "delete_file", "apply_patch"}:
            observation = entry.get("observation", {})
            data = observation.get("result", {}) if isinstance(observation, dict) else {}
            paths = ([args["path"]] if "path" in args else data.get("paths", []))
            paths = [*result.get("changed_files", []), *paths]
            labels = [path if isinstance(path, str) else str(path.get("preview", "<shortened path>"))
                      for path in paths if isinstance(path, (str, dict))]
            result["changed_files"] = list(dict.fromkeys(labels))[-20:]
    if _failed(entry):
        result["failures"] = [*result.get("failures", []), _bounded(entry, 128, 4)][-2:]
    result["through_sequence"] = entry["sequence"]
    return result


def action_receipt(entry):
    """Store concise observed receipts; the journal retains the complete entry."""
    result = {key: deepcopy(entry[key]) for key in ("sequence", "round", "kind", "tool", "tool_call_id") if key in entry}
    if entry.get("kind") == "tool":
        args = deepcopy(entry.get("arguments") or {})
        if isinstance(args, dict):
            for key in ("content", "patch", "old_text", "new_text"):
                if isinstance(args.get(key), str):
                    text = args.pop(key)
                    args[key + "_chars"] = len(text)
                    args[key + "_sha256"] = hashlib.sha256(text.encode()).hexdigest()
        result["arguments"] = _bounded(args, 120, 6)
        observation = entry.get("observation", {})
        if isinstance(observation, dict):
            observed = observation.get("result", {})
            brief = {key: _bounded(observation[key], 200, 4) for key in ("ok", "error_kind", "error") if key in observation}
            if isinstance(observed, dict):
                brief["result"] = {key: _bounded(observed[key], 200, 6) for key in
                    ("path", "status", "exit_code", "tests_run", "failed", "observation_ref", "summary", "sha256", "before_sha256", "paths") if key in observed}
                execution = observed.get("execution", {})
                if isinstance(execution, dict) and "exit_code" in execution:
                    brief["result"]["exit_code"] = execution["exit_code"]
                if isinstance(observed.get("content"), str):
                    brief["result"]["content_sha256"] = hashlib.sha256(observed["content"].encode()).hexdigest()
                for key in ("stdout", "stderr"):
                    if observed.get(key):
                        brief["result"][key] = _bounded(observed[key], 160, 4)
            result["observation"] = brief
    elif entry.get("kind") == "assistant":
        text = str(entry.get("content", ""))
        result.update(content_chars=len(text), content_sha256=hashlib.sha256(text.encode()).hexdigest(),
                      preview=text[:160])
    else:
        result.update({key: _bounded(value, 240, 6) for key, value in entry.items() if key not in result})
    result["detail_ref"] = "journal_sequence:" + str(entry["sequence"])
    return result


def compact_entries(entries, target):
    """Replace old detail with action counts/paths and retain recent outcomes/errors."""
    summary = {}
    work = []
    for entry in entries:
        if entry.get("kind") == "summary":
            summary = deepcopy(entry.get("data", {}))
        elif entry.get("kind") == "working_notes":
            work.append(deepcopy(entry))
        else:
            work.append(_bounded(entry))
    protected = set()
    for predicate in (lambda item: item.get("kind") == "working_notes", lambda item: item.get("kind") == "handoff", _failed):
        found = next((i for i in range(len(work) - 1, -1, -1) if predicate(work[i])), None)
        if found is not None:
            protected.add(work[found]["sequence"])

    def view():
        return ([{"kind": "summary", "sequence": summary.get("through_sequence", 0),
                  "data": summary}] if summary else []) + work

    while estimated_tokens(view()) > target:
        removable = next((i for i, item in enumerate(work) if item["sequence"] not in protected), None)
        if removable is None:
            break
        summary = _summarize(summary, work.pop(removable))
    result = view()
    for limit, width in ((256, 8), (128, 4), (64, 2)):
        if estimated_tokens(result) <= target:
            break
        # Keep the record envelope and all protected records. Only their data
        # gets shorter; slicing the outer list would lose protected failures.
        result = [deepcopy(entry) if entry.get("kind") == "working_notes" else
                  {key: (value if key in {"sequence", "round", "kind", "tool"}
                         else _bounded(value, limit, width)) for key, value in entry.items()}
                  for entry in result]
    return [item for item in result if item.get("kind") == "working_notes"] + [item for item in result if item.get("kind") != "working_notes"]


class ShortTermMemory:
    VERSION = "short-term-v2"

    def __init__(self, directory: Path, identity: dict, policy: ShortTermMemoryPolicy, *, redact):
        self.identity = deepcopy(identity)
        self.policy = policy
        self.redact = redact
        digest = hashlib.sha256(_json(identity).encode()).hexdigest()[:20]
        stem = "coder-" + digest
        self.path = Path(directory) / (stem + ".json")
        self.journal_path = Path(directory) / (stem + ".events.jsonl")
        self.entries = []
        self.revision = self.compactions = 0
        if self.path.exists():
            saved = json.loads(self.path.read_text(encoding="utf-8"))
            if saved.get("version") not in {"short-term-v1", self.VERSION} or saved.get("identity") != self.identity:
                raise ValueError("Memory identity/version mismatch")
            self.entries = saved["entries"]
            self.revision = saved["revision"]
            self.compactions = saved["compactions"]

    def view(self):
        """Return the stored representation unchanged; no write or compaction here."""
        return {"version": self.VERSION, "identity": deepcopy(self.identity),
                "revision": self.revision, "entries": deepcopy(self.entries)}

    def append(self, kind, *, round_number, snapshot_transform=None, **data):
        self.revision += 1
        entry = self.redact({"sequence": self.revision, "round": round_number, "kind": kind, **data})
        self.path.parent.mkdir(parents=True, exist_ok=True)
        stored_entry = snapshot_transform(entry) if snapshot_transform else deepcopy(entry)
        record = {"identity": self.identity, "entry": entry}
        if snapshot_transform:
            record["stored_entry"] = stored_entry
        with self.journal_path.open("a", encoding="utf-8") as stream:
            stream.write(_json(record) + "\n")
        if kind == "working_notes":
            self.entries = [stored_entry, *[item for item in self.entries if item.get("kind") != "working_notes"]]
        else:
            self.entries.append(stored_entry)
        before = estimated_tokens(self.entries)
        compacted = before > self.policy.max_tokens
        if compacted:
            self.entries = compact_entries(self.entries, self.policy.target_tokens)
            self.compactions += 1
        after = estimated_tokens(self.entries)
        document = {"version": self.VERSION, "identity": self.identity, "revision": self.revision,
                    "compactions": self.compactions, "estimated_tokens": after, "entries": self.entries}
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.path.parent,
                                             prefix=self.path.stem + "-", suffix=".tmp", delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(_json(document))
            temporary.replace(self.path)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()
        return {"compacted": compacted, "before_estimated_tokens": before,
                "after_estimated_tokens": after, "revision": self.revision,
                "compactions": self.compactions, "path": str(self.path), "entry": entry,
                "stored_entries": len(self.entries), "stored_entry": stored_entry}
