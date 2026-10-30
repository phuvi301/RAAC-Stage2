"""New-run no-op patches and observed nested Git origins are checked without guessing."""

from pathlib import Path
import difflib
import tempfile
import unittest

from raac.harness.layer3_execution.local_executor import LocalExecutor, LocalExecutionPolicy
from raac.harness.layer3_execution.tools.workspace import WorkspaceTools
from raac.harness.layer3_execution.tools.patching import rebase_repo_patch, apply_repo_patch
from raac.harness.layer3_execution.tool_orchestrator import ToolOrchestrator


OLD = "def operation():\n    return 1\n"
NEW = "def operation():\n    return 2\n"


def patch(old=OLD, new=NEW, name="pkg/app.py"):
    return "".join(difflib.unified_diff(old.splitlines(True), new.splitlines(True),
                                       fromfile="a/" + name, tofile="b/" + name))


class PatchOriginTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "repo/xarray/pkg").mkdir(parents=True)
        (self.root / "repo/xarray/.git").mkdir()
        (self.root / "requirement").mkdir()
        (self.root / "requirement/user_chat.txt").write_text("Fix operation")
        self.source = self.root / "repo/xarray/pkg/app.py"
        self.source.write_text(NEW)
        self.workspace = WorkspaceTools(self.root, LocalExecutor(LocalExecutionPolicy()))

    def test_previous_run_git_patch_is_a_noop_against_new_run_baseline(self):
        before = self.source.read_bytes()
        selected, applied = self.workspace.select_coder_patch(patch(), git_cwds=[str(self.source.parent)])
        self.assertTrue(applied)
        self.assertIn("a/xarray/pkg/app.py", selected)
        self.assertEqual(self.workspace.current_patch(), "")
        self.assertEqual(self.workspace.coder_paths, set())
        self.assertEqual(self.workspace.code_edit_count, 0)
        self.assertEqual(self.source.read_bytes(), before)

    def test_nested_patch_only_change_uses_observed_git_root(self):
        self.source.write_text(OLD)
        workspace = WorkspaceTools(self.root, self.workspace.executor)
        selected, applied = workspace.select_coder_patch(patch(), git_cwds=[str(self.source.parent)])
        self.assertFalse(applied)
        workspace.apply_patch(selected, base="baseline")
        self.assertEqual(self.source.read_text(), NEW)
        self.assertEqual(workspace.coder_paths, {"xarray/pkg/app.py"})
        self.assertIn("xarray/pkg/app.py", workspace.current_patch())

    def test_never_guesses_a_prefix_without_observed_git_directory(self):
        for directories in ([], [str(self.root.parent)]):
            with self.subTest(directories=directories), self.assertRaises(ValueError):
                self.workspace.select_coder_patch(patch(), git_cwds=directories)
        self.assertEqual(self.source.read_text(), NEW)

    def test_wrong_new_content_and_external_modifications_are_rejected(self):
        wrong = patch(OLD, "def operation():\n    return 999\n")
        with self.assertRaises(ValueError):
            self.workspace.select_coder_patch(wrong, git_cwds=[str(self.source.parent)])
        self.source.write_text(NEW + "# outside this Coder\n")
        repeated = patch(OLD, self.source.read_text())
        with self.assertRaises(ValueError):
            self.workspace.select_coder_patch(repeated, git_cwds=[str(self.source.parent)])

    def test_multiple_valid_roots_are_rejected_before_any_write(self):
        self.source.write_text(OLD)
        (self.root / "repo/pkg").mkdir()
        (self.root / "repo/pkg/app.py").write_text(OLD)
        workspace = WorkspaceTools(self.root, self.workspace.executor)
        with self.assertRaises(ValueError):
            workspace.select_coder_patch(patch(), git_cwds=[str(self.source.parent)])
        self.assertEqual(self.source.read_text(), OLD)
        self.assertEqual((self.root / "repo/pkg/app.py").read_text(), OLD)

    def test_rebase_only_changes_headers_even_when_hunk_looks_like_headers(self):
        original = "-- a/old\nold\n"
        updated = "++ b/new\nnew\n"
        rebased = rebase_repo_patch(patch(original, updated), "xarray")
        self.assertEqual(apply_repo_patch({"xarray/pkg/app.py": original}, rebased),
                         {"xarray/pkg/app.py": updated})
        self.assertIn("--- a/old\n", rebased)
        self.assertIn("+++ b/new\n", rebased)
        with self.assertRaises(ValueError):
            rebase_repo_patch(patch(), "../outside")

    def test_only_successful_git_diff_observations_supply_origins(self):
        tools = ToolOrchestrator(self.workspace)
        context = {"agent_call_id": "coder-1"}
        tools._evidence["coder-1"] = [
            {"command": ["git", "diff"], "status": "completed", "exit_code": 0, "cwd": str(self.source.parent)},
            {"command": ["git", "diff"], "status": "completed", "exit_code": 1, "cwd": "wrong"},
            {"command": ["git", "status"], "status": "completed", "exit_code": 0, "cwd": "other"}]
        self.assertEqual(tools.git_diff_directories(context), [str(self.source.parent)])
        self.assertEqual(tools.git_diff_directories({"agent_call_id": "other"}), [])
