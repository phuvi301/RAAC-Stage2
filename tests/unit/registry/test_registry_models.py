import pytest
from pydantic import ValidationError

from raac.registry.models import ModelSpec, OutputFormat, RoleGroup, RoleSpec, ToolCatalog, ToolSpec


def make_role(**overrides):
    data = dict(
        id="coder",
        role="Coder",
        group="core",
        description="d",
        prompt_version="v1",
        tools=["read_file"],
        output_format="unified_diff",
    )
    data.update(overrides)
    return RoleSpec(**data)


def test_defaults_for_model_and_cost_weight():
    role = make_role()
    assert role.model == ModelSpec(ref="default")
    assert role.cost_weight == 1.0
    assert role.group is RoleGroup.CORE
    assert role.output_format is OutputFormat.UNIFIED_DIFF
    assert role.prompt_filename == "coder.v1.md"


@pytest.mark.parametrize(
    "overrides",
    [
        {"id": "Coder"},  # not a slug
        {"id": "1coder"},
        {"prompt_version": "1"},
        {"cost_weight": 0},
        {"cost_weight": -1},
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
        RoleSpec(id="x", role="X", group="core", description="d", prompt_version="v1")  # no output_format


def test_role_is_immutable():
    role = make_role()
    with pytest.raises(ValidationError):
        role.cost_weight = 2.0


def test_tool_catalog_rejects_duplicate_names():
    t = {"name": "read_file", "description": "d"}
    with pytest.raises(ValidationError):
        ToolCatalog(tools=[t, t])
    assert ToolCatalog(tools=[t]).tools[0] == ToolSpec(name="read_file", description="d", read_only=True)
