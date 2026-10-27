"""Snapshots skip files that .gitignore excludes: install output is not source."""

from pathlib import Path
import shutil
import subprocess
from unittest.mock import patch

import pytest

from raac.harness.layer3_execution.tools.workspace import WorkspaceTools

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is needed to read .gitignore")


def git(directory, *args):
    subprocess.run(["git", "-C", str(directory), *args], check=True, capture_output=True)


def make_project(tmp_path, *, nested=True):
    """PROJECT/repo/app is the git checkout, as with repo/xarray or repo/astropy."""
    (tmp_path / "requirement").mkdir()
    (tmp_path / "requirement/user_chat.txt").write_text("Fix behavior\n", encoding="utf-8")
    checkout = tmp_path / "repo/app" if nested else tmp_path / "repo"
    checkout.mkdir(parents=True)
    (checkout / "src.py").write_text("value = 1\n", encoding="utf-8")
    (checkout / ".gitignore").write_text("build/\n*.pyd\n*.egg-info/\ngenerated.c\n", encoding="utf-8")
    git(checkout, "init", "-q")
    return tmp_path, checkout


def install_output(checkout):
    (checkout / "build/lib").mkdir(parents=True)
    (checkout / "build/lib/mod.bin").write_bytes(b"x" * 100)
    (checkout / "ext.pyd").write_bytes(b"MZ")
    (checkout / "generated.c").write_text("int x;\n", encoding="utf-8")
    (checkout / "app.egg-info").mkdir()
    (checkout / "app.egg-info/PKG-INFO").write_text("Name: app\n", encoding="utf-8")


def names(workspace):
    return sorted(workspace._snapshot())


def test_ignored_install_output_is_not_part_of_the_snapshot(tmp_path):
    project, checkout = make_project(tmp_path)
    install_output(checkout)
    (checkout / "new_module.py").write_text("x = 2\n", encoding="utf-8")  # untracked but not ignored
    workspace = WorkspaceTools(project, None)
    assert names(workspace) == ["app/.gitignore", "app/new_module.py", "app/src.py"]


def test_install_after_baseline_does_not_change_patch_or_listing(tmp_path):
    project, checkout = make_project(tmp_path)
    workspace = WorkspaceTools(project, None)
    baseline = workspace.baseline_ref
    install_output(checkout)  # what Environment's pip install -e . leaves behind
    assert names(workspace) == ["app/.gitignore", "app/src.py"]
    assert workspace.current_patch() == ""
    assert baseline == workspace.baseline_ref
    assert [item["path"] for item in workspace.list_files()["files"]] == ["app/.gitignore", "app/src.py"]


def test_ignored_files_do_not_count_toward_size_and_count_limits(tmp_path):
    project, checkout = make_project(tmp_path)
    with (checkout / "build.pyd").open("wb") as stream:
        stream.seek(WorkspaceTools.max_file_bytes)
        stream.write(b"x")  # a compiled artifact larger than any single file may be
    workspace = WorkspaceTools(project, None)
    assert names(workspace) == ["app/.gitignore", "app/src.py"]


def test_tracked_file_matching_gitignore_stays_in_the_snapshot(tmp_path):
    project, checkout = make_project(tmp_path)
    (checkout / "build").mkdir()
    (checkout / "build/keep.txt").write_text("tracked on purpose\n", encoding="utf-8")
    git(checkout, "add", "-f", "build/keep.txt")
    (checkout / "build/other.bin").write_bytes(b"x")
    workspace = WorkspaceTools(project, None)
    assert "app/build/keep.txt" in names(workspace)
    assert "app/build/other.bin" not in names(workspace)


def test_checkout_at_repo_root_is_handled_too(tmp_path):
    project, checkout = make_project(tmp_path, nested=False)
    install_output(checkout)
    assert names(WorkspaceTools(project, None)) == [".gitignore", "src.py"]


def test_without_git_metadata_or_git_the_snapshot_is_unchanged(tmp_path):
    project, checkout = make_project(tmp_path)
    install_output(checkout)
    shutil.rmtree(checkout / ".git")
    plain = names(WorkspaceTools(project, None))
    assert "app/ext.pyd" in plain and "app/generated.c" in plain
    (tmp_path / "second").mkdir()
    project2, checkout2 = make_project(tmp_path / "second")
    install_output(checkout2)
    with patch("raac.harness.layer3_execution.tools.workspace.subprocess.run", side_effect=OSError("no git")):
        assert "app/ext.pyd" in names(WorkspaceTools(project2, None))
