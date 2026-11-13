"""The four phases. Each builds one model request from earlier artifacts, validates the reply and saves it."""

from __future__ import annotations

import json
import re
from pathlib import Path

from pydantic import BaseModel, ValidationError

from .models import Architecture, Clarification, EnvironmentPlan, Spec, TaskGraph
from .ports import JsonModel
from .store import Store
from .task_graph import problems

PROMPTS = Path(__file__).resolve().parents[1] / "prompts"
SKIP_DIRS = {".git", ".hg", ".svn", ".venv", "venv", "node_modules", "__pycache__", "build", "dist", ".tox",
             ".mypy_cache", ".pytest_cache", ".idea", ".vscode"}
MANIFESTS = ("pyproject.toml", "setup.py", "setup.cfg", "requirements.txt", "package.json", "go.mod", "Cargo.toml",
             "pom.xml", "build.gradle", "composer.json", "Gemfile", "Makefile", "Dockerfile", "tox.ini")


class PhaseError(RuntimeError):
    """The model could not produce a valid artifact within the allowed attempts."""


def repo_overview(repo: Path | None, *, max_files: int = 20000) -> dict | None:
    """A short, read-only description of an existing repository (no file contents beyond a README excerpt)."""
    if repo is None or not Path(repo).is_dir():
        return None
    root = Path(repo)
    extensions: dict[str, int] = {}
    seen = 0
    for path in root.rglob("*"):
        if any(part in SKIP_DIRS or part.startswith(".venv") for part in path.relative_to(root).parts):
            continue
        if path.is_file():
            seen += 1
            extensions[path.suffix.lower() or "(none)"] = extensions.get(path.suffix.lower() or "(none)", 0) + 1
            if seen >= max_files:
                break
    readme = next((p for p in root.iterdir() if p.is_file() and p.name.lower().startswith("readme")), None)
    return {
        "top_level": sorted(p.name + ("/" if p.is_dir() else "") for p in root.iterdir()
                            if p.name not in SKIP_DIRS and not p.name.startswith(".venv"))[:60],
        "manifests": [name for name in MANIFESTS if (root / name).is_file()],
        "file_types": dict(sorted(extensions.items(), key=lambda item: -item[1])[:15]),
        "files_counted": seen,
        "readme_excerpt": readme.read_text(encoding="utf-8", errors="replace")[:1500] if readme else None,
    }


def parse_json_object(text: str) -> dict:
    """The JSON object in a reply, tolerating Markdown fences or a sentence around it."""
    cleaned = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", text.strip())
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("the reply contains no JSON object") from None
        value = json.loads(cleaned[start:end + 1])
    if not isinstance(value, dict):
        raise ValueError("the reply is not a JSON object")
    return value


async def ask(model: JsonModel, *, phase: str, schema: type[BaseModel], payload: dict,
              check=None, attempts: int = 3) -> BaseModel:
    """Ask, validate against ``schema`` (and ``check`` for rules a schema cannot express), retry with the errors."""
    system = (PROMPTS / f"{phase}.md").read_text(encoding="utf-8").strip() + (
        "\n\nReply with exactly one JSON object, no prose and no Markdown fences, matching this JSON schema:\n"
        + json.dumps(schema.model_json_schema(), ensure_ascii=False))
    user = json.dumps(payload, ensure_ascii=False, indent=1)
    feedback = ""
    for _ in range(attempts):
        reply = await model.complete_json(phase=phase, system=system, user=user + feedback)
        try:
            artifact = schema.model_validate(parse_json_object(reply))
            issues = check(artifact) if check else []
        except (ValueError, ValidationError) as error:
            issues = [str(error)[:3000]]
        if not issues:
            return artifact
        feedback = ("\n\nYour previous reply was rejected. Fix every problem and return the complete JSON object "
                    "again.\nProblems:\n- " + "\n- ".join(issues) + "\nPrevious reply:\n" + reply[:12000])
    raise PhaseError(f"{phase}: no valid reply after {attempts} attempts: " + "; ".join(issues)[:1000])


# ---------------------------------------------------------------- phases
def _base(store: Store, repo: Path | None) -> dict:
    payload = {"request": store.request()}
    overview = repo_overview(repo)
    if overview:
        payload["existing_repository"] = overview
    return payload


async def clarify(store: Store, model: JsonModel, *, repo: Path | None = None) -> Clarification:
    payload = _base(store, repo)
    previous = store.answers()
    if previous:  # a follow-up round: show what was already settled
        asked = store.load_model("clarify.json", Clarification)
        payload["earlier_questions_and_answers"] = [
            {"question": q.question, "answer": a.answer, "source": a.source}
            for q, a in zip(asked.questions, previous)]
    result = await ask(model, phase="clarify", schema=Clarification, payload=payload)
    if previous:  # keep earlier answers on record by renumbering the new questions after them
        offset = len(previous)
        for index, question in enumerate(result.questions, 1):
            question.id = f"Q{offset + index}"
        asked = store.load_model("clarify.json", Clarification)
        kept = asked.model_copy(update={"understanding": result.understanding,
                                        "questions": asked.questions + result.questions})
        old_answers = {a.question_id: a for a in previous}
        store.save_clarification(kept)
        _restore_answers(store, old_answers)
        return kept
    store.save_clarification(result)
    return result


