"""scripts/local_grade.py reports only the benchmark's graded tests, like the evaluator."""

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "local_grade.py"
spec = importlib.util.spec_from_file_location("local_grade", SCRIPT)
local_grade = importlib.util.module_from_spec(spec)
spec.loader.exec_module(local_grade)


def test_django_output_maps_plain_and_docstring_tests():
    output = "\n".join([
        "test_a (timezones.tests.NewDatabaseTests) ... ok",
        "test_b (timezones.tests.TemplateTests)",
        "Test the {% timezone %} templatetag. ... skipped 'Windows'",
        "test_c (timezones.tests.SerializationTests) ... FAIL",
    ])
    results = local_grade.django_results(output)
    assert results["test_a (timezones.tests.NewDatabaseTests)"] == "passed"
    assert results["test_b (timezones.tests.TemplateTests)"] == "skipped"  # named by its docstring in the output
    assert results["Test the {% timezone %} templatetag."] == "skipped"
    groups = local_grade.grade(["test_a (timezones.tests.NewDatabaseTests)", "test_z (x.Y)"], results)
    assert groups["passed"] == ["test_a (timezones.tests.NewDatabaseTests)"] and groups["missing"] == ["test_z (x.Y)"]
    # An ungraded failure in the same module does not count.
    assert local_grade.grade(["test_a (timezones.tests.NewDatabaseTests)"], results)["failed"] == []


def test_pytest_output_maps_node_ids_and_bare_function_names():
    output = "\n".join([
        "astropy/units/tests/test_format.py::test_cds_grammar_fail[km/s.Mpc-1] FAILED  [ 10%]",
        "sympy/core/tests/test_assumptions.py::test_infinity PASSED [ 20%]",
        "sympy/core/tests/test_assumptions.py::test_skip SKIPPED (no numpy) [ 30%]",
    ])
    results = local_grade.pytest_results(output)
    assert results["astropy/units/tests/test_format.py::test_cds_grammar_fail[km/s.Mpc-1]"] == "failed"
    assert results["test_infinity"] == "passed"
    assert results["test_skip"] == "skipped"


def test_commands_follow_the_evaluator_roots():
    row = {"repo": "django/django", "test_patch": "+++ b/tests/timezones/tests.py\n",
           "FAIL_TO_PASS": ["test_x (timezones.tests.NewDatabaseTests)"], "PASS_TO_PASS": []}
    assert local_grade.django_command(row)[-1] == "timezones.tests"
    row = {"repo": "sympy/sympy", "test_patch": "+++ b/sympy/core/tests/test_assumptions.py\n",
           "FAIL_TO_PASS": ["test_infinity"], "PASS_TO_PASS": []}
    assert local_grade.pytest_command(row)[-1] == "sympy/core/tests/test_assumptions.py"
