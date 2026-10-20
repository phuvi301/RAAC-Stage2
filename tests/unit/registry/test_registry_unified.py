"""Registry boundaries shared by configuration callers and the MAS adapter."""
from copy import deepcopy
import hashlib
import shutil

import pytest
import yaml

from raac.registry import RegistryError, RoleGroup, load_registry
from raac.registry.models import RoleSpec
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def copied_registry(tmp_path):
    path = tmp_path / "registry"
    shutil.copytree(ROOT / "registry", path)
    return path


def edit_role(root, role_id, **updates):
    path = root / "roles/core" / (role_id + ".yaml")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data.update(updates)
    path.write_text(yaml.safe_dump(data), encoding="utf-8")


def test_loaded_role_hash_and_snapshot_are_stable_and_detached(copied_registry):
    registry = load_registry(copied_registry)
    loaded = registry.get("Planner")
    assert loaded == registry.get("planner")
    assert loaded.spec is registry.get_role("planner")
    assert loaded.prompt_hash == hashlib.sha256(loaded.system_prompt.encode("utf-8")).hexdigest()
    snapshot = registry.snapshot()
    assert snapshot["roles"]["planner"]["prompt_hash"] == loaded.prompt_hash
    snapshot["roles"]["planner"]["tools"].append("not_a_tool")
    assert "not_a_tool" not in registry.get_role("planner").tools


@pytest.mark.parametrize("value", ["../VERSION", "prompts/../../outside.md"])
def test_prompt_cannot_escape_prompt_root(copied_registry, value):
    edit_role(copied_registry, "planner", prompt=value)
    with pytest.raises(RegistryError, match="stay inside registry/prompts"):
        load_registry(copied_registry)


def test_duplicate_tools_are_rejected_by_loader(copied_registry):
    edit_role(copied_registry, "planner", tools=["read_file", "read_file"])
    with pytest.raises(RegistryError, match="duplicates"):
        load_registry(copied_registry)


def test_core_id_and_display_name_must_match(copied_registry):
    edit_role(copied_registry, "planner", role="Other")
    with pytest.raises(RegistryError, match="must be named"):
        load_registry(copied_registry)


def test_group_alias_and_bom_are_supported(copied_registry):
    path = copied_registry / "roles/core/planner.yaml"
    data = yaml.safe_load(path.read_text())
    data["Group"] = data.pop("group")
    path.write_text(yaml.safe_dump(data), encoding="utf-8-sig")
    registry = load_registry(copied_registry)
    assert registry.get("Planner").spec.group is RoleGroup.CORE


def test_extended_role_can_be_loaded_without_claiming_execution(copied_registry):
    source = copied_registry / "roles/core/reviewer.yaml"
    data = yaml.safe_load(source.read_text())
    data.update(id="security_reviewer", role="Security Reviewer", group="extended", prompt="prompts/security.md")
    (copied_registry / "roles/extended").mkdir(exist_ok=True)
    (copied_registry / "roles/extended/security_reviewer.yaml").write_text(yaml.safe_dump(data))
    (copied_registry / "prompts/security.md").write_text("Review security.")
    registry = load_registry(copied_registry)
    assert [role.id for role in registry.roles_in_group(RoleGroup.EXTENDED)] == ["security_reviewer"]
    assert registry.get("Security Reviewer").system_prompt == "Review security."
    assert len(registry.roles_in_group(RoleGroup.CORE)) == 5


def test_missing_catalog_and_bad_version_are_both_reported(copied_registry):
    (copied_registry / "tools.yaml").unlink()
    (copied_registry / "VERSION").write_text("latest")
    with pytest.raises(RegistryError) as caught:
        load_registry(copied_registry)
    assert "tools.yaml" in str(caught.value)
    assert "valid version" in str(caught.value)
