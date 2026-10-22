"""Full MAS CLI runs with HTTP mock and real local development commands."""

import os
import difflib
import io
import json
import tempfile
import sys
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import httpx

from raac.mas_cli import execute
from raac.harness.layer3_execution.tools.patching import apply_solution_patch
from raac.llm.client import LLMClient
from raac.llm.config import load_llm_config, ModelProfile
from raac.registry.loader import load_registry

PROJECT = Path(__file__).resolve().parents[3]
BASELINE = "class Solution:\n    def countSmaller(self, nums):\n        pass\n"
SOLUTION = """class Solution:
    def countSmaller(self, nums):
        return [sum(y < x for y in nums[i+1:]) for i, x in enumerate(nums)]
"""
GENERATED = """import unittest
from solution import Solution
class Generated(unittest.TestCase):
    def test_additional_case(self):
        self.assertEqual(Solution().countSmaller([2, 0, 1]), [2, 0, 0])
"""
ENVELOPES = """from bisect import bisect_left
class Solution:
    def maxEnvelopes(self, envelopes):
        tails = []
        for _, height in sorted(envelopes, key=lambda pair: (pair[0], -pair[1])):
            index = bisect_left(tails, height)
            if index == len(tails):
                tails.append(height)
            else:
                tails[index] = height
        return len(tails)
"""
ENVELOPE_TESTS = """import unittest
from solution import Solution
class EnvelopesTests(unittest.TestCase):
    def test_example(self):
        self.assertEqual(Solution().maxEnvelopes([[5,4],[6,4],[6,7],[2,3]]), 3)
    def test_equal_dimensions(self):
        self.assertEqual(Solution().maxEnvelopes([[1,1],[1,1],[1,1]]), 1)
    def test_equal_width_cannot_nest(self):
        self.assertEqual(Solution().maxEnvelopes([[2,2],[2,3],[3,4]]), 2)
"""


def commit_payload(data):
    """Serialize existing scenario fixtures using the commit's wire fields."""
    if not isinstance(data, dict):
        return data
    if "status" in data and "blocking_issues" in data and "tests_run" not in data:
        return data
    if "acceptance_criteria" in data:
        return {**data, "requirements": [{"id": "R1", "description": data["summary"]}],
                "acceptance_criteria": [item["description"] if isinstance(item, dict) else item
                                        for item in data["acceptance_criteria"]], "open_questions": []}
    if "steps" in data:
        return {**data, "steps": [{"id": int(str(step["id"]).removeprefix("S")),
                "description": step["approach"], "files": [step["target"].split("::")[0]],
                "depends_on": [], "ac_ids": step["ac_ids"]} for step in data["steps"]]}
    if "tests_run" in data:
        names = data["test_cases"] or ["<no tests executed>"]
        status = data["status"]
        results = [{"test": name, "status": ("error" if status == "error" else
                    "failed" if index < data["failed"] else "passed" if data["test_cases"] else "skipped"),
                    "details": data["summary"]} for index, name in enumerate(names)]
        return {"tests_to_run": names, "results": results,
                "verdict": "fail" if any(item["status"] in {"failed", "error"} for item in results) else "pass",
                "covered_ac_ids": data["covered_ac_ids"], "blocking_issues": data["blocking_issues"]}
    if "verdict" in data:
        rejected = data["verdict"] == "REJECT"
        comments = [{"severity": "blocker", "message": issue} for issue in data["blocking_issues"]]
        if rejected and not comments:
            comments = [{"severity": "major", "message": "Fixture review rejects this change"}]
        return {"decision": "reject" if rejected else "approve", "summary": data["summary"],
                "comments": comments, "covered_ac_ids": data["covered_ac_ids"]}
    if "summary" in data:
        return data.get("patch", "")
    return data


class LocalChainTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project_dir = Path(self.temp.name) / "currentProject"
        (self.project_dir / "repo").mkdir(parents=True)
        (self.project_dir / "requirement").mkdir()
        (self.project_dir / "repo/solution.py").write_text(BASELINE)
        (self.project_dir / "requirement/user_chat.txt").write_text("Count smaller elements to the right")

    async def test_cli_nested_src_project_selects_tests_without_solution_file(self):
        repo = self.project_dir / "repo"
        (repo / "solution.py").unlink()
        (repo / "src/app").mkdir(parents=True)
        (repo / "src/app/__init__.py").write_text("")
        (repo / "src/app/service.py").write_text("def add(a, b): return a - b\n")
        (self.project_dir / "requirement/user_chat.txt").write_text("Fix app.service.add; preserve project layout")
        implementation = "def add(a, b): return a + b\n" + "# saved source context fixture\n" * 100
        tests = """import unittest
from pathlib import Path
from app.service import add
class ServiceTests(unittest.TestCase):
    def test_positive(self):
        self.assertEqual(add(2, 3), 5)
        Path('build').mkdir(exist_ok=True)
        Path('build/output.txt').write_text('local-build')
    def test_negative(self):
        self.assertEqual(add(-2, 3), 1)
"""
        command = ["python", "-m", "unittest", "discover", "-s", "checks"]
        wrote_code = wrote_tests = fixed_tests = False
        registry = load_registry(PROJECT / "registry")
        for role_id, ref in (("coder", "coding"), ("tester", "testing")):
            spec = registry.get_role(role_id)
            registry.roles[role_id] = spec.model_copy(update={"model": spec.model.model_copy(update={"ref": ref})})
        config = load_llm_config(PROJECT / "configs/llm.yaml", model="mock-project", base_url="http://mock.invalid/v1")
        config.profiles = {role.model.ref: ModelProfile(model="mock-project")
                           for role in registry.roles.values() if role.model.ref != "default"}
        config.profiles.update({"coding": ModelProfile(model="code-specialist", max_tokens=8192),
                                "testing": ModelProfile(model="test-specialist", temperature=0.2)})
        selected = []

        def handler(request):
            nonlocal wrote_code, wrote_tests, fixed_tests
            payload = json.loads(request.content)
            messages = payload["messages"]
            role_id = next(role for role in registry.roles if role.capitalize() in messages[0]["content"].splitlines()[0])
            expected = {"coder": "code-specialist", "tester": "test-specialist"}.get(role_id, "mock-project")
            self.assertEqual(payload["model"], expected)
            self.assertEqual(payload["max_tokens"], 8192 if role_id == "coder" else config.max_tokens)
            selected.append(role_id)
            work = json.loads(messages[1]["content"])
            self.assertNotIn("solution.py", work["repository_files"])
            system = messages[0]["content"].replace("**", "")
            if system.startswith("You are the Environment"):
                self.assertIn("requirement", work)
                self.assertNotIn("spec", work["artifacts"])
            elif system.startswith(("You are the Planner", "You are the Coder", "You are the Tester")):
                self.assertNotIn("requirement", work)
                self.assertEqual(work["artifacts"]["spec"]["interface_contracts"]["add"],
                                 "app.service.add(a, b) returns a + b")
            else:
                self.assertIn("requirement", work)
            tool_calls = None
            if system.startswith("You are the Analyst"):
                output = {"summary": "Fix add", "constraints": [],
                          "interface_contracts": {"add": "app.service.add(a, b) returns a + b"},
                          "acceptance_criteria": [{"id": "AC1", "description": "Correct sum"}]}
            elif system.startswith("You are the Planner"):
                output = {"steps": [{"id": "S1", "ac_ids": ["AC1"], "target": "src/app/service.py::add",
                                     "approach": "Choose addition instead of subtraction", "complexity": "O(1)",
                                     "risks": ["Preserve negative inputs"]}],
                          "interface_contracts": {"test_command": "PYTHONPATH=src python -m unittest discover -s checks"},
                          "covered_ac_ids": ["AC1"]}
            elif system.startswith("You are the Environment"):
                if any(message.get("role") == "tool" for message in messages[2:]):
                    output = {"status": "ready", "summary": "Verified temporary test runtime", "blocking_issues": []}
                else:
                    output = None
                    tool_calls = [{"id": "configure-runtime", "type": "function", "function": {
                        "name": "configure_environment", "arguments": json.dumps({
                            "python_executable": sys.executable, "cwd": ".",
                            "test_command": ["python", "-m", "unittest", "discover"],
                            "check_command": ["python", "-c", "import unittest"]})}}]
            elif system.startswith("You are the Coder") and not wrote_code:
                self.assertEqual(work["artifacts"]["plan"]["steps"][0]["id"], 1)
                wrote_code = True
                output = None
                tool_calls = [{"id": "write-source", "type": "function", "function": {"name": "edit_file",
                    "arguments": json.dumps({"path": "src/app/service.py", "content": implementation})}},
                    {"id": "inspect-runtime", "type": "function", "function": {"name": "run_commands",
                     "arguments": json.dumps({"commands": [
                         {"command": ["python", "--version"], "cwd": "."},
                         {"command": ["python", "-c", "from app.service import add; assert add(2, 3) == 5; print('smoke-ok')"],
                          "cwd": ".", "env": {"PYTHONPATH": "src"}}]})}}]
            elif system.startswith("You are the Coder"):
                output = "--- a/src/app/service.py\n+++ b/src/app/service.py\n@@ -1,1 +1,81 @@\n-old\n+new\n"
            elif system.startswith("You are the Tester") and not wrote_tests:
                wrote_tests = True
                output = None
                self.assertNotIn("patch", work["artifacts"])
                self.assertIn("patch_context", work["artifacts"])
                self.assertEqual(work["repository_changes"], [{"path": "src/app/service.py", "deleted": False}])
                evidence = work["artifacts"]["coder_evidence"]
                self.assertEqual(len(evidence), 2)
                self.assertIn("smoke-ok", evidence[1]["stdout"])
                self.assertTrue(all(item["matches_final_patch"] for item in evidence))
                self.assertEqual(work["artifacts"]["coder_summary"], "Workspace changes saved")
                self.assertEqual(set(work["artifacts"]["plan"]), {"interface_contracts"})
                self.assertIn("checks", work["artifacts"]["plan"]["interface_contracts"]["test_command"])
                self.assertNotIn("Choose addition instead of subtraction", json.dumps(work))
                tool_calls = [{"id": "inspect-source", "type": "function", "function": {"name": "read_files",
                    "arguments": json.dumps({"files": [{"path": "src/app/service.py"}, {"path": "src/app/__init__.py"}]})}},
                    {"id": "write-tests", "type": "function", "function": {"name": "edit_file",
                    "arguments": json.dumps({"path": "checks/test_service.py", "content": tests.replace("self.assertEqual(add(2, 3), 5)", "self.assertEqual(add(2, 3), 4)")})}},
                    {"id": "run-project-tests", "type": "function", "function": {"name": "run_tests",
                     "arguments": json.dumps({"command": command, "cwd": ".", "env": {"PYTHONPATH": "src"}})}}]
            elif system.startswith("You are the Tester") and not fixed_tests:
                fixed_tests = True
                output = None
                tool_calls = [{"id": "fix-test-region", "type": "function", "function": {"name": "replace_text",
                    "arguments": json.dumps({"path": "checks/test_service.py", "old_text": "self.assertEqual(add(2, 3), 4)",
                                             "new_text": "self.assertEqual(add(2, 3), 5)"})}},
                    {"id": "rerun-project-tests", "type": "function", "function": {"name": "run_tests", "arguments": "{}"}}]
            elif system.startswith("You are the Tester"):
                output = {"patch_hash": work["artifacts"]["patch_hash"], "status": "passed", "tests_run": 99,
                          "failed": 0, "test_cases": ["claimed"], "covered_ac_ids": ["AC1"], "blocking_issues": [], "summary": "Observed tests"}
            else:
                self.assertIn("src/app/service.py", work["artifacts"]["patch"])
                self.assertIn("smoke-ok", work["artifacts"]["coder_evidence"][1]["stdout"])
                self.assertNotIn("steps", work["artifacts"]["plan"])
                self.assertEqual(work["artifacts"]["plan"]["decisions"][0]["files"], ["src/app/service.py"])
                output = {"patch_hash": work["artifacts"]["patch_hash"], "verdict": "PASS", "covered_ac_ids": ["AC1"],
                          "blocking_issues": [], "summary": "Reviewed project"}
            return httpx.Response(200, json={"model": "mock-project", "choices": [{"finish_reason": "tool_calls" if tool_calls else "stop",
                "message": {"role": "assistant", "content": (commit_payload(output) if isinstance(commit_payload(output), str) else json.dumps(commit_payload(output))) if output is not None else None,
                            **({"tool_calls": tool_calls} if tool_calls else {})}}], "usage": {"prompt_tokens": 10, "completion_tokens": 5}})

        def client_factory(config, *, log_path):
            return LLMClient(config, log_path=log_path, transport=httpx.MockTransport(handler))

        stdout = io.StringIO()
        args = SimpleNamespace(command="run-chain", model="mock-project", base_url="http://mock.invalid/v1", max_rounds=1,
                               project_dir=str(self.project_dir))
        with patch("raac.mas_cli.load_registry", return_value=registry), \
                patch("raac.mas_cli.load_llm_config", return_value=config), \
                patch("raac.mas_cli.LLMClient", side_effect=client_factory), redirect_stdout(stdout):
            self.assertEqual(await execute(args), 0)
        output_dir = Path(json.loads(stdout.getvalue())["output"])
        record = json.loads((output_dir / "result.json").read_text(encoding="utf-8"))
        self.assertEqual(set(selected), set(registry.roles))
        self.assertGreater(selected.count("coder"), 1)
        self.assertGreater(selected.count("tester"), 1)
        role_models = record["provenance"]["effective_config"]["role_models"]
        self.assertEqual(role_models["coder"]["model"], "code-specialist")
        self.assertEqual(role_models["tester"]["model"], "test-specialist")
        self.assertEqual(role_models["analyst"]["model"], "mock-project")
        self.assertEqual(record["provenance"]["registry_snapshot"]["roles"]["coder"]["model"], {"ref": "coding"})
        self.assertEqual(record["test_report"]["tests_run"], 2)
        self.assertIsNone(record["usage"]["cached_tokens"])
        self.assertFalse(record["usage"]["cache_usage_complete"])
        self.assertIn("src/app/service.py", record["patch"])
        self.assertNotIn("checks/test_service.py", record["patch"])
        self.assertFalse((repo / "solution.py").exists())
        self.assertFalse((repo / "test_generated.py").exists())
        self.assertEqual((repo / "build/output.txt").read_text(), "local-build")
        self.assertEqual((repo / "src/app/service.py").read_text(), implementation)
        self.assertEqual((repo / "checks/test_service.py").read_text(), tests)
        self.assertEqual(len(record["coder_evidence"]), 2)
        self.assertNotIn("fabricated", json.dumps(record["coder_evidence"]))
        self.assertEqual({file.name for file in output_dir.iterdir()}, {"llm.jsonl", "result.json", "memory"})
        trace = [json.loads(line) for line in (output_dir / "llm.jsonl").read_text(encoding="utf-8").splitlines()]
        rebuilt = next(event for event in trace if event["event"] == "coder_patch_rebuilt")
        self.assertEqual(rebuilt["source"], "workspace")
        self.assertEqual(rebuilt["reason"], "invalid_model_diff")
        self.assertEqual(rebuilt["patch_hash"], record["patch_hash"])
        audit = next(event["request"] for event in trace
                     if event["event"] == "agent_started" and event["role"] == "Tester")
        self.assertEqual(audit["artifacts"]["plan"], {"interface_contracts": record["plan"]["interface_contracts"]})
        self.assertNotIn("requirement", audit)
        self.assertIn("steps", record["plan"])
        self.assertEqual(record["repository_changes"], [{"path": "src/app/service.py", "deleted": False}])
        self.assertNotIn("patch", audit["artifacts"])
        self.assertEqual(audit["artifacts"]["patch_context"]["chars"], len(record["patch"]))
        runtime = next(event["observation"]["result"] for event in trace
                       if event["event"] == "tool_call_completed" and event["tool_name"] == "run_command")
        self.assertEqual(runtime["mode"], "local")
        self.assertEqual(runtime["exit_code"], 0)
        self.assertIn("Python", runtime["stdout"] + runtime["stderr"])

    async def test_cli_full_chain_with_http_mock_and_local_execution(self):
        original = (self.project_dir / "repo/solution.py").read_text()
        wrote_tests = False
        wrote_code = False
        seen_requirements = []
        generated_source = GENERATED
        implementation = SOLUTION

        def handler(request):
            nonlocal wrote_tests, wrote_code
            messages = json.loads(request.content)["messages"]
            work = json.loads(messages[1]["content"])
            system = messages[0]["content"].replace("**", "")
            if system.startswith(("You are the Analyst", "You are the Reviewer")):
                seen_requirements.append(work["requirement"])
            elif system.startswith("You are the Environment"):
                self.assertIn("requirement", work)
            else:
                self.assertNotIn("requirement", work)
                self.assertIn("spec", work["artifacts"])
            tool_calls = None
            if system.startswith("You are the Analyst"):
                output = {"summary": work["requirement"], "constraints": [], "acceptance_criteria": [
                    {"id": "AC1", "description": "Implement the current requested operation"}]}
            elif system.startswith("You are the Planner"):
                output = {"steps": [{"id": "S1", "ac_ids": ["AC1"], "target": "solution.py",
                                     "approach": "Implement the current operation"}],
                          "covered_ac_ids": ["AC1"]}
            elif system.startswith("You are the Environment"):
                if any(message.get("role") == "tool" for message in messages[2:]):
                    output = {"status": "ready", "summary": "Verified temporary test runtime", "blocking_issues": []}
                else:
                    output = None
                    tool_calls = [{"id": "configure-runtime", "type": "function", "function": {
                        "name": "configure_environment", "arguments": json.dumps({
                            "python_executable": sys.executable, "cwd": ".",
                            "test_command": ["python", "-m", "unittest", "discover"],
                            "check_command": ["python", "-c", "import unittest"]})}}]
            elif system.startswith("You are the Coder") and not wrote_code:
                wrote_code = True
                output = None
                tool_calls = [{"id": "write-code", "type": "function", "function": {
                    "name": "edit_file", "arguments": json.dumps({"path": "solution.py", "content": implementation}),
                }}]
            elif system.startswith("You are the Coder"):
                output = {"summary": "Saved implementation fixture through edit_file"}
            elif system.startswith("You are the Tester") and not wrote_tests:
                wrote_tests = True
                output = None
                tool_calls = [{"id": "write-tests", "type": "function", "function": {
                    "name": "edit_file", "arguments": json.dumps({"path": "test_generated.py", "content": generated_source}),
                }}]
            elif system.startswith("You are the Tester"):
                output = {"patch_hash": work["artifacts"]["patch_hash"], "status": "passed",
                          "tests_run": 99, "failed": 0, "test_cases": ["invented"],
                          "covered_ac_ids": ["AC1"], "blocking_issues": [], "summary": "HTTP mock report"}
            else:
                output = {"patch_hash": work["artifacts"]["patch_hash"], "verdict": "PASS",
                          "covered_ac_ids": ["AC1"], "blocking_issues": [], "summary": "HTTP mock review"}
            return httpx.Response(200, json={"model": "http-mock-verification", "choices": [{
                "finish_reason": "tool_calls" if tool_calls else "stop",
                "message": {"role": "assistant", "content": (commit_payload(output) if isinstance(commit_payload(output), str) else json.dumps(commit_payload(output))) if output is not None else None,
                            **({"tool_calls": tool_calls} if tool_calls else {})},
            }], "usage": {"prompt_tokens": 10, "completion_tokens": 5,
                           "prompt_tokens_details": {"cached_tokens": 6}}})

        def client_factory(config, *, log_path):
            return LLMClient(config, log_path=log_path, transport=httpx.MockTransport(handler))

        stdout = io.StringIO()
        args = SimpleNamespace(command="run-chain", model="http-mock-verification",
                               base_url="http://http-mock.invalid/v1", max_rounds=1,
                               project_dir=str(self.project_dir))
        with patch.dict(os.environ, {"LLM_API_KEY": "integration-test-only"}), \
                patch("raac.mas_cli.LLMClient", side_effect=client_factory), redirect_stdout(stdout):
            code = await execute(args)
        self.assertEqual(code, 0)
        output_dir = Path(json.loads(stdout.getvalue())["output"])
        record = json.loads((output_dir / "result.json").read_text(encoding="utf-8"))
        self.assertEqual(record["verdict"], "PASS")
        self.assertEqual(record["usage"]["cached_tokens"], record["usage"]["calls"] * 6)
        self.assertTrue(record["usage"]["cache_usage_complete"])
        self.assertEqual(record["usage"]["total_tokens"], record["usage"]["calls"] * 15)
        decision = record["coordinator_decision"]
        self.assertEqual(decision["topology"], "chain")
        self.assertEqual(decision["max_rounds"], 1)
        self.assertEqual(decision["source"], "fixed_default")
        self.assertEqual(record["events"][0]["node"], "coordinator")
        self.assertEqual(record["events"][0]["event"], "topology_selected")
        self.assertEqual(record["events"][0]["decision"], decision)
        self.assertEqual(record["provenance"]["effective_config"]["coordinator_decision"], decision)
        self.assertEqual(record["test_report"]["tests_run"], 1)
        self.assertNotIn("baseline_test", record)
        self.assertEqual(record["execution"]["mode"], "local")
        self.assertNotIn("evaluation", record)
        self.assertNotIn("sandbox_image_id", record)
        self.assertEqual(record["provenance"]["registry_version"], "0.18.7")
        declaration = record["provenance"]["registry_snapshot"]["roles"]["planner"]
        self.assertEqual(declaration["role"], "Planner")
        self.assertEqual(declaration["model"], {"ref": "default"})
        self.assertEqual(declaration["cost_weight"], 1.0)
        self.assertEqual(declaration["prompt"], "prompts/planner.md")
        self.assertEqual(record["provenance"]["effective_config"]["llm"]["model"], "http-mock-verification")
        self.assertNotIn("api_key", record["provenance"]["effective_config"]["llm"])
        self.assertEqual((self.project_dir / "repo/solution.py").read_text(), SOLUTION)
        self.assertEqual(apply_solution_patch(original, record["patch"]), SOLUTION)
        self.assertEqual((self.project_dir / "repo/test_generated.py").read_text(), GENERATED)
        self.assertEqual({path.name for path in output_dir.iterdir()}, {"result.json", "llm.jsonl", "memory"})
        self.assertEqual(record["provenance"]["workspace"]["mode"], "in_place")
        self.assertEqual(record["provenance"]["workspace"]["repo_dir"], str(self.project_dir / "repo"))
        trace = [json.loads(line) for line in (output_dir / "llm.jsonl").read_text(encoding="utf-8").splitlines()]
        events = {event["event"] for event in trace}
        self.assertTrue({"run_started", "agent_started", "llm_request", "llm_response", "tool_call_started",
                         "tool_call_completed", "agent_completed", "run_completed"}.issubset(events))
        self.assertEqual(record["trace"]["event_count"], len(trace))
        environment_calls = [event for event in trace if event["event"] == "agent_started" and event["agent_id"] == "environment"]
        self.assertEqual(len(environment_calls), 1)
        self.assertEqual(decision["topology_version"], "5")
        self.assertTrue(record["test_report"]["observation_ref"])
        self.assertTrue(record["environment_report"]["observation_ref"])
        self.assertEqual([event["sequence"] for event in trace], list(range(1, len(trace) + 1)))
        planner = next(event for event in trace if event["event"] == "agent_started" and event["agent_id"] == "planner")
        self.assertEqual(planner["request"]["incoming_message"]["from"], "Analyst")
        tool_events = [event for event in trace if event["event"] == "tool_call_completed"]
        self.assertTrue(any(event["origin"] == "adapter_validation" for event in tool_events))
        self.assertTrue(all(event.get("agent_call_id") for event in tool_events))

        # A second run uses current code + new requirements. No result/workspace
        # or built-in test suite from the first run is loaded.
        follow_up = ENVELOPES
        implementation = follow_up
        generated_source = ENVELOPE_TESTS
        wrote_tests = False
        wrote_code = False
        seen_requirements.clear()
        (self.project_dir / "requirement/user_chat.txt").write_text(
            "Russian Doll Envelopes: implement Solution.maxEnvelopes(envelopes). "
            "Return the longest nested chain with strictly increasing width and height.")
        second_stdout = io.StringIO()
        with patch.dict(os.environ, {"LLM_API_KEY": "integration-test-only"}), \
                patch("raac.mas_cli.LLMClient", side_effect=client_factory), redirect_stdout(second_stdout):
            self.assertEqual(await execute(args), 0)
        second_dir = Path(json.loads(second_stdout.getvalue())["output"])
        second = json.loads((second_dir / "result.json").read_text(encoding="utf-8"))
        self.assertNotEqual(second_dir, output_dir)
        self.assertNotIn("baseline_test", second)
        self.assertNotEqual(second["provenance"]["task_snapshot"], record["provenance"]["task_snapshot"])
        self.assertTrue(all("Russian Doll Envelopes" in text for text in seen_requirements))
        self.assertNotEqual(second["task_id"], record["task_id"])
        self.assertEqual(second["test_report"]["tests_run"], 3)
        self.assertTrue(all("EnvelopesTests" in name for name in second["test_report"]["test_cases"]))
        self.assertEqual((self.project_dir / "repo/solution.py").read_text(), follow_up)
        self.assertEqual(apply_solution_patch(SOLUTION, second["patch"]), follow_up)
        self.assertEqual((self.project_dir / "repo/test_generated.py").read_text(), ENVELOPE_TESTS)
        self.assertEqual({path.name for path in second_dir.iterdir()}, {"result.json", "llm.jsonl", "memory"})


    async def test_unknown_profile_fails_before_workspace_or_http(self):
        registry = load_registry(PROJECT / "registry")
        spec = registry.get_role("analyst")
        registry.roles["analyst"] = spec.model_copy(update={"model": spec.model.model_copy(update={"ref": "missing"})})
        args = SimpleNamespace(command="run-chain", model="base-id", base_url="http://mock.invalid/v1",
                               max_rounds=1, project_dir=str(self.project_dir))
        from raac.mas_cli import DemoError
        with patch("raac.mas_cli.load_registry", return_value=registry), \
                patch("raac.mas_cli.WorkspaceTools") as workspace, \
                patch("raac.mas_cli.LLMClient") as client:
            with self.assertRaisesRegex(DemoError, "analyst.*Unknown model profile.*no task was sent"):
                await execute(args)
        workspace.assert_not_called()
        client.assert_not_called()
        self.assertEqual((self.project_dir / "repo/solution.py").read_text(), BASELINE)
