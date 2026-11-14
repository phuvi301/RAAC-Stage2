"""apply_patch accepts the patch shapes models actually send and explains a refusal."""

import tempfile
import unittest
from pathlib import Path

from raac.common.errors import PatchFormatError
from raac.harness.layer3_execution.tool_errors import describe_tool_error
from raac.harness.layer3_execution.tools.patching import (
    apply_repo_patch,
    apply_tool_patch,
    recount_hunks,
)
from raac.harness.layer3_execution.tools.workspace import WorkspaceTools

CORE = "def area(w, h):\n    return w + h\n\n\ndef name():\n    return 'box'\n"


class ApplyPatchFormatTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name)
        self.repo = self.project / "repo"
        self.repo.mkdir()
        (self.project / "requirement").mkdir()
        (self.project / "requirement/user_chat.txt").write_text("Change the project", encoding="utf-8")
        (self.repo / "core.py").write_text(CORE, encoding="utf-8")
        (self.repo / "old.txt").write_text("gone\n", encoding="utf-8")
        self.tools = WorkspaceTools(self.project, None)

    def read(self, name):
        return (self.repo / name).read_text(encoding="utf-8")

    def test_begin_patch_envelope_adds_updates_and_deletes(self):
        patch = ("*** Begin Patch\n"
                 "*** Add File: js/app.js\n+const a = 1;\n+\n+module.exports = a;\n"
                 "*** Update File: core.py\n@@ def area(w, h):\n-    return w + h\n+    return w * h\n"
                 "@@\n def name():\n-    return 'box'\n+    return 'rect'\n"
                 "*** Delete File: old.txt\n"
                 "*** End Patch trailing words\n")
        self.assertEqual(self.tools.apply_patch(patch)["paths"], ["core.py", "js/app.js", "old.txt"])
        self.assertEqual(self.read("js/app.js"), "const a = 1;\n\nmodule.exports = a;\n")
        self.assertEqual(self.read("core.py"), CORE.replace("w + h", "w * h").replace("box", "rect"))
        self.assertFalse((self.repo / "old.txt").exists())
        # The run's candidate diff is still derived from the files, in the strict format.
        self.assertEqual(apply_repo_patch(self.tools._baseline_text(), self.tools.current_patch())["js/app.js"],
                         "const a = 1;\n\nmodule.exports = a;\n")

    def test_miscounted_hunks_are_recounted_but_content_must_still_match(self):
        # Wrong counts on both files, and an empty context line without its leading space.
        patch = ("--- /dev/null\n+++ b/package.json\n@@ -0,0 +1,9 @@\n+{\n+  \"name\": \"x\"\n+}\n"
                 "--- a/core.py\n+++ b/core.py\n@@ -1,3 +1,3 @@\n def area(w, h):\n-    return w + h\n+    return w * h\n\n\n def name():\n")
        with self.assertRaises(ValueError):
            apply_repo_patch({"core.py": CORE}, patch)
        self.tools.apply_patch(patch)
        self.assertEqual(self.read("package.json"), "{\n  \"name\": \"x\"\n}\n")
        self.assertEqual(self.read("core.py"), CORE.replace("w + h", "w * h"))
        stale = "--- a/core.py\n+++ b/core.py\n@@ -1,9 +1,9 @@\n def area(w, h):\n-    return w - h\n+    return 0\n"
        with self.assertRaises(PatchFormatError) as raised:
            self.tools.apply_patch(stale)
        self.assertEqual(str(raised.exception), "Patch does not match original baseline")

    def test_recount_keeps_correct_patches_and_file_boundaries(self):
        patch = ("--- a/a.txt\n+++ b/a.txt\n@@ -1 +1 @@\n-- old\n+- new\n"
                 "--- a/b.txt\n+++ b/b.txt\n@@ -1,1 +1,1 @@\n-x\n+y\n\\ No newline at end of file\n")
        baseline = {"a.txt": "- old\n", "b.txt": "x\n"}
        self.assertEqual(apply_repo_patch(baseline, recount_hunks(patch)), apply_repo_patch(baseline, patch))
        self.assertEqual(apply_tool_patch(baseline, patch), {"a.txt": "- new\n", "b.txt": "y"})

    def test_refusal_writes_nothing_and_names_the_reason(self):
        with self.assertRaisesRegex(PatchFormatError, "Missing patch terminator"):
            self.tools.apply_patch("*** Begin Patch\n*** Delete File: old.txt\n")
        self.assertEqual(self.read("old.txt"), "gone\n")
        patch = ("*** Begin Patch\n*** Add File: new.txt\n+ok\n"
                 "*** Update File: core.py\n@@\n-    return missing\n+    return 1\n*** End Patch\n")
        with self.assertRaises(PatchFormatError) as raised:
            self.tools.apply_patch(patch)
        self.assertFalse((self.repo / "new.txt").exists())
        self.assertEqual(self.read("core.py"), CORE)
        observation = describe_tool_error(raised.exception)
        self.assertEqual(observation["error_kind"], "InvalidPatch")
        self.assertIn("Patch does not match original baseline", observation["error"])
        self.assertIn("--- /dev/null", observation["error"])
        for bad, kind in (("*** Begin Patch\n*** Update File: core.py\n*** Move to: other.py\n@@\n-x\n+y\n*** End Patch", "InvalidPatch"),
                          ("*** Begin Patch\n*** Add File: ../escape.txt\n+x\n*** End Patch", "PathNotAllowed"),
                          ("not a patch", "InvalidPatch")):
            with self.assertRaises(PatchFormatError) as raised:
                self.tools.apply_patch(bad)
            self.assertEqual(describe_tool_error(raised.exception)["error_kind"], kind, bad)


if __name__ == "__main__":
    unittest.main()
