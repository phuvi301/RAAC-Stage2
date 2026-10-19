import io
import unittest

from raac.harness.layer3_execution.sandbox import issue_runner as runner


def test_subtest_description_failure_does_not_crash_reporting(tmp_path, monkeypatch):
    class Unprintable:
        calls = 0

        def __repr__(self):
            Unprintable.calls += 1
            raise RuntimeError("query failed while formatting subtest parameter")

    class Sample(unittest.TestCase):
        def test_issue(self):
            with self.subTest(query=Unprintable()):
                self.assertEqual(0, 1)

        def test_stable(self):
            self.assertEqual(1, 1)

    ids = [Sample(name).id() for name in ("test_issue", "test_stable")]
    monkeypatch.setattr(runner, "REPORT", str(tmp_path / "report.json"))
    monkeypatch.setattr(
        runner, "spec", {"ids": ids, "command": ["python", "fixture.py"]}
    )
    for name, value in [
        ("results", {}),
        ("found", []),
        ("errors", []),
        ("selection_errors", {}),
    ]:
        monkeypatch.setattr(runner, name, value)
    monkeypatch.setattr(runner.sys, "argv", list(runner.sys.argv))
    monkeypatch.setattr(runner.sys, "path", list(runner.sys.path))
    # run_django wraps unittest hooks; ensure every mutation is restored after this test.
    for name in (
        "addSuccess",
        "addFailure",
        "addError",
        "addSkip",
        "addExpectedFailure",
        "addUnexpectedSuccess",
        "addSubTest",
        "getDescription",
    ):
        monkeypatch.setattr(
            unittest.TextTestResult, name, getattr(unittest.TextTestResult, name)
        )
    monkeypatch.setattr(unittest.TextTestRunner, "run", unittest.TextTestRunner.run)
    stream = io.StringIO()
    original_description = unittest.TextTestResult.getDescription

    def legacy_description(self, test):
        # Python 3.9 directly formats subtest parameters with repr; newer
        # unittest versions protect this internally with safe_repr.
        if hasattr(test, "test_case"):
            return test.test_case.id() + " " + repr(test.params["query"])
        return original_description(self, test)

    monkeypatch.setattr(unittest.TextTestResult, "getDescription", legacy_description)

    def run_fixture(*args, **kwargs):
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(Sample)
        result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
        assert not result.wasSuccessful()
        assert len(result.failures) == 1
        assert not result.errors

    monkeypatch.setattr(runner.runpy, "run_path", run_fixture)
    runner.run_django()
    assert runner.results[ids[0]]["outcome"] == "failed"
    assert runner.results[ids[1]]["outcome"] == "passed"
    assert "AssertionError" in stream.getvalue()
    assert Unprintable.calls == 0