def _restore_answers(store: Store, answers: dict) -> None:
    """Rewriting answers.md for a new round must not lose what the user already typed."""
    text = store.read_text("answers.md")
    for question_id, answer in answers.items():
        if answer.source == "user":
            text = re.sub(rf"(?ms)(^## {question_id}\b.*?^A:)[^\n]*", lambda m: m.group(1) + " " + answer.answer, text)
    store.write_text("answers.md", text)


async def specify(store: Store, model: JsonModel, *, repo: Path | None = None) -> Spec:
    payload = _base(store, repo)
    if store.exists("clarify.json"):
        asked = store.load_model("clarify.json", Clarification)
        payload["clarifications"] = [
            {"question": q.question, "answer": a.answer, "source": a.source}
            for q, a in zip(asked.questions, store.answers())]
    spec = await ask(model, phase="specify", schema=Spec, payload=payload, check=_spec_issues)
    store.save_spec(spec)
    return spec


def _spec_issues(spec: Spec) -> list[str]:
    found = []
    ids = [req.id for req in spec.requirements]
    if len(ids) != len(set(ids)):
        found.append("requirement ids must be unique")
    for req in spec.requirements:
        if req.kind == "non_functional" and not (req.metric and req.target):
            found.append(f"{req.id} is non-functional and needs both metric and target")
    if not any(req.kind == "functional" for req in spec.requirements):
        found.append("at least one functional requirement is needed")
    return found


async def architect(store: Store, model: JsonModel, *, repo: Path | None = None) -> Architecture:
    payload = _base(store, repo)
    spec = store.load_model("spec.json", Spec)
    payload["specification"] = spec.model_dump()
    known = {req.id for req in spec.requirements}

    def check(architecture: Architecture) -> list[str]:
        return [f"{d.id} refers to unknown requirement {r}"
                for d in architecture.decisions for r in d.requirement_ids if r not in known]

    architecture = await ask(model, phase="architect", schema=Architecture, payload=payload, check=check)
    store.save_architecture(architecture)
    return architecture


def environment_issues(plan: EnvironmentPlan, inventory: dict) -> list[str]:
    """A runtime marked installed must really be on the machine in a matching version; installs need a command."""
    installed: dict[str, list[tuple[str, str | None]]] = {}
    for item in inventory.get("python", []):
        if item.get("exists"):
            installed.setdefault("python", []).append((item["version"], item["path"]))
    for name, item in inventory.get("tools", {}).items():
        installed.setdefault(name, []).append((item["version"], item.get("path")))
    aliases = {"nodejs": "node", "node.js": "node", "python3": "python", "cpython": "python", ".net": "dotnet",
               "jdk": "java", "openjdk": "java", "golang": "go", "rust": "rustc"}
    found = []
    for runtime in plan.runtimes:
        name = aliases.get(runtime.name.strip().lower(), runtime.name.strip().lower())
        if runtime.source == "install":
            if not runtime.install_command:
                found.append(f"{runtime.name} {runtime.version} must be installed but has no install_command")
            continue
        candidates = installed.get(name)
        if not candidates:
            found.append(f"{runtime.name} is marked installed but is not on this machine; use source=install")
        elif not any(_same_version(runtime.version, version) for version, _ in candidates):
            have = ", ".join(version for version, _ in candidates)
            found.append(f"{runtime.name} {runtime.version} is marked installed but the machine has {have}")
    return found


def _same_version(wanted: str, have: str) -> bool:
    """3.12 matches 3.12.4; 24 matches 24.21.0; 24.21.0 matches 24.21.0."""
    wanted_parts, have_parts = wanted.strip().lstrip("v").split("."), have.strip().lstrip("v").split(".")
    return have_parts[:len(wanted_parts)] == wanted_parts or wanted_parts[:len(have_parts)] == have_parts


async def environment(store: Store, model: JsonModel, *, repo: Path | None = None,
                      inventory: dict | None = None) -> EnvironmentPlan:
    from .machine import probe

    inventory = inventory if inventory is not None else probe()
    store.write_text("machine.json", json.dumps(inventory, ensure_ascii=False, indent=2) + "\n")
    payload = _base(store, repo)
    payload["specification"] = store.load_model("spec.json", Spec).model_dump()
    payload["architecture"] = store.load_model("architecture.json", Architecture).model_dump()
    payload["machine"] = inventory
    result = await ask(model, phase="environment", schema=EnvironmentPlan, payload=payload,
                       check=lambda candidate: environment_issues(candidate, inventory))
    store.save_environment(result)
    return result


async def plan(store: Store, model: JsonModel, *, repo: Path | None = None, max_tasks: int = 40) -> TaskGraph:
    payload = _base(store, repo)
    spec = store.load_model("spec.json", Spec)
    payload["specification"] = spec.model_dump()
    payload["acceptance_criterion_ids"] = spec.ac_ids()
    payload["architecture"] = store.load_model("architecture.json", Architecture).model_dump()
    if store.exists("environment.json"):
        payload["environment"] = store.load_model("environment.json", EnvironmentPlan).model_dump()
    payload["max_tasks"] = max_tasks
    graph = await ask(model, phase="plan", schema=TaskGraph, payload=payload,
                      check=lambda candidate: problems(candidate, spec, max_tasks=max_tasks))
    store.save_tasks(graph, spec)
    return graph
