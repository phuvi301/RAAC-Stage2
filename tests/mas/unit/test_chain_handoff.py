"""Chain controls role visibility independently of the runtime and context compaction."""

from copy import deepcopy
import json
import unittest

from raac.collaboration.levels.l3_collaboration.chain_handoff import prepare_role_request


class ChainHandoffTests(unittest.TestCase):
    def test_handoff_views_keep_contracts_and_authoritative_task_data_without_mutating_audit(self):
        plan = {"steps": [{"id": "S1", "ac_ids": ["AC1"], "target": "src/service.py::run",
                           "approach": "IMPLEMENTATION_ONLY " * 100, "complexity": "O(n)",
                           "risks": ["Boundary overflow"]}],
                "interface_contracts": {"result": "Return a sorted list"}, "covered_ac_ids": ["AC1"]}
        request = {"requirement": "Keep the ENTIRE user chat", "constraints": ["No new dependencies"],
                   "acceptance_criteria": [{"id": "AC1", "description": "Exact inherited AC"}],
                   "artifacts": {"spec": {"summary": "Compatibility facts", "acceptance_criteria": [
                       {"id": "AC1", "description": "Exact inherited AC"}], "constraints": ["Preserve API"]},
                       "plan": plan, "patch": "full diff", "patch_hash": "revision",
                       "test_report": {"blocking_issues": ["Unresolved boundary"]},
                       "coder_evidence": [{"exit_code": 1}], "coder_summary": "Known blocker"},
                   "incoming_message": {"from": "Planner", "content": plan}}
        original = deepcopy(request)
        coder = prepare_role_request(request, role="Coder")
        tester = prepare_role_request(request, role="Tester")
        reviewer = prepare_role_request(request, role="Reviewer")
        self.assertEqual(coder["artifacts"]["plan"], plan)
        self.assertEqual(tester["artifacts"]["plan"], {"interface_contracts": plan["interface_contracts"]})
        self.assertNotIn("IMPLEMENTATION_ONLY", json.dumps(tester))
        self.assertEqual(reviewer["artifacts"]["plan"]["decisions"][0]["risks"], ["Boundary overflow"])
        self.assertEqual(reviewer["artifacts"]["plan"]["decisions"][0]["complexity"], "O(n)")
        self.assertNotIn("steps", reviewer["artifacts"]["plan"])
        for view in (coder, tester, reviewer):
            for field in ("constraints", "acceptance_criteria"):
                self.assertEqual(view[field], original[field])
            for artifact in ("spec", "test_report", "coder_evidence", "coder_summary", "patch_hash", "patch"):
                self.assertEqual(view["artifacts"][artifact], original["artifacts"][artifact])
            self.assertEqual(view["incoming_message"]["content_ref"], "artifacts.plan")
        self.assertNotIn("requirement", coder)
        self.assertNotIn("requirement", tester)
        self.assertEqual(reviewer["requirement"], original["requirement"])
        self.assertEqual(request, original)
        self.assertLess(len(json.dumps(tester)), len(json.dumps(coder)) // 2)

    def test_compaction_toggle_does_not_change_chain_visibility(self):
        from pathlib import Path
        from raac.registry.loader import load_registry
        from raac.harness.layer2_core.prompt_hydrator import hydrate_messages
        role = load_registry(Path(__file__).resolve().parents[3] / "registry").get("Tester")
        request = {"requirement": "Keep task", "artifacts": {"plan": {"steps": [
            {"id": "S1", "ac_ids": ["AC1"], "target": "service", "approach": "Design choice"}],
            "interface_contracts": {}}}}
        prepared = prepare_role_request(request, role="Tester")
        raw = json.loads(hydrate_messages(role, prepared, compact=False)[1]["content"])
        compact = json.loads(hydrate_messages(role, prepared)[1]["content"])
        self.assertEqual(raw, prepared)
        self.assertEqual(raw["artifacts"]["plan"], {"interface_contracts": {}})
        self.assertEqual(compact["artifacts"]["plan"], {"interface_contracts": {}})
        for recipient in ("Coder", "Tester", "Reviewer"):
            view = prepare_role_request(request, role=recipient)
            self.assertEqual(prepare_role_request(view, role=recipient), view)

    def test_legacy_review_plan_is_preserved_when_decisions_cannot_be_projected(self):
        request = {"artifacts": {"plan": {"approach": "Legacy design", "steps": ["Inspect risk"]}}}
        self.assertEqual(prepare_role_request(request, role="Reviewer"), request)

    def test_raw_chat_only_reaches_analyst_and_reviewer_even_with_compaction_disabled(self):
        from pathlib import Path
        from raac.registry.loader import load_registry
        from raac.harness.layer2_core.prompt_hydrator import hydrate_messages
        registry = load_registry(Path(__file__).resolve().parents[3] / "registry")
        spec = {"summary": "Implement add", "acceptance_criteria": [
            {"id": "AC1", "description": "Return mathematical sum"}],
            "constraints": ["O(1)"], "interface_contracts": {"add": "add(a: int, b: int) -> int"}}
        request = {"requirement": "RAW_CHAT_ONLY_MARKER", "artifacts": {"spec": spec},
                   "incoming_message": {"from": "caller", "content": "RAW_CHAT_ONLY_MARKER",
                                        "requirement": "RAW_CHAT_ONLY_MARKER", "requirement_ref": "requirement"}}
        original = deepcopy(request)
        for recipient in ("Analyst", "Planner", "Coder", "Tester", "Reviewer"):
            for enabled in (True, False):
                with self.subTest(role=recipient, compact=enabled):
                    messages = hydrate_messages(registry.get(recipient), prepare_role_request(request, role=recipient), compact=enabled)
                    payload = json.loads(messages[1]["content"])
                    self.assertEqual(payload["artifacts"]["spec"], spec)
                    if recipient in {"Analyst", "Reviewer"}:
                        self.assertEqual(payload["requirement"], "RAW_CHAT_ONLY_MARKER")
                    else:
                        self.assertNotIn("RAW_CHAT_ONLY_MARKER", json.dumps(payload))
                        self.assertNotIn("requirement", payload)
                        self.assertEqual(payload["incoming_message"]["content_ref"], "artifacts.spec")
                        self.assertEqual(payload["incoming_message"]["requirement_ref"], "artifacts.spec")
        self.assertEqual(request, original)

    def test_role_selection_is_idempotent_and_does_not_leave_missing_requirement_references(self):
        request = {"requirement": "raw chat", "incoming_message": {
            "from": "caller", "requirement": "raw chat", "content_ref": "requirement"}}
        original = deepcopy(request)
        for recipient in ("Analyst", "Planner", "Coder", "Tester", "Reviewer"):
            view = prepare_role_request(request, role=recipient)
            self.assertEqual(prepare_role_request(view, role=recipient), view)
            if recipient not in {"Analyst", "Reviewer"}:
                self.assertEqual(view, {"incoming_message": {"from": "caller"}})
        self.assertEqual(request, original)

    def test_tester_uses_changed_file_references_while_reviewer_keeps_full_patch(self):
        patch = "full source diff\n" * 1000
        request = {"requirement": "Full user task", "repository_changes": [
            {"path": "src/app.py", "deleted": False}, {"path": "old.py", "deleted": True}],
            "artifacts": {"patch": patch, "patch_hash": "revision", "spec": {"acceptance_criteria": ["AC1"]}},
            "incoming_message": {"from": "Coder", "patch": patch, "patch_hash": "revision"}}
        tester = prepare_role_request(request, role="Tester")
        self.assertNotIn("patch", tester["artifacts"])
        self.assertEqual(tester["artifacts"]["patch_hash"], "revision")
        self.assertEqual(tester["artifacts"]["spec"], request["artifacts"]["spec"])
        self.assertEqual(tester["incoming_message"]["patch_ref"], "artifacts.patch_context")
        self.assertEqual(tester["repository_changes"], request["repository_changes"])
        self.assertEqual(prepare_role_request(request, role="Reviewer")["artifacts"]["patch"], patch)
        without_paths = {**request, "repository_changes": []}
        self.assertEqual(prepare_role_request(without_paths, role="Tester")["artifacts"]["patch"], patch)
