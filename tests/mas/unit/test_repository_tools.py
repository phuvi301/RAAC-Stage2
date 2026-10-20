"""Generic repository layouts, multi-file changes and per-run command selection."""

from pathlib import Path
import tempfile
import unittest

from raac.harness.layer3_execution.tools.patching import apply_repo_patch
from raac.harness.layer3_execution.tools.workspace import WorkspaceTools


class Executor:
    async def run_tests(self, root, **spec):
        self.files = {path.relative_to(root).as_posix(): path.read_bytes() for path in root.rglob("*") if path.is_file()}
        self.spec = spec
        return {"status": "passed", "tests_run": 1, "failed": 0,
                "test_cases": ["custom.check"], "summary": "fixture"}


class RepositoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name)
        self.repo = self.project / "repo"
        (self.repo / "src/pkg").mkdir(parents=True)
        (self.project / "requirement").mkdir()
        (self.project / "requirement/user_chat.txt").write_text("Fix the existing project", encoding="utf-8")
        (self.repo / "src/pkg/core.py").write_text("value = 1\n", encoding="utf-8")
        (self.repo / "obsolete.txt").write_text("old", encoding="utf-8")
        self.executor = Executor()
        self.tools = WorkspaceTools(self.project, self.executor)

    async def test_discovery_and_recursive_search_without_fixed_filenames(self):
        listing = self.tools.list_files()
        self.assertIn("src/pkg/core.py", {row["path"] for row in listing["files"]})
        self.assertEqual(self.tools.search_repo("value")["matches"][0]["path"], "src/pkg/core.py")
        self.assertEqual(self.tools.read_file("src/pkg/core.py")["content"], "value = 1\n")
        for name in ("../outside", "src/../../outside", "C:/outside", "/tmp/outside", ".env", ".git/config",
                     ".GIT/config", ".env ", "NUL.txt", ".npmrc"):
            with self.subTest(path=name), self.assertRaises(ValueError):
                self.tools.edit_file(role="Coder", path=name, content="forbidden")

    async def test_patch_covers_nested_edit_creation_empty_file_deletion_and_eof(self):
        self.tools.edit_file(role="Coder", path="src/pkg/core.py", content="value = 2\n")
        self.tools.edit_file(role="Coder", path="new folder/feature.py", content="enabled = True")
        self.tools.edit_file(role="Coder", path="new folder/__init__.py", content="")
        self.tools.delete_file(role="Coder", path="obsolete.txt")
        self.tools.edit_file(role="Tester", path="verification/check.py", content="assert True\n")
        patch = self.tools.current_patch()
        changes = apply_repo_patch(self.tools._baseline_text(), patch)
        self.assertEqual(changes["src/pkg/core.py"], "value = 2\n")
        self.assertEqual(changes["new folder/feature.py"], "enabled = True")
        self.assertEqual(changes["new folder/__init__.py"], "")
        self.assertIsNone(changes["obsolete.txt"])
        self.assertNotIn("verification/check.py", changes)
        self.assertIn("\\ No newline at end of file", patch)

    async def test_bad_second_file_is_rejected_before_first_file_changes(self):
        patch = ("--- a/src/pkg/core.py\n+++ b/src/pkg/core.py\n@@ -1 +1 @@\n-value = 1\n+value = 2\n"
                 "--- a/obsolete.txt\n+++ b/obsolete.txt\n@@ -1 +1 @@\n-wrong\n+new\n")
        with self.assertRaises(ValueError):
            self.tools.apply_patch(patch)
        self.assertEqual(self.tools.read_file("src/pkg/core.py")["content"], "value = 1\n")

    async def test_incremental_tool_patches_produce_a_cumulative_result(self):
        for old, new in ((1, 2), (2, 3)):
            self.tools.apply_patch(f"--- a/src/pkg/core.py\n+++ b/src/pkg/core.py\n@@ -1 +1 @@\n-value = {old}\n+value = {new}\n")
        result = apply_repo_patch(self.tools._baseline_text(), self.tools.current_patch())
        self.assertEqual(result["src/pkg/core.py"], "value = 3\n")

    async def test_command_and_environment_repeat_only_inside_this_run(self):
        self.tools.edit_file(role="Tester", path="verification/check.py", content="assert True\n")
        command = ["python", "check.py"]
        await self.tools.run_tests(command=command, cwd="verification", env={"PYTHONPATH": "../src"})
        await self.tools.run_tests()
        self.assertEqual(self.executor.spec["command"], command)
        self.assertEqual(self.executor.spec["cwd"], "verification")
        self.assertIn("src/pkg/core.py", self.executor.files)
        with self.assertRaises(PermissionError):
            self.tools.edit_file(role="Tester", path="src/pkg/core.py", content="cheat")
        next_run = WorkspaceTools(self.project, self.executor)
        self.assertIsNone(next_run.test_spec)
        self.assertEqual((await next_run.run_tests())["status"], "error")

    async def test_snapshot_omits_credentials_and_dependency_caches_but_keeps_assets(self):
        (self.repo / ".env").write_text("DO_NOT_UPLOAD", encoding="utf-8")
        (self.repo / "node_modules").mkdir()
        (self.repo / "node_modules/ignored.js").write_text("dependency cache", encoding="utf-8")
        (self.repo / "asset.bin").write_bytes(b"\0\xff\x01")
        snapshot = self.tools._snapshot()
        self.assertNotIn(".env", snapshot)
        self.assertNotIn("node_modules/ignored.js", snapshot)
        self.assertEqual(snapshot["asset.bin"], b"\0\xff\x01")

    async def test_empty_repository_is_valid_input(self):
        (self.repo / "src/pkg/core.py").unlink()
        (self.repo / "obsolete.txt").unlink()
        workspace = WorkspaceTools(self.project, self.executor)
        self.assertEqual(workspace.list_files()["files"], [])
        workspace.edit_file(role="Coder", path="web/index.html", content="<main>Hello</main>")
        self.assertIn("web/index.html", workspace.current_patch())
