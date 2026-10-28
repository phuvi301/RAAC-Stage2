import shutil
from pathlib import Path

import pytest
import yaml

from raac.registry import (
    REQUIRED_CORE_ROLES,
    RegistryError,
    RoleGroup,
    load_registry,
    read_registry_version,
    render_prompt,
    stamp_record,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
REAL_REGISTRY = REPO_ROOT / "registry"


@pytest.fixture
def reg_dir(tmp_path):
    """A writable copy of the real registry."""
    dest = tmp_path / "registry"
    shutil.copytree(REAL_REGISTRY, dest)
    return dest


def test_real_registry_loads():
    registry = load_registry(REAL_REGISTRY)
    assert set(REQUIRED_CORE_ROLES) <= set(registry.roles)
    assert {r.id for r in registry.roles_in_group(RoleGroup.CORE)} == set(REQUIRED_CORE_ROLES) | {"environment", "router"}
    assert registry.roles_in_group(RoleGroup.EXTENDED) == []
    assert registry.version == read_registry_version(REAL_REGISTRY)


def test_every_role_uses_only_catalog_tools_and_has_prompt():
    registry = load_registry(REAL_REGISTRY)
    for role in registry.roles.values():
        assert all(t in registry.tools for t in role.tools)
        assert registry.prompts[role.id].strip()
        assert role.model.ref == "default"
        assert role.cost_weight == 1.0


def test_unknown_role_gives_clear_error():
    with pytest.raises(KeyError, match="unknown role 'nope'"):
        load_registry(REAL_REGISTRY).get_role("nope")


def test_missing_registry_dir(tmp_path):
    with pytest.raises(RegistryError, match="registry directory not found"):
        load_registry(tmp_path / "nothing")


def test_missing_field_names_file_and_field(reg_dir):
    path = reg_dir / "roles" / "core" / "coder.yaml"
    data = yaml.safe_load(path.read_text())
    del data["max_llm_calls"]
    path.write_text(yaml.safe_dump(data))
    with pytest.raises(RegistryError) as exc:
        load_registry(reg_dir)
    msg = str(exc.value)
    assert "coder.yaml" in msg and "missing required field 'max_llm_calls'" in msg


def test_unknown_yaml_key_is_reported(reg_dir):
    path = reg_dir / "roles" / "core" / "coder.yaml"
    path.write_text(path.read_text() + "temprature: 0.2\n")
    with pytest.raises(RegistryError, match="unknown field 'temprature'"):
        load_registry(reg_dir)


def test_unknown_tool_is_reported(reg_dir):
    path = reg_dir / "roles" / "core" / "coder.yaml"
    data = yaml.safe_load(path.read_text())
    data["tools"].append("launch_missiles")
    path.write_text(yaml.safe_dump(data))
    with pytest.raises(RegistryError, match="unknown tools \\['launch_missiles'\\]"):
        load_registry(reg_dir)


def test_missing_prompt_file_is_reported(reg_dir):
    (reg_dir / "prompts" / "tester.md").unlink()
    with pytest.raises(RegistryError, match="tester.md not found"):
        load_registry(reg_dir)


def test_group_must_match_directory(reg_dir):
    path = reg_dir / "roles" / "core" / "reviewer.yaml"
    path.write_text(path.read_text().replace("group: core", "group: extended"))
    with pytest.raises(RegistryError, match="file is under roles/core/"):
        load_registry(reg_dir)


def test_file_name_must_match_id(reg_dir):
    (reg_dir / "roles" / "core" / "planner.yaml").rename(reg_dir / "roles" / "core" / "plan.yaml")
    with pytest.raises(RegistryError, match="file name must match id 'planner'"):
        load_registry(reg_dir)


def test_missing_core_role_is_reported(reg_dir):
    (reg_dir / "roles" / "core" / "analyst.yaml").unlink()
    with pytest.raises(RegistryError, match="missing core roles: analyst"):
        load_registry(reg_dir)
    # allowed when explicitly not required (e.g. loader tests with partial registries)
    assert "analyst" not in load_registry(reg_dir, require_core=False).roles


def test_all_problems_reported_together(reg_dir):
    (reg_dir / "prompts" / "tester.md").unlink()
    (reg_dir / "prompts" / "coder.md").unlink()
    with pytest.raises(RegistryError) as exc:
        load_registry(reg_dir)
    assert "tester.md" in str(exc.value) and "coder.md" in str(exc.value)


def test_invalid_yaml(reg_dir):
    (reg_dir / "roles" / "core" / "coder.yaml").write_text("id: [unclosed\n")
    with pytest.raises(RegistryError, match="invalid YAML"):
        load_registry(reg_dir)


def test_render_prompt_substitutes_known_variables_only(reg_dir):
    (reg_dir / "prompts" / "coder.md").write_text("Language: ${lang}. Keep ${other}.")
    registry = load_registry(reg_dir)
    assert render_prompt(registry, "coder", lang="Python") == "Language: Python. Keep ${other}."


# ---------------------------------------------------------------- version stamping
def test_version_file_exists_and_is_semver():
    assert read_registry_version(REAL_REGISTRY) == "0.19.5"


def test_bad_or_missing_version(reg_dir):
    (reg_dir / "VERSION").write_text("latest\n")
    with pytest.raises(RegistryError, match="not a valid version"):
        read_registry_version(reg_dir)
    (reg_dir / "VERSION").unlink()
    with pytest.raises(RegistryError, match="file not found"):
        read_registry_version(reg_dir)
    with pytest.raises(RegistryError):
        load_registry(reg_dir)


def test_stamp_record_adds_version_without_mutating_input(reg_dir):
    record = {"task_id": "t1", "level": "L1"}
    stamped = stamp_record(record, reg_dir)
    assert stamped == {"task_id": "t1", "level": "L1", "registry_version": "0.19.5"}
    assert "registry_version" not in record


def test_stamp_record_follows_version_file(reg_dir):
    (reg_dir / "VERSION").write_text("0.2.0\n")
    assert stamp_record({}, reg_dir)["registry_version"] == "0.2.0"


def test_stamp_record_rejects_mixed_versions(reg_dir):
    with pytest.raises(RegistryError, match="already has registry_version='0.0.9'"):
        stamp_record({"registry_version": "0.0.9"}, reg_dir)
    assert stamp_record({"registry_version": "0.19.5"}, reg_dir)["registry_version"] == "0.19.5"


@pytest.mark.parametrize("role_id", ["analyst", "planner"])
def test_inspection_prompts_prefer_batching_only_known_independent_paths(role_id):
    registry = load_registry(REAL_REGISTRY)
    prompt = registry.prompts[role_id]
    assert registry.get_role(role_id).prompt_version == ("chain-v7" if role_id in {"analyst", "tester"} else "chain-v6")
    assert "independent file paths are already known" in prompt
    assert "read_files" in prompt
    assert "changed or the earlier result was truncated" in prompt
    assert "keep dependent inspections ordered" in prompt
