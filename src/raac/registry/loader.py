"""Load and validate the Agent Registry from ``registry/``.

Layout expected on disk::

    registry/
      VERSION                        # e.g. 0.1.0, stamped into every result record
      tools.yaml                     # tool catalog
      roles/core/<id>.yaml           # core roles
      roles/extended/<id>.yaml       # extended roles (L4 only)
      prompts/<role>.md (explicit role.prompt path)      # system prompt template per role

All problems found are collected and raised together as one :class:`RegistryError`
so a broken registry can be fixed in a single pass.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from string import Template
from typing import Any

import yaml
from pydantic import ValidationError

from .models import (
    REQUIRED_CORE_ROLES,
    LoadedRole,
    Registry,
    RoleGroup,
    RoleSpec,
    ToolCatalog,
)

ENV_REGISTRY_DIR = "RAAC_REGISTRY_DIR"
RESULT_VERSION_KEY = "registry_version"
_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+([-+][0-9A-Za-z.-]+)?$")


class RegistryError(ValueError):
    """The registry is missing, malformed or inconsistent."""


def default_registry_dir() -> Path:
    """``$RAAC_REGISTRY_DIR`` if set, else ``<repo root>/registry`` (source checkout layout)."""
    env = os.environ.get(ENV_REGISTRY_DIR)
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[3] / "registry"


# --------------------------------------------------------------------------- version


def read_registry_version(registry_dir: Path | str | None = None) -> str:
    path = Path(registry_dir) if registry_dir else default_registry_dir()
    version_file = path / "VERSION"
    if not version_file.is_file():
        raise RegistryError(f"{version_file}: file not found (registry version is required)")
    version = version_file.read_text(encoding="utf-8-sig").strip()
    if not _VERSION_RE.match(version):
        raise RegistryError(f"{version_file}: '{version}' is not a valid version (expected MAJOR.MINOR.PATCH)")
    return version


def stamp_record(record: dict[str, Any], registry_dir: Path | str | None = None) -> dict[str, Any]:
    """Return a copy of a result record with ``registry_version`` set from ``registry/VERSION``.

    Every writer of a result record (``benchmark/run_record.py``, the JSONL logger) must
    pass the record through this function. An existing, different ``registry_version`` is
    an error: a record must never mix data from two registry versions.
    """
    version = read_registry_version(registry_dir)
    existing = record.get(RESULT_VERSION_KEY)
    if existing is not None and existing != version:
        raise RegistryError(
            f"record already has {RESULT_VERSION_KEY}='{existing}' but registry is '{version}'"
        )
    out = dict(record)
    out[RESULT_VERSION_KEY] = version
    return out


# --------------------------------------------------------------------------- loading


def _read_yaml(path: Path, errors: list[str]) -> Any | None:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8-sig"))
    except yaml.YAMLError as exc:
        errors.append(f"{path}: invalid YAML ({exc})")
        return None
    except (OSError, UnicodeError) as exc:
        errors.append(f"{path}: cannot read ({type(exc).__name__})")
        return None
    if not isinstance(data, dict):
        errors.append(f"{path}: expected a mapping at the top level")
        return None
    return data


def _format_validation_error(path: Path, exc: ValidationError) -> list[str]:
    lines = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err["loc"]) or "<root>"
        if err["type"] == "missing":
            lines.append(f"{path}: missing required field '{loc}'")
        elif err["type"] == "extra_forbidden":
            lines.append(f"{path}: unknown field '{loc}'")
        else:
            lines.append(f"{path}: field '{loc}': {err['msg']}")
    return lines


def load_registry(registry_dir: Path | str | None = None, *, require_core: bool = True) -> Registry:
    """Load, validate and return the registry. Raises :class:`RegistryError` on any problem."""
    root = Path(registry_dir) if registry_dir else default_registry_dir()
    if not root.is_dir():
        raise RegistryError(f"{root}: registry directory not found")

    errors: list[str] = []

    try:
        version = read_registry_version(root)
    except RegistryError as exc:
        errors.append(str(exc))
        version = ""

    # tool catalog
    tools: dict = {}
    tools_path = root / "tools.yaml"
    if not tools_path.is_file():
        errors.append(f"{tools_path}: file not found")
    else:
        data = _read_yaml(tools_path, errors)
        if data is not None:
            try:
                catalog = ToolCatalog.model_validate(data)
                tools = {t.name: t for t in catalog.tools}
            except ValidationError as exc:
                errors.extend(_format_validation_error(tools_path, exc))

    # roles
    roles: dict[str, RoleSpec] = {}
    prompts: dict[str, str] = {}
    origin: dict[str, Path] = {}
    names: set[str] = set()
    for group in RoleGroup:
        group_dir = root / "roles" / group.value
        if not group_dir.is_dir():
            if group is RoleGroup.CORE:
                errors.append(f"{group_dir}: directory not found")
            continue
        for path in sorted(group_dir.glob("*.yaml")):
            data = _read_yaml(path, errors)
            if data is None:
                continue
            try:
                role = RoleSpec.model_validate(data)
            except ValidationError as exc:
                errors.extend(_format_validation_error(path, exc))
                continue

            problems: list[str] = []
            if role.group is not group:
                problems.append(f"group is '{role.group.value}' but file is under roles/{group.value}/")
            if path.stem != role.id:
                problems.append(f"file name must match id '{role.id}'")
            if role.id in roles:
                problems.append(f"duplicate role id '{role.id}' (also defined in {origin[role.id]})")
            unknown = [t for t in role.tools if t not in tools]
            if unknown and tools:
                problems.append(f"unknown tools {unknown} (not in tools.yaml)")
            if role.role in names:
                problems.append(f"duplicate role name '{role.role}'")
            if group is RoleGroup.CORE and role.id in REQUIRED_CORE_ROLES and role.role != role.id.capitalize():
                problems.append(f"core role '{role.id}' must be named '{role.id.capitalize()}'")
            prompt_path = (root / role.prompt).resolve()
            prompt_root = (root / "prompts").resolve()
            if not prompt_root.is_relative_to(root.resolve()) or not prompt_path.is_relative_to(prompt_root):
                errors.append(f"{path}: role prompt must stay inside registry/prompts")
                continue
            if not prompt_path.is_file():
                problems.append(f"prompt file {prompt_path} not found")
            else:
                try:
                    text = prompt_path.read_text(encoding="utf-8-sig").strip()
                except (OSError, UnicodeError):
                    errors.append(f"{path}: cannot read prompt file {prompt_path}")
                    continue
                if not text.strip():
                    problems.append(f"prompt file {prompt_path} is empty")
                else:
                    prompts[role.id] = text
            if problems:
                errors.extend(f"{path}: {p}" for p in problems)
                continue
            roles[role.id] = role
            origin[role.id] = path
            names.add(role.role)

    if require_core:
        missing = [r for r in REQUIRED_CORE_ROLES if r not in roles]
        if missing and not any("directory not found" in e for e in errors):
            errors.append(f"{root / 'roles' / 'core'}: missing core roles: {', '.join(missing)}")

    if errors:
        raise RegistryError("invalid registry:\n  - " + "\n  - ".join(errors))

    return Registry(version=version, tools=tools, roles=roles, prompts=prompts)


def render_prompt(registry: Registry, role_id: str, **variables: str) -> str:
    """Return the system prompt of a role, substituting ``${name}`` variables if any.

    Unknown placeholders are left untouched (``safe_substitute``), so the current static
    templates are returned unchanged.
    """
    registry.get_role(role_id)  # raises a clear KeyError for unknown roles
    return Template(registry.prompts[role_id]).safe_substitute(**variables)
