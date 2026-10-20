import pytest
from pydantic import ValidationError

from raac.registry.models import ModelSpec, OutputFormat, RoleGroup, RoleSpec, ToolCatalog, ToolSpec


def make_role(**overrides):
    data = dict(
        id="coder",
        role="Coder",
        group="core",
        prompt="prompts/coder.md",
        max_llm_calls=10,
        prompt_version="v1",
        tools=["read_file"],
    )
    data.update(overrides)
    return RoleSpec(**data)


def test_defaults_for_model_and_cost_weight():
    role = make_role()
    assert role.model == ModelSpec(ref="default")
    assert role.cost_weight == 1.0
    assert role.group is RoleGroup.CORE
    assert role.output_format is OutputFormat.UNIFIED_DIFF
    assert role.name == "Coder"
    assert "output_format" not in role.model_dump()
    assert role.prompt_filename == "coder.md"


@pytest.mark.parametrize(
    "overrides",
    [
        {"id": "Coder"},  # not a slug
        {"id": "1coder"},
        {"prompt_version": ""},
        {"cost_weight": 0},
        {"cost_weight": -1},
        {"cost_weight": float("inf")},
        {"max_llm_calls": "6"},
        {"model": {"ref": "invalid/name"}},
        {"group": "optional"},
        {"output_format": "yaml"},
        {"tools": ["read_file", "read_file"]},
        {"unknown_field": 1},
    ],
)
def test_invalid_role_is_rejected(overrides):
    with pytest.raises(ValidationError):
        make_role(**overrides)


def test_missing_required_field():
    with pytest.raises(ValidationError):
        RoleSpec(id="coder", role="Coder", group="core", prompt_version="chain-v3")  # missing prompt and call cap


def test_role_is_immutable():
    role = make_role()
    with pytest.raises(ValidationError):
        role.cost_weight = 2.0


def test_tool_catalog_rejects_duplicate_names():
    t = {"name": "read_file", "description": "d"}
    with pytest.raises(ValidationError):
        ToolCatalog(tools=[t, t])
    assert ToolCatalog(tools=[t]).tools[0] == ToolSpec(name="read_file", description="d", read_only=True)
