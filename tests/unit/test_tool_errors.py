"""Tool failures say what went wrong, without forwarding raw exception text."""

import errno

import pytest

from raac.common.errors import SourceModifiedError
from raac.harness.layer3_execution.tool_errors import GENERIC, describe_tool_error


def test_missing_file_is_reported_as_not_found():
    result = describe_tool_error(FileNotFoundError(errno.ENOENT, "No such file", "C:/Users/secret/setup.cfg"))
    assert result["error_kind"] == "NotFound"
    assert "secret" not in result["error"] and "setup.cfg" not in result["error"]


def test_operating_system_denial_is_not_a_role_rule():
    result = describe_tool_error(PermissionError(errno.EACCES, "denied", "/root/x"))
    assert result["error_kind"] == "PermissionDenied"
    assert "/root" not in result["error"]


def test_source_modified_lists_the_files_that_changed():
    paths = [f"result_images/{index}.png" for index in range(13)]
    result = describe_tool_error(SourceModifiedError("Environment modified tracked source; rebuild its profile", paths))
    assert result["error_kind"] == "SourceModified"
    assert result["paths"] == sorted(paths)[:10] and result["more_paths"] == 3
    assert result["detail"] == "Environment modified tracked source; rebuild its profile"
    assert "outside the repository" in result["error"]


def test_framework_role_rule_keeps_its_own_sentence():
    result = describe_tool_error(PermissionError("Tester may write separate new development tests only; baseline tests are read-only"))
    assert result["error_kind"] == "RoleNotAllowed"
    assert result["error"].startswith("Tester may write separate new development tests")


@pytest.mark.parametrize("message,kind", [
    ("Path is outside workspace", "PathNotAllowed"),
    ("symlink", "PathNotAllowed"),
    ("File exceeds size limit", "TooLarge"),
    ("snapshot limit", "TooLarge"),
    ("Command cwd is not a repository directory", "BadWorkingDirectory"),
    ("Invalid line range", "InvalidLineRange"),
    ("Query must contain 1..256 characters", "InvalidQuery"),
    ("Specify nonempty old_text and a positive exact match count", "InvalidReplace"),
    ("Candidate has unattributed source changes", "AttributionFailed"),
    ("Container file operation failed or exceeded its limit", "ContainerFileError"),
])
def test_known_value_errors_map_to_a_specific_kind(message, kind):
    assert describe_tool_error(ValueError(message))["error_kind"] == kind


def test_unknown_value_error_text_is_never_forwarded():
    result = describe_tool_error(ValueError("bad value C:/Users/secret/token.txt"))
    assert (result["error_kind"], result["error"]) == GENERIC
    assert "secret" not in result["error"]
