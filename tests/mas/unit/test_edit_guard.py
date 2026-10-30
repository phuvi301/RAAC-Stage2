"""File tools refuse writes that make a file worse: broken Python or leaked tool-call markup."""

from pathlib import Path
import tempfile
import unittest

from raac.common.errors import RejectedEditError
from raac.harness.layer3_execution.tool_errors import describe_tool_error
from raac.harness.layer3_execution.tools.workspace import WorkspaceTools

GARBAGE = "\n ခ}יי to=functions.run_commands? Let's call.#+#+assistant to=functions.replace_text Japgolly{"


class EditGuardTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.project = Path(temp.name)
        self.repo = self.project / "repo"
        (self.repo / "pkg").mkdir(parents=True)
        (self.project / "requirement").mkdir()
        (self.project / "requirement/user_chat.txt").write_text("Fix the project", encoding="utf-8")
        (self.repo / "pkg/core.py").write_text("def value():\n    return 1\n", encoding="utf-8")
        (self.repo / "pkg/legacy.py").write_text("print 'python 2 fixture'\n", encoding="utf-8")
        (self.repo / "notes.md").write_text("# Notes\n", encoding="utf-8")
        self.tools = WorkspaceTools(self.project, None)

    def read(self, name):
        return (self.repo / name).read_text(encoding="utf-8")

    def test_edit_that_breaks_python_is_refused_and_nothing_is_written(self):
        with self.assertRaises(RejectedEditError) as raised:
            self.tools.edit_file(role="Coder", path="pkg/core.py", content="def value():\nreturn 1\n")
        self.assertEqual((raised.exception.reason, raised.exception.line), ("invalid_python", 2))
        self.assertEqual(self.read("pkg/core.py"), "def value():\n    return 1\n")
        self.assertNotIn("pkg/core.py", self.tools.coder_paths)

    def test_replace_text_that_appends_tool_markup_is_refused(self):
        with self.assertRaises(RejectedEditError) as raised:
            self.tools.replace_text(role="Coder", path="pkg/core.py", old_text="    return 1\n",
                                    new_text="    return 2\n" + GARBAGE)
        self.assertEqual(raised.exception.reason, "tool_markup")
        self.assertEqual(self.read("pkg/core.py"), "def value():\n    return 1\n")

    def test_valid_edits_still_work(self):
        self.tools.replace_text(role="Coder", path="pkg/core.py", old_text="return 1", new_text="return 2")
        self.tools.edit_file(role="Coder", path="pkg/new.py", content="X = 1\n")
        self.assertEqual(self.read("pkg/core.py"), "def value():\n    return 2\n")
        self.assertEqual(self.read("pkg/new.py"), "X = 1\n")

    def test_new_python_file_must_parse(self):
        with self.assertRaises(RejectedEditError):
            self.tools.edit_file(role="Coder", path="pkg/new.py", content="def broken(:\n")
        self.assertFalse((self.repo / "pkg/new.py").exists())

    def test_a_file_that_never_parsed_can_still_be_edited(self):
        self.tools.edit_file(role="Coder", path="pkg/legacy.py", content="print 'still python 2'\n")
        self.assertEqual(self.read("pkg/legacy.py"), "print 'still python 2'\n")

    def test_tool_markup_is_refused_in_any_file_but_existing_text_is_allowed(self):
        with self.assertRaises(RejectedEditError):
            self.tools.edit_file(role="Coder", path="notes.md", content="# Notes\n<|im_end|>\n")
        (self.repo / "docs.md").write_text("Example token: to=functions.search\n", encoding="utf-8")
        tools = WorkspaceTools(self.project, None)
        tools.edit_file(role="Coder", path="docs.md", content="Example token: to=functions.search\nMore text\n")
        self.assertIn("More text", self.read("docs.md"))

    def test_a_patch_with_one_broken_file_writes_nothing(self):
        patch = ("--- a/notes.md\n+++ b/notes.md\n@@ -1 +1,2 @@\n # Notes\n+added\n"
                 "--- a/pkg/core.py\n+++ b/pkg/core.py\n@@ -1,2 +1,2 @@\n def value():\n-    return 1\n+return 1\n")
        with self.assertRaises(RejectedEditError):
            self.tools.apply_patch(patch)
        self.assertEqual(self.read("notes.md"), "# Notes\n")
        self.assertEqual(self.read("pkg/core.py"), "def value():\n    return 1\n")

    def test_the_model_is_told_why_and_where(self):
        result = describe_tool_error(RejectedEditError("invalid_python", "pkg/core.py", 2))
        self.assertEqual((result["error_kind"], result["path"], result["line"]), ("InvalidPython", "pkg/core.py", 2))
        self.assertIn("No file was written", result["error"])
        markup = describe_tool_error(RejectedEditError("tool_markup", "pkg/core.py"))
        self.assertEqual(markup["error_kind"], "ToolMarkupInContent")
        with self.assertRaises(ValueError):
            RejectedEditError("unknown", "x")
