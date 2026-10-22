"""Candidate export uses a trusted baseline and includes shell-written files."""

import hashlib

import pytest

from raac.harness.layer3_execution.tools.docker_workspace import DockerWorkspaceTools
from raac.harness.layer3_execution.tools.workspace import apply_repo_patch


def fixture_workspace(before, after, *, modes=None):
    workspace = object.__new__(DockerWorkspaceTools)
    workspace.baseline_files = before
    workspace.baseline_modes = dict.fromkeys(before, 0o644)
    workspace.current_modes = modes or dict.fromkeys(after, 0o644)
    workspace.coder_paths = set(before) | set(after)
    workspace.tester_paths = set()
    workspace.baseline_ref = "trusted-baseline"
    workspace._snapshot = lambda: after
    return workspace


def test_export_reconstructs_edits_new_files_and_deletions(tmp_path):
    before = {"old.py": b"OLD = 1\n", "value.py": b"VALUE = 0\n"}
    after = {"value.py": b"VALUE = 1\n", "new.py": "TEXT = 'xin chào'\n".encode()}
    workspace = fixture_workspace(before, after)
    output = tmp_path / "candidate.patch"
    artifact = workspace.export_candidate(output)
    patch = output.read_text(encoding="utf-8")
    changed = apply_repo_patch({k: v.decode() for k, v in before.items()}, patch)
    assert changed == {"old.py": None, "value.py": "VALUE = 1\n", "new.py": "TEXT = 'xin chào'\n"}
    assert artifact["sha256"] == hashlib.sha256(output.read_bytes()).hexdigest()


@pytest.mark.parametrize("after,modes", [
    ({"value.py": b"\x00\x01"}, None),
    ({"value.py": b"VALUE = 0\n"}, {"value.py": 0o755}),
    ({"value.py": b"VALUE = 0\n", "empty.py": b""}, None),
])
def test_unsupported_changes_are_not_silently_dropped(tmp_path, after, modes):
    workspace = fixture_workspace({"value.py": b"VALUE = 0\n"}, after, modes=modes)
    with pytest.raises(ValueError, match="unsupported"):
        workspace.export_candidate(tmp_path / "candidate.patch")


def test_unattributed_change_blocks_export(tmp_path):
    workspace = fixture_workspace({}, {"mystery.py": b"VALUE = 1\n"})
    workspace.coder_paths.clear()
    with pytest.raises(ValueError, match="attribut"):
        workspace.export_candidate(tmp_path / "candidate.patch")
