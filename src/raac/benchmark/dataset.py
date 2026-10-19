"""Stage1 dataset loader."""

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Task:
    id: str
    repo: str
    repo_url: str
    title: str
    body: str
    base_commit: str


def source_dataset(path: Path) -> Path:
    """Resolve a frozen selection without exposing evaluator artifacts to agents."""
    path = Path(path).resolve()
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("kind") != "raac-task-selection":
        return path
    if data.get("schema_version") != 1:
        raise ValueError("Unsupported task selection version")
    source = (path.parent / data["source_dataset"]).resolve()
    raw = source.read_bytes()
    if hashlib.sha256(raw).hexdigest() != data["source_sha256"]:
        raise ValueError("Frozen selection source hash changed")
    if json.loads(raw).get("kind") == "raac-task-selection":
        raise ValueError("Nested task selections are not supported")
    return source


def load_tasks(path: Path) -> dict[str, Task]:
    path = Path(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    source = source_dataset(path)
    if source != path.resolve():
        tasks = load_tasks(source)
        ids = data.get("task_ids")
        if (
            not isinstance(ids, list)
            or not ids
            or any(not isinstance(x, str) for x in ids)
            or len(ids) != len(set(ids))
            or set(ids) - set(tasks)
        ):
            raise ValueError("Invalid frozen task selection")
        if data.get("task_count") != len(ids):
            raise ValueError("Frozen task count mismatch")
        return {identifier: tasks[identifier] for identifier in ids}
    tasks = {}
    for repo in data["repos"]:
        name = repo["repo"]
        if not re.fullmatch(r"[\w.-]+/[\w.-]+", name) or any(
            x in {".", ".."} for x in name.split("/")
        ):
            raise ValueError(f"Invalid repo: {name}")
        if repo["repo_url"] != f"https://github.com/{name}":
            raise ValueError(f"Expected public GitHub URL for {name}")
        for issue in repo["issues"]:
            fields = {k: issue[k] for k in ("id", "title", "body", "base_commit")}
            if not all(isinstance(v, str) and v.strip() for v in fields.values()):
                raise ValueError("Task fields must be nonempty strings")
            if not re.fullmatch(r"[0-9a-fA-F]{40}", fields["base_commit"]):
                raise ValueError("Invalid base commit")
            if fields["id"] in tasks:
                raise ValueError(f"Duplicate task: {fields['id']}")
            tasks[fields["id"]] = Task(repo=name, repo_url=repo["repo_url"], **fields)
    return tasks
