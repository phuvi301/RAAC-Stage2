"""Commit 39d39e7 formats and the additional L3 coverage/revision contract."""
from copy import deepcopy
import json
import unittest

from raac.collaboration.levels.l3_collaboration.chain_state import (
    AnalystOutput, PlannerOutput, CoderOutput, TesterOutput, ReviewerOutput,
    JSON_OUTPUT_MODELS, RoleHandoff, OutputFormatError, parse_role_output, role_output_model,
)
from raac.registry import parse_role_output as parse_registry_output


class ChainOutputTests(unittest.TestCase):
    def setUp(self):
        self.outputs = {
            "analyst": {"summary": "Implement add", "requirements": [
                {"id": "R1", "description": "Return sum", "priority": "high"}],
                "acceptance_criteria": ["add(2, 3) returns 5"], "constraints": [], "open_questions": []},
            "planner": {"steps": [{"id": 1, "description": "Implement add", "files": ["app.py"], "depends_on": []}]},
            "tester": {"tests_to_run": ["test_add"], "results": [
                {"test": "test_add", "status": "passed", "details": ""}], "verdict": "pass"},
            "reviewer": {"decision": "approve", "summary": "Reviewed", "comments": []},
        }
        self.patch = "--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n-old\n+new\n"

    def test_registry_api_and_chain_share_json_contract(self):
        for role, payload in self.outputs.items():
            original = deepcopy(payload)
            commit = parse_registry_output(role, json.dumps(payload)).model_dump()
            for name in (role, role.title()):
                for value in (payload, json.dumps(payload)):
                    with self.subTest(role=name, encoding=type(value).__name__):
                        parsed = parse_role_output(name, value)
                        self.assertIsInstance(parsed, role_output_model(name))
                        for key in commit:
                            actual = parsed.model_dump()[key]
                            if key == "steps":
                                actual = [{field: step[field] for field in expected} for step, expected in zip(actual, commit[key])]
                            self.assertEqual(actual, commit[key])
            self.assertEqual(payload, original)

    def test_coder_uses_unified_diff_instead_of_json(self):
        self.assertNotIn("coder", JSON_OUTPUT_MODELS)
        self.assertIsInstance(parse_role_output("Coder", self.patch), CoderOutput)
        self.assertEqual(parse_role_output("coder", self.patch).root, self.patch)
        self.assertEqual(role_output_model("Coder").model_json_schema()["type"], "string")
        for invalid in ({"patch": self.patch, "summary": "saved"}, json.dumps({"summary": "saved"}), self.patch.replace("@@ -1 +1 @@", "@@ -5,4 +5,4 @@")):
            with self.subTest(invalid=invalid), self.assertRaises(OutputFormatError):
                parse_role_output("coder", invalid)

    def test_noop_has_no_fabricated_diff(self):
        self.assertEqual(parse_role_output("coder", "").root, "")

    def test_l3_coverage_extensions_are_explicit(self):
        payload = deepcopy(self.outputs["planner"])
        payload["steps"][0]["ac_ids"] = ["AC1"]
        payload["covered_ac_ids"] = ["AC1"]
        payload["interface_contracts"] = {"add": "add(a, b)"}
        self.assertEqual(parse_role_output("Planner", payload).model_dump(), payload)
        # Absence is not silently promoted to coverage. ChainNodes rejects it.
        self.assertEqual(parse_role_output("planner", self.outputs["planner"]).covered_ac_ids, [])

    def test_unknown_role_does_not_fall_back(self):
        for role in ("manager", "", None):
            with self.subTest(role=role), self.assertRaises(OutputFormatError):
                parse_role_output(role, self.outputs["analyst"])

    def test_json_roles_reject_non_objects_and_fences(self):
        for value in ("bad JSON", "[]", "null", [], None, "```json\n{}\n```"):
            with self.subTest(value=value), self.assertRaises(OutputFormatError):
                parse_role_output("analyst", value)

    def test_analyst_requires_unique_requirement_ids_and_nonempty_ac(self):
        for update in ({"requirements": []}, {"requirements": self.outputs["analyst"]["requirements"] * 2}, {"acceptance_criteria": []}):
            with self.subTest(update=update), self.assertRaises(OutputFormatError):
                parse_role_output("analyst", {**self.outputs["analyst"], **update})

    def test_planner_keeps_integer_sequence_and_prior_dependencies(self):
        valid = {"steps": [self.outputs["planner"]["steps"][0],
                           {"id": 2, "description": "Test add", "files": ["tests/test_add.py"], "depends_on": [1]}]}
        self.assertEqual(parse_role_output("planner", valid).steps[1].depends_on, [1])
        for steps in ([{**valid["steps"][0], "id": "1"}], [valid["steps"][0]] * 2,
                      [{**valid["steps"][0], "depends_on": [1]}],
                      [valid["steps"][0], {**valid["steps"][1], "depends_on": [3]}]):
            with self.subTest(steps=steps), self.assertRaises(OutputFormatError):
                parse_role_output("planner", {"steps": steps})

    def test_tester_verdict_must_match_results_including_skip(self):
        for status in ("failed", "error"):
            with self.subTest(status=status), self.assertRaises(OutputFormatError):
                parse_role_output("tester", {**self.outputs["tester"], "results": [{"test": "t", "status": status}]})
        skipped = {**self.outputs["tester"], "results": [{"test": "t", "status": "skipped"}]}
        self.assertEqual(parse_role_output("tester", skipped).verdict, "pass")
        with self.assertRaises(OutputFormatError):
            parse_role_output("tester", {**self.outputs["tester"], "verdict": "fail"})

    def test_reviewer_requires_rejection_comment_and_rejects_blocker_approval(self):
        invalid = [dict(decision="reject", summary="No reason", comments=[]),
                   dict(decision="approve", summary="Unsafe", comments=[{"severity": "blocker", "message": "Unsafe input"}])]
        for payload in invalid:
            with self.subTest(payload=payload), self.assertRaises(OutputFormatError):
                parse_role_output("reviewer", payload)
        report = parse_role_output("reviewer", {"decision": "reject", "summary": "Fix it",
            "comments": [{"file": "app.py", "line": 2, "severity": "major", "message": "Wrong output"}]})
        self.assertEqual(report.comments[0].line, 2)

    def test_runtime_counts_and_revision_cannot_be_authored_in_wire_json(self):
        for role in ("tester", "reviewer"):
            for key, value in (("patch_hash", "forged"), ("runtime", {"tests_run": 999})):
                with self.subTest(role=role, key=key), self.assertRaises(OutputFormatError):
                    parse_role_output(role, {**self.outputs[role], key: value})
        self.assertIsInstance(RoleHandoff(self.patch, {"execution_evidence": []}), RoleHandoff)

    def test_error_identifies_field_without_echoing_raw_value(self):
        payload = {"steps": [{"id": "SENSITIVE_OUTPUT_VALUE", "description": "Implement"}]}
        with self.assertRaises(OutputFormatError) as caught:
            parse_role_output("planner", json.dumps(payload))
        self.assertIn("steps.0.id", str(caught.exception))
        self.assertNotIn("SENSITIVE_OUTPUT_VALUE", str(caught.exception))
