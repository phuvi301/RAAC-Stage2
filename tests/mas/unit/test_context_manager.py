"""Context reduction preserves task contracts, tool errors and protocol pairs."""

from copy import deepcopy
import json
import hashlib
import unittest

from raac.harness.layer2_core.context_manager import (
    RoleContextPolicy, compact_request, compact_schema, prepare_messages, trim_checkpoint_receipts,
)


def exchange(name, args, observation, identifier="call-1"):
    return [{"role": "assistant", "content": None, "tool_calls": [
        {"id": identifier, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]},
        {"role": "tool", "tool_call_id": identifier, "content": json.dumps(observation)}]


class RoleContextTests(unittest.TestCase):
    def setUp(self):
        self.base = [{"role": "system", "content": "Role instructions"},
                     {"role": "user", "content": json.dumps({"requirement": "Keep every AC", "artifacts": {"patch_hash": "sha"}})}]
        self.policy = RoleContextPolicy()

    def test_only_identical_handoff_fields_become_references(self):
        spec = {"acceptance_criteria": [{"id": "AC1", "description": "Preserve exact contract"}]}
        request = {"requirement": "Full user request", "artifacts": {"spec": spec, "patch": "actual diff", "patch_hash": "sha"},
                   "incoming_message": {"from": "Coder", "content": spec, "patch": "actual diff", "patch_hash": "sha", "other": "retain me"}}
        original = deepcopy(request)
        reduced = compact_request(request)
        self.assertEqual(reduced["artifacts"], request["artifacts"])
        self.assertEqual(reduced["requirement"], request["requirement"])
        self.assertEqual(reduced["incoming_message"]["content_ref"], "artifacts.spec")
        self.assertEqual(reduced["incoming_message"]["patch_ref"], "artifacts.patch")
        self.assertEqual(reduced["incoming_message"]["other"], "retain me")
        self.assertEqual(request, original)
        analyst = compact_request({"requirement": "Full task", "incoming_message": {"from": "caller", "requirement": "Full task"}})
        self.assertEqual(analyst["incoming_message"]["requirement_ref"], "requirement")

    def test_hydrator_and_compaction_preserve_the_supplied_role_view(self):
        from pathlib import Path
        from raac.registry.loader import load_registry
        from raac.harness.layer2_core.prompt_hydrator import hydrate_messages
        registry = load_registry(Path(__file__).resolve().parents[3] / "registry")
        request = {"requirement": "Caller-selected raw text", "artifacts": {
            "plan": {"steps": [{"id": 1, "description": "Caller-selected implementation detail"}]},
            "patch": "Caller-selected full diff" * 200},
            "repository_changes": [{"path": "app.py", "deleted": False}]}
        original = deepcopy(request)
        for recipient in ("Analyst", "Planner", "Coder", "Tester", "Reviewer"):
            for enabled in (True, False):
                with self.subTest(role=recipient, compact=enabled):
                    payload = json.loads(hydrate_messages(registry.get(recipient), request, compact=enabled)[1]["content"])
                    self.assertEqual(payload, request)
        self.assertEqual(compact_request(request), request)
        self.assertEqual(request, original)

    def test_schema_removes_annotations_but_keeps_title_property_and_constraints(self):
        schema = {"title": "Work", "type": "object", "properties": {
            "title": {"title": "Title", "type": "string", "minLength": 1},
            "settings": {"type": "object", "default": {"title": "user data"}}},
            "required": ["title"], "additionalProperties": False}
        result = compact_schema(schema)
        self.assertNotIn("title", result)
        self.assertIn("title", result["properties"])
        self.assertEqual(result["properties"]["title"]["minLength"], 1)
        self.assertEqual(result["properties"]["settings"]["default"], {"title": "user data"})
        self.assertEqual(result["required"], ["title"])


    def test_write_receipt_does_not_repeat_code_or_claim_failed_write_succeeded(self):
        code = "unique_source_marker\n" * 800
        messages = self.base + exchange("edit_file", {"path": "src/app.py", "content": code},
                                        {"ok": False, "error_kind": "PermissionError", "error": "denied"})
        original = deepcopy(messages)
        prepared, metrics = prepare_messages(messages, self.policy)
        serialized = json.dumps(prepared)
        self.assertNotIn("unique_source_marker", serialized)
        self.assertIn("PermissionError", serialized)
        self.assertIn("src/app.py", serialized)
        self.assertIn("content_sha256", serialized)
        self.assertLess(metrics["after_chars"], metrics["before_chars"] // 3)
        self.assertEqual(prepared[:2], self.base)
        self.assertEqual(messages, original)

    def test_old_reads_are_shortened_and_recent_protocol_pairs_stay_complete(self):
        messages = self.base + exchange("read_file", {"path": "old.py"},
            {"ok": True, "result": {"path": "old.py", "content": "old content\n" * 2000}}, "old")
        messages += exchange("read_file", {"path": "current.py"},
            {"ok": True, "result": {"path": "current.py", "content": "needed current code"}}, "new")
        prepared, metrics = prepare_messages(messages, self.policy)
        self.assertEqual(prepared[:2], self.base)
        self.assertIn("Context shortened", json.dumps(prepared))
        self.assertEqual(prepared[-2]["tool_calls"][0]["id"], "new")
        self.assertEqual(prepared[-1]["tool_call_id"], "new")
        self.assertIn("needed current code", json.dumps(prepared))
        self.assertIn("content_ref", prepared[-1]["content"])
        self.assertGreater(metrics["compacted_turns"], 0)

    def test_execution_report_copy_is_removed_but_failure_details_remain(self):
        observation = {"ok": True, "result": {"status": "failed", "tests_run": 3, "failed": 1,
            "test_cases": ["case.a", "case.b", "case.c"], "summary": "AssertionError: expected 5, got 4",
            "execution": {"mode": "local", "exit_code": 1, "stdout": "duplicate report\n" * 1000, "stderr": ""}}}
        messages = self.base + exchange("run_tests", {"command": ["python", "-m", "unittest"]}, observation)
        prepared, _ = prepare_messages(messages, self.policy)
        report = json.loads(prepared[-1]["content"])["result"]
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["failed"], 1)
        self.assertEqual(report["tests_run"], 3)
        self.assertEqual(report["summary"], observation["result"]["summary"])
        self.assertNotIn("stdout", report["execution"])
        self.assertEqual(report["execution"]["exit_code"], 1)
        self.assertIn("stdout", observation["result"]["execution"])

    def test_disabled_and_incomplete_protocol_preserve_input(self):
        messages = self.base + exchange("edit_file", {"path": "x.py", "content": "x" * 10000}, {"ok": True})
        prepared, _ = prepare_messages(messages, RoleContextPolicy(enabled=False))
        self.assertEqual(prepared, messages)
        incomplete = messages[:-1]
        self.assertEqual(prepare_messages(incomplete, self.policy)[0], incomplete)

    def test_recent_file_content_is_marked_when_larger_than_the_limit(self):
        messages = self.base + exchange("read_file", {"path": "large.py"},
            {"ok": True, "result": {"path": "large.py", "content": "start\n" + "x" * 30000 + "\nend", "total_lines": 3}})
        prepared, _ = prepare_messages(messages, self.policy)
        result = json.loads(prepared[-1]["content"])["result"]
        self.assertTrue(result["context_shortened"])
        self.assertLessEqual(len(result["content"]), self.policy.max_tool_output_chars)
        self.assertTrue(result["content"].startswith("start"))
        self.assertTrue(result["content"].endswith("end"))

    def test_context_has_no_memory_between_role_calls(self):
        prior = self.base + exchange("edit_file", {"path": "old.py", "content": "x" * 10000}, {"ok": True})
        prepare_messages(prior, self.policy)
        next_role = [{"role": "system", "content": "Next role"}, {"role": "user", "content": "New task"}]
        self.assertEqual(prepare_messages(next_role, self.policy)[0], next_role)

    def test_latest_file_version_is_present_once_even_after_repeated_reads(self):
        old = "old_source_marker\n" * 100
        new = "current_source_marker\n" * 100
        messages = self.base + exchange("edit_file", {"path": "app.py", "content": old},
            {"ok": True, "result": {"path": "app.py"}}, "write-old")
        messages += exchange("edit_file", {"path": "app.py", "content": new},
            {"ok": True, "result": {"path": "app.py"}}, "write-new")
        for index in range(3):
            messages += exchange("read_file", {"path": "app.py"},
                {"ok": True, "result": {"path": "app.py", "content": new, "truncated": False}}, f"read-{index}")
        prepared, _ = prepare_messages(messages, self.policy)
        text = json.dumps(prepared)
        self.assertNotIn("old_source_marker", text)
        self.assertEqual(text.count("current_source_marker"), 100)
        self.assertIn("role_file_context", text)

    def test_changed_file_is_not_pinned_when_disk_validator_rejects_its_hash(self):
        messages = self.base + exchange("edit_file", {"path": "app.py", "content": "cached code\n" * 200},
            {"ok": True, "result": {"path": "app.py"}})
        seen = []
        prepared, _ = prepare_messages(messages, self.policy,
            file_validator=lambda path, digest: seen.append((path, digest)) or False)
        self.assertEqual(seen[0][0], "app.py")
        self.assertNotIn("role_file_context", json.dumps(prepared))
        self.assertNotIn("cached code", json.dumps(prepared))

    def test_langchain_trims_whole_receipts_to_budget_and_keeps_latest_failure(self):
        failure = {"tool": "run_tests", "observation": {
            "ok": True, "result": {"failed": 1, "summary": "AssertionError: unresolved AC1"}}}
        receipts = [{"tool": "read_file", "observation": "old source " * 100}, failure]
        receipts += [{"tool": "run_command", "observation": f"recent-{i} " + "x" * 160} for i in range(10)]
        original = deepcopy(receipts)
        kept, metrics = trim_checkpoint_receipts(receipts, 256)
        self.assertIn(failure, kept)
        self.assertEqual(kept[-1], receipts[-1])
        self.assertNotIn(receipts[0], kept)
        self.assertLessEqual(metrics["checkpoint_estimated_tokens"], 256)
        self.assertGreater(metrics["checkpoint_receipts_dropped"], 0)
        self.assertEqual(metrics["history_backend"], "langchain_core")
        self.assertFalse(metrics["checkpoint_protected_over_budget"])
        self.assertEqual(receipts, original)

    def test_latest_test_report_is_protected_even_when_over_budget(self):
        older = {"tool": "run_tests", "observation": {"summary": "old passing report"}}
        latest = {"tool": "run_tests", "observation": {"failed": 1, "summary": "failure " * 300}}
        kept, metrics = trim_checkpoint_receipts([older, latest], 256)
        self.assertEqual(kept, [latest])
        self.assertTrue(metrics["checkpoint_protected_over_budget"])
        self.assertGreater(metrics["checkpoint_estimated_tokens"], 256)
        self.assertEqual(trim_checkpoint_receipts([], 256)[0], [])

    def test_history_budget_preserves_requirement_and_recent_tool_protocol(self):
        messages = deepcopy(self.base)
        for index in range(15):
            messages += exchange("run_command", {"command": ["python", "--version"]},
                {"ok": True, "result": {"stdout": f"output-{index} " + "x" * 1000}}, str(index))
        prepared, metrics = prepare_messages(messages, RoleContextPolicy(max_checkpoint_tokens=256))
        self.assertEqual(prepared[:2], self.base)
        self.assertEqual(prepared[-2:][0]["tool_calls"][0]["id"], "14")
        self.assertEqual(prepared[-1]["tool_call_id"], "14")
        checkpoint = json.loads(prepared[2]["content"])
        self.assertGreater(checkpoint["older_receipts_omitted"], 0)
        self.assertEqual(checkpoint["older_receipts_omitted"], metrics["checkpoint_receipts_dropped"])
        self.assertLessEqual(metrics["checkpoint_estimated_tokens"], 256)

    def test_explicit_read_range_covering_whole_file_is_cached_once(self):
        source = "".join(f"unique_line_{i}\n" for i in range(167))
        messages = self.base + exchange("read_file", {"path": "app.py", "start_line": 1, "end_line": 500},
            {"ok": True, "result": {"path": "app.py", "content": source,
                                    "total_lines": 167, "truncated": False}}, "full")
        messages += exchange("read_file", {"path": "app.py", "start_line": 1, "end_line": 2},
            {"ok": True, "result": {"path": "app.py", "content": "unique_line_0\nunique_line_1\n",
                                    "total_lines": 167, "truncated": False}}, "partial")
        prepared, _ = prepare_messages(messages, self.policy, file_validator=lambda path, digest: True)
        cache = json.loads(prepared[2]["content"])["role_file_context"]
        self.assertEqual(cache[0]["content"], source)
        self.assertEqual(json.dumps(prepared).count("unique_line_0"), 1)
        self.assertNotIn("content", json.loads(prepared[-1]["content"])["result"])

    def test_partial_read_is_never_treated_as_complete_file(self):
        messages = self.base + exchange("read_file", {"path": "app.py", "start_line": 1, "end_line": 2},
            {"ok": True, "result": {"path": "app.py", "content": "line1\nline2\n",
                                    "total_lines": 3, "truncated": False}})
        prepared, _ = prepare_messages(messages, self.policy)
        self.assertNotIn("role_file_context", json.dumps(prepared))
        self.assertEqual(json.loads(prepared[-1]["content"])["result"]["content"], "line1\nline2\n")

    def test_batch_read_pins_full_files_once_and_preserves_native_parent_pair(self):
        reads = [{"path": "a.py"}, {"path": "b.py"}]
        results = [{"index": index + 1, "request": request, "observation": {"ok": True, "result": {
            "path": request["path"], "content": marker * 100, "total_lines": 100, "truncated": False}}}
            for index, (request, marker) in enumerate(zip(reads, ("source_a\n", "source_b\n")))]
        messages = self.base + exchange("read_files", {"files": reads}, {"ok": True, "result": {"results": results}}, "batch")
        prepared, _ = prepare_messages(messages, self.policy)
        rendered = json.dumps(prepared)
        self.assertEqual(rendered.count("source_a"), 100)
        self.assertEqual(rendered.count("source_b"), 100)
        self.assertEqual(prepared[-1]["tool_call_id"], "batch")
        observations = json.loads(prepared[-1]["content"])["result"]["results"]
        self.assertTrue(all("content_ref" in item["observation"]["result"] for item in observations))

    def test_exact_edit_updates_cached_source_without_another_read(self):
        before = "old_marker\n" + "unchanged\n" * 100
        after = before.replace("old_marker", "new_marker")
        messages = self.base + exchange("edit_file", {"path": "a.py", "content": before},
            {"ok": True, "result": {"path": "a.py"}}, "write")
        messages += exchange("replace_text", {"path": "a.py", "old_text": "old_marker", "new_text": "new_marker"},
            {"ok": True, "result": {"path": "a.py", "before_sha256": hashlib.sha256(before.encode()).hexdigest(),
                                    "sha256": hashlib.sha256(after.encode()).hexdigest()}}, "replace")
        prepared, _ = prepare_messages(messages, self.policy)
        rendered = json.dumps(prepared)
        self.assertNotIn("old_marker", rendered)
        self.assertEqual(rendered.count("new_marker"), 1)
        self.assertIn(after, json.loads(prepared[2]["content"])["role_file_context"][0]["content"])

    def test_batch_command_payloads_are_shortened_but_failure_and_exit_code_remain(self):
        commands = [{"command": ["python", "-c", "long-script\n" * 1000]}]
        observed = {"ok": True, "result": {"results": [{"request": commands[0], "observation": {
            "ok": True, "result": {"status": "completed", "exit_code": 3, "stdout": "output\n" * 5000,
                                    "stderr": "AssertionError: AC1"}}}], "failed": 1}}
        messages = self.base + exchange("run_commands", {"commands": commands}, observed)
        messages += exchange("list_files", {}, {"ok": True, "result": {"files": []}}, "recent")
        prepared, metrics = prepare_messages(messages, self.policy)
        serialized = json.dumps(prepared)
        self.assertIn("AssertionError: AC1", serialized)
        self.assertIn('"exit_code":3', serialized.replace(" ", "").replace('\\"', '"'))
        self.assertLess(metrics["after_chars"], metrics["before_chars"] // 3)
