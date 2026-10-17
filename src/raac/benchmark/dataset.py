"""Stage1 dataset loader."""

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


def load_tasks(path: Path) -> dict[str, Task]:
    data = json.loads(path.read_text(encoding="utf-8"))
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
