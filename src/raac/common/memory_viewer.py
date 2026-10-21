"""Read a selected run's memory snapshots/journals without following log-supplied paths."""

import json
from copy import deepcopy
from pathlib import Path, PureWindowsPath
import re

from .tracing import JSONLTrace


MEMORY_ID = re.compile(r"coder-[0-9a-f]{20}")
MAX_MEMORY_BYTES = 2 * 1024 * 1024
MAX_JOURNAL_LINE_BYTES = 16 * 1024 * 1024


class MemoryFeed:
    def __init__(self, log_path):
        self.log_path = Path(log_path).resolve()
        self.root = self.log_path.parent
        self.directory = self.root / "memory"
        self.redactor = JSONLTrace(self.log_path)

    def _run_id(self):
        with self.log_path.open("rb") as stream:
            for _ in range(50):
                line = stream.readline(MAX_JOURNAL_LINE_BYTES + 1)
                if not line:
                    break
                try:
                    item = json.loads(line)
                except (ValueError, RecursionError):
                    continue
                if isinstance(item, dict) and isinstance(item.get("run_id"), str):
                    return item["run_id"]
        return None

    def _path(self, identifier, suffix=".json"):
        if not isinstance(identifier, str) or not MEMORY_ID.fullmatch(identifier):
            raise ValueError("Invalid memory ID")
        if self.directory.resolve().parent != self.root:
            raise ValueError("Memory directory is outside the selected run")
        target = self.directory / (identifier + suffix)
        if target.resolve().parent != self.directory.resolve():
            raise ValueError("Memory file is outside the selected run")
        return target

    def _snapshot(self, identifier, run_id):
        path = self._path(identifier)
        if path.stat().st_size > MAX_MEMORY_BYTES:
            raise ValueError("Memory snapshot exceeds viewer limit")
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        if (not isinstance(data, dict) or not isinstance(data.get("identity"), dict)
                or not isinstance(data.get("entries"), list)):
            raise ValueError("Invalid memory snapshot")
        if run_id is not None and data["identity"].get("run_id") != run_id:
            raise ValueError("Memory belongs to another run")
        return data

    def snapshots(self):
        result, warnings = [], []
        if not self.directory.exists():
            return {"memories": result, "warnings": warnings}
        if self.directory.resolve().parent != self.root:
            return {"memories": result, "warnings": ["Bỏ qua memory nằm ngoài thư mục run."]}
        run_id = self._run_id()
        for path in sorted(self.directory.glob("coder-*.json"))[:100]:
            if not MEMORY_ID.fullmatch(path.stem):
                continue
            try:
                data = self._snapshot(path.stem, run_id)
                journal = self._path(path.stem, ".events.jsonl")
                result.append(self.redactor.redact({**data, "id": path.stem,
                              "snapshot_file": "memory/" + path.name,
                              "journal_available": journal.is_file()}))
            except (ValueError, OSError, RecursionError):
                warnings.append("Bỏ qua memory không hợp lệ hoặc không thuộc run đang xem.")
        return {"memories": result, "warnings": list(dict.fromkeys(warnings))}

    def journal(self, identifier, cursor=0):
        if type(cursor) is not int or cursor < 0:
            raise ValueError("Invalid memory cursor")
        snapshot = self._snapshot(identifier, self._run_id())
        path = self._path(identifier, ".events.jsonl")
        size = path.stat().st_size
        reset = cursor > size
        cursor = 0 if reset else cursor
        entries, warnings = [], []
        with path.open("rb") as stream:
            stream.seek(cursor)
            for _ in range(50):
                start = stream.tell()
                line = stream.readline(MAX_JOURNAL_LINE_BYTES + 1)
                if not line:
                    break
                if len(line) > MAX_JOURNAL_LINE_BYTES:
                    while line and not line.endswith(b"\n"):
                        line = stream.readline(MAX_JOURNAL_LINE_BYTES)
                    cursor = stream.tell()
                    warnings.append("Bỏ qua bản ghi memory quá 16 MiB.")
                    continue
                if not line.endswith(b"\n"):
                    break
                try:
                    record = json.loads(line.decode("utf-8-sig"))
                    if (not isinstance(record, dict) or record.get("identity") != snapshot["identity"]
                            or not isinstance(record.get("entry"), dict)):
                        raise ValueError("Memory journal scope mismatch")
                    entries.append(self.redactor.redact(record["entry"]))
                except (ValueError, RecursionError):
                    warnings.append(f"Bỏ qua bản ghi memory không hợp lệ tại byte {start}.")
                cursor = stream.tell()
        return {"id": identifier, "entries": entries, "cursor": cursor,
                "has_more": cursor < size and bool(line and line.endswith(b"\n")),
                "reset": reset, "warnings": warnings}

    def _records(self, path, warnings):
        """Read complete records only; never execute or follow paths in records."""
        with path.open("rb") as stream:
            while True:
                line = stream.readline(MAX_JOURNAL_LINE_BYTES + 1)
                if not line:
                    return
                if len(line) > MAX_JOURNAL_LINE_BYTES:
                    while line and not line.endswith(b"\n"):
                        line = stream.readline(MAX_JOURNAL_LINE_BYTES)
                    warnings.append("Bỏ qua bản ghi quá lớn khi đọc lịch sử memory.")
                    continue
                if not line.endswith(b"\n"):
                    return
                try:
                    record = json.loads(line)
                    if isinstance(record, dict):
                        yield record
                except (ValueError, RecursionError):
                    warnings.append("Bỏ qua bản ghi lỗi khi đọc lịch sử memory.")

    def _revision_data(self, identifier):
        snapshot = self._snapshot(identifier, self._run_id())
        if type(snapshot.get("revision")) is not int or not 0 <= snapshot["revision"] <= 100000:
            raise ValueError("Invalid memory revision")
        identity, warnings = snapshot["identity"], []
        entries, saved, compacted = {}, {}, {}
        for record in self._records(self._path(identifier, ".events.jsonl"), warnings):
            entry = record.get("entry")
            if record.get("identity") != identity or not isinstance(entry, dict):
                warnings.append("Bỏ qua journal khác phạm vi memory.")
                continue
            revision = entry.get("sequence")
            if type(revision) is int and 1 <= revision <= snapshot["revision"]:
                stored = record.get("stored_entry", entry)
                entries[revision] = stored if isinstance(stored, dict) and stored.get("sequence") == revision else entry
        for event in self._records(self.log_path, warnings):
            if event.get("run_id") != identity.get("run_id"):
                continue
            if event.get("event") == "memory_saved":
                # Match only the basename; no log-provided path is ever opened.
                if PureWindowsPath(str(event.get("path", ""))).name != identifier + ".json":
                    continue
                revision = event.get("revision")
                if type(revision) is int:
                    stored = event.get("stored_entry")
                    if isinstance(stored, dict) and stored.get("sequence") == revision and revision in entries:
                        entries[revision] = stored
                    saved[revision] = {key: event[key] for key in
                        ("revision", "compacted", "compactions", "before_estimated_tokens",
                         "after_estimated_tokens", "stored_entries", "timestamp") if key in event}
            elif event.get("event") == "memory_compacted":
                memory = event.get("memory", {})
                if isinstance(memory, dict) and memory.get("identity") == identity and isinstance(memory.get("entries"), list):
                    revision = memory.get("revision")
                    if type(revision) is int:
                        compacted[revision] = memory["entries"]
        return snapshot, entries, saved, compacted, warnings

    def history(self, identifier, cursor=0):
        if type(cursor) is not int or cursor < 0:
            raise ValueError("Invalid history cursor")
        snapshot, entries, saved, compacted, warnings = self._revision_data(identifier)
        revisions = sorted(set(entries) | {snapshot["revision"]}, reverse=True)
        rows = []
        for revision in revisions[cursor:cursor + 50]:
            entry, metadata = entries.get(revision, {}), saved.get(revision, {})
            rows.append({**metadata, "revision": revision, "kind": entry.get("kind"),
                         "round": entry.get("round"), "tool": entry.get("tool"),
                         "compacted": metadata.get("compacted", revision in compacted)})
        return self.redactor.redact({"id": identifier, "revisions": rows, "total": len(revisions),
            "cursor": cursor + len(rows), "has_more": cursor + len(rows) < len(revisions),
            "latest_revision": snapshot["revision"], "warnings": list(dict.fromkeys(warnings))})

    def change(self, identifier, revision):
        snapshot, journal, saved, compacted, warnings = self._revision_data(identifier)
        if type(revision) is not int or not 1 <= revision <= snapshot["revision"]:
            raise ValueError("Invalid memory revision")
        current, available = [], True
        versions = {0: []}
        for number in range(1, revision + 1):
            entry = journal.get(number)
            if entry is None:
                available = False
            elif available:
                if entry.get("kind") == "working_notes":
                    current = [entry, *[item for item in current if item.get("kind") != "working_notes"]]
                else:
                    current = [*current, entry]
            metadata = saved.get(number)
            if number in compacted:
                current, available = compacted[number], True
            elif ((metadata is not None and metadata.get("compacted"))
                  or (metadata is None and snapshot.get("compactions", 0))):
                available = False
            if number == snapshot["revision"]:
                current, available = snapshot["entries"], True
            if number in {revision - 1, revision}:
                versions[number] = deepcopy(current) if available else None
        before, after = versions.get(revision - 1), versions.get(revision)
        metadata = {**saved.get(revision, {}), "compacted": saved.get(revision, {}).get("compacted", revision in compacted)}
        result = {"id": identifier, "previous_revision": revision - 1, "revision": revision,
                  "metadata": metadata, "available": before is not None and after is not None,
                  "warnings": list(dict.fromkeys(warnings)), "added": [], "removed": [], "updated": []}
        if not result["available"]:
            result["warnings"].append("Log thiếu bản chụp sau compact hoặc journal bị thiếu; không thể so sánh chính xác revision này.")
            return self.redactor.redact(result)
        key = lambda entry: (entry.get("kind"), entry.get("sequence"))
        left, right = {key(e): e for e in before}, {key(e): e for e in after}
        result.update(added=[e for k, e in right.items() if k not in left],
                      removed=[e for k, e in left.items() if k not in right],
                      updated=[{"before": left[k], "after": e} for k, e in right.items() if k in left and left[k] != e],
                      before={"entries": len(before), "estimated_tokens": saved.get(revision - 1, {}).get("after_estimated_tokens", 0 if revision == 1 else None)},
                      after={"entries": len(after), "estimated_tokens": saved.get(revision, {}).get("after_estimated_tokens", snapshot.get("estimated_tokens") if revision == snapshot["revision"] else None)})
        return self.redactor.redact(result)
