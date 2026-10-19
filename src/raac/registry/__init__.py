"""Agent Registry: schema, loader and output-format validation for agent roles."""

from .loader import (
    RegistryError,
    default_registry_dir,
    load_registry,
    read_registry_version,
    render_prompt,
    stamp_record,
)
from .models import (
    REQUIRED_CORE_ROLES,
    ModelSpec,
    OutputFormat,
    Registry,
    RoleGroup,
    RoleSpec,
    ToolSpec,
)
from .output_formats import OutputFormatError, parse_role_output, validate_unified_diff

__all__ = [
    "REQUIRED_CORE_ROLES",
    "ModelSpec",
    "OutputFormat",
    "OutputFormatError",
    "Registry",
    "RegistryError",
    "RoleGroup",
    "RoleSpec",
    "ToolSpec",
    "default_registry_dir",
    "load_registry",
    "parse_role_output",
    "read_registry_version",
    "render_prompt",
    "stamp_record",
    "validate_unified_diff",
]
