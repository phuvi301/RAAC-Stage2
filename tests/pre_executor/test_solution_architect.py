"""Flow solution_architect (SA, tầng Macro): tech stack, module, API, ràng buộc toàn cục, runtime/test contract."""

import asyncio
import json
import shutil
from copy import deepcopy
from pathlib import Path

import httpx
import pytest
from pe_helpers import BA_SPEC, approve_ba_spec, pm_output, sa_output

from raac.pre_executor import cli, gate
from raac.pre_executor.artifacts import Artifacts
from raac.pre_executor.config import PreExecutorConfig
from raac.pre_executor.flows import project_manager, solution_architect
from raac.pre_executor.flows._checks import find_cycle, validate_relative_path
from raac.pre_executor.flows.base import FlowError

ROOT = Path(__file__).resolve().parents[2]
CONFIG = PreExecutorConfig(max_flow_retries=1)


class FakeRuntime:
    def __init__(self, *outputs):
        self.outputs, self.requests = list(outputs), []
        self.run_id, self.task_id = "chain-fake", "task-fake"

    async def run(self, role_name, request):
        self.requests.append((role_name, request))
        return self.outputs.pop(0)

    async def aclose(self, *, status="completed"):
        pass


def run(coro):
    return asyncio.run(coro)


def ready(tmp_path) -> Artifacts:
    return approve_ba_spec(Artifacts(tmp_path))


def modules(*items):
    return [{"responsibility": "x", "path": f"src/{item['name']}", **item} for item in items]


# ---------------------------------------------------------------- helper dùng chung

@pytest.mark.parametrize("path, ok", [("src/shop", True), ("tests", True), (".", True), ("/abs/path", False),
                                      ("../escape", False), ("a/../b", False), ("C:\\win", False),
                                      ("C:/win", False), ("a\\b", False), ("~/home", False), (" ", False)])
def test_relative_path_rule(path, ok):
    assert (validate_relative_path(path) is None) is ok


def test_find_cycle_is_deterministic():
    assert find_cycle({"MOD-1": ["MOD-2"], "MOD-2": ["MOD-1"]}) == ["MOD-1", "MOD-2", "MOD-1"]
    assert find_cycle({"T1": [], "T2": ["T3"], "T3": ["T4"], "T4": ["T2"]}) == ["T2", "T3", "T4", "T2"]
    assert find_cycle({"T1": [], "T2": ["T1"], "T10": ["T2"]}) is None


# ---------------------------------------------------------------- gate

def test_needs_an_approved_ba_spec(tmp_path):
    artifacts = Artifacts(tmp_path)
    artifacts.save_request("Làm website")
    with pytest.raises(gate.GateError):  # missing
        run(solution_architect.run(artifacts, FakeRuntime(sa_output()), CONFIG))
    artifacts = ready(tmp_path)
    approvals = artifacts.read_json("approvals.json")
    del approvals["business_analyst"]
    artifacts.write_json("approvals.json", approvals)  # draft
    with pytest.raises(gate.GateError):
        run(solution_architect.run(artifacts, FakeRuntime(sa_output()), CONFIG))
    artifacts = ready(tmp_path)
    spec = artifacts.read_json("ba_spec.json")
    artifacts.write_json("ba_spec.json", {**spec, "constraints": ["đã sửa tay"]})  # stale
    assert gate.state(artifacts, "business_analyst") == "stale"
    with pytest.raises(gate.GateError):
        run(solution_architect.run(artifacts, FakeRuntime(sa_output()), CONFIG))


def test_drafts_the_architecture_from_the_approved_ba_spec(tmp_path):
    artifacts = ready(tmp_path)
    runtime = FakeRuntime(sa_output())
    result = run(solution_architect.run(artifacts, runtime, CONFIG))
    role, request = runtime.requests[0]
    assert role == "SolutionArchitect" and request["output_schema"]["title"] == "SolutionArchitectureOutput"
    sent = request["artifacts"]
    assert sent["ba_spec"] == BA_SPEC and sent["workspace_mode"] == "greenfield"
    assert set(sent) == {"request", "clarification", "ba_spec", "workspace_mode", "user_feedback"}
    assert artifacts.read_json("solution_architecture.json")["modules"][1]["id"] == "MOD-2"
    assert gate.state(artifacts, "solution_architect") == "draft" and result["changes"] == []
    assert "Python, 3 module, 1 data model, 2 hợp đồng API, 3 ràng buộc (greenfield)" in result["message"]
    markdown = artifacts.read_text("solution_architecture.md")
    for text in ("## 2. Tech stack", "| Ngôn ngữ | Python | >=3.11 | đội quen |", "### MOD-3 · orders (`src/shop/orders`)",
                 "### DM-1 · Order (MOD-3)", "`POST /orders`", "**Xác thực:** session cookie cho admin",
                 "**Test:** `python -m pytest -q` (pytest)", "| CON-2 | security | route admin phải kiểm quyền |",
                 "**ADR-1 · Kiểu kiến trúc**", "chưa chốt cổng thanh toán online", "- **Thực thể BA:** ENT-2",
                 "- **Tích hợp ngoài:** INT-1"):
        assert text in markdown


def test_read_ba_spec_prefers_ba_spec_json_and_falls_back_to_an_old_workspace(tmp_path):
    artifacts = Artifacts(tmp_path)
    assert artifacts.read_ba_spec() is None
    artifacts.write_json("requirement_profile.json", {"features": [], "from": "old"})  # workspace trước khi gộp BA
    assert artifacts.read_ba_spec()["from"] == "old"
    artifacts.write_json("ba_spec.json", {"features": [], "from": "new"})
    assert artifacts.read_ba_spec()["from"] == "new"


def test_detects_a_brownfield_workspace(tmp_path):
    artifacts = ready(tmp_path)
    (tmp_path / "pyproject.toml").write_text("[project]\nname='shop'\n", encoding="utf-8")
    runtime = FakeRuntime(sa_output())
    run(solution_architect.run(artifacts, runtime, CONFIG))
    assert runtime.requests[0][1]["artifacts"]["workspace_mode"] == "brownfield"


def test_entities_and_integrations_are_only_checked_when_the_ba_spec_has_ids(tmp_path):
    spec = deepcopy(BA_SPEC)
    spec["key_entities"] = [{"name": "Đơn hàng", "description": "đơn"}]  # workspace cũ: chưa có ENT-n
    spec.pop("external_integrations")
    artifacts = approve_ba_spec(Artifacts(tmp_path), spec)
    plain = sa_output(data_models=[{**sa_output()["data_models"][0], "entity_ids": []}],
                      api_contracts=[{**c, "integration_ids": []} for c in sa_output()["api_contracts"]])
    runtime = FakeRuntime(plain)
    run(solution_architect.run(artifacts, runtime, CONFIG))
    assert len(runtime.requests) == 1


# ---------------------------------------------------------------- schema

def _without_reason():
    data = sa_output()
    del data["tech_stack"]["language"]["reason"]
    return data


def _without_test_command():
    data = sa_output()
    del data["runtime_contract"]["test_command"]
    return data


@pytest.mark.parametrize("bad", [
    sa_output(extra_field="x"),
    sa_output(modules=modules({"id": "MOD-x", "name": "core"})),
    sa_output(api_contracts=[{"id": "API-x", "module_id": "MOD-1", "kind": "http", "signature": "GET /", "purpose": "x"}]),
    _without_test_command(),
    _without_reason(),
    sa_output(global_constraints=[]),
])
def test_schema_errors_fail_the_flow(tmp_path, bad):
    with pytest.raises(FlowError):
        run(solution_architect.run(ready(tmp_path), FakeRuntime(bad), CONFIG))


# ---------------------------------------------------------------- luật kiểm bằng code

BASE_MODULES = sa_output()["modules"]


def _mod(index, **changes):
    items = deepcopy(BASE_MODULES)
    items[index].update(changes)
    return items


def _security(**changes):
    return sa_output(security_architecture={"auth_strategy": "", "authorization_model": "", "nfr_ids": ["NFR-2"],
                                            **changes})


@pytest.mark.parametrize("bad, problem", [
    (sa_output(modules=[*BASE_MODULES, {**BASE_MODULES[0], "name": "core2"}]), "duplicate module id MOD-1"),
    (sa_output(data_models=[*sa_output()["data_models"], {**sa_output()["data_models"][0], "name": "Order2"}]),
     "duplicate data model id DM-1"),
    (sa_output(api_contracts=[*sa_output()["api_contracts"], {**sa_output()["api_contracts"][0], "signature": "x"}]),
     "duplicate API contract id API-1"),
    (sa_output(global_constraints=[*sa_output()["global_constraints"], sa_output()["global_constraints"][0]]),
     "duplicate global constraint id CON-1"),
    (sa_output(decisions=[*sa_output()["decisions"], sa_output()["decisions"][0]]), "duplicate decision id ADR-1"),
    (sa_output(modules=_mod(1, name="Core")), "duplicate module name core"),
    (sa_output(tech_stack={**sa_output()["tech_stack"], "libraries": [{"name": "Pydantic", "reason": "a"},
                                                                       {"name": "pydantic", "reason": "b"}]}),
     "duplicate library pydantic"),
    (sa_output(folders=[{"path": "src", "purpose": "a"}, {"path": "src/", "purpose": "b"}]), "duplicate folder path src"),
    (sa_output(folders=[{"path": "/abs/path", "purpose": "a"}]), "folder: path '/abs/path' must be relative"),
    (sa_output(folders=[{"path": "../escape", "purpose": "a"}]), "must not contain '..'"),
    (sa_output(modules=_mod(0, path="C:\\win")), "module MOD-1: path 'C:\\\\win' must use '/' separators"),
    (sa_output(modules=_mod(0, path="a\\b")), "must use '/' separators"),
    (sa_output(modules=_mod(1, feature_ids=["F-1", "F-99"])), "module MOD-2 refers to unknown features ['F-99']"),
    (sa_output(modules=_mod(2, feature_ids=["F-3"])), "feature F-2 is not owned by any module"),
    (sa_output(modules=_mod(1, depends_on=["MOD-99"])), "module MOD-2 depends on unknown modules ['MOD-99']"),
    (sa_output(modules=_mod(0, depends_on=["MOD-1"])), "module MOD-1 depends on itself"),
    (sa_output(modules=_mod(0, depends_on=["MOD-2"])), "module dependency cycle MOD-1 -> MOD-2 -> MOD-1"),
    (sa_output(api_contracts=[{**sa_output()["api_contracts"][0], "module_id": "MOD-9"}]),
     "API contract API-1 refers to unknown module MOD-9"),
    (sa_output(api_contracts=[{**sa_output()["api_contracts"][0], "fr_ids": ["FR-9"]}]),
     "API contract API-1 refers to unknown functional requirements ['FR-9']"),
    (sa_output(data_models=[{**sa_output()["data_models"][0], "module_id": "MOD-9"}]),
     "data model DM-1 refers to unknown module MOD-9"),
    (sa_output(security_architecture={"auth_strategy": "x", "nfr_ids": []}),
     "security_architecture.nfr_ids must include the security NFRs ['NFR-2']"),
    (sa_output(security_architecture={"auth_strategy": "x", "nfr_ids": ["NFR-2", "NFR-7"]}),
     "security_architecture refers to unknown NFRs ['NFR-7']"),
    (_security(), "security NFRs exist: describe auth"),
    (sa_output(data_models=[{**sa_output()["data_models"][0], "entity_ids": ["ENT-2", "ENT-9"]}]),
     "data model DM-1 refers to unknown entities ['ENT-9']"),
    (sa_output(data_models=[{**sa_output()["data_models"][0], "entity_ids": []}]),
     "entity ENT-2 is not mapped to any data model entity_ids or module owns_entities"),
    (sa_output(modules=_mod(1, owns_entities=[])), "entity ENT-1 is not mapped"),
    (sa_output(api_contracts=[sa_output()["api_contracts"][0],
                              {**sa_output()["api_contracts"][1], "integration_ids": ["INT-1", "INT-7"]}]),
     "API contract API-2 refers to unknown integrations ['INT-7']"),
    (sa_output(api_contracts=[sa_output()["api_contracts"][0],
                              {**sa_output()["api_contracts"][1], "integration_ids": []}]),
     "integration INT-1 is not covered by any API contract integration_ids"),
    (sa_output(runtime_contract={**sa_output()["runtime_contract"], "env_vars": {"OPENAI_KEY": "sk-abcdefghijklmnop"}}),
     "env var OPENAI_KEY looks like a real secret"),
])
def test_rule_violations_are_sent_back_then_fixed(tmp_path, bad, problem):
    artifacts = ready(tmp_path)
    runtime = FakeRuntime(bad, sa_output())
    run(solution_architect.run(artifacts, runtime, CONFIG))
    violations = runtime.requests[1][1]["artifacts"]["rule_violations"]
    assert any(problem in item for item in violations), violations
    assert runtime.requests[1][1]["artifacts"]["previous_output"]["summary"] == "Web app Python một khối"


def test_secret_values_never_reach_the_violation_text(tmp_path):
    secret = "ghp_" + "A" * 30
    bad = sa_output(runtime_contract={**sa_output()["runtime_contract"], "env_vars": {"GH": secret}})
    runtime = FakeRuntime(bad, sa_output())
    run(solution_architect.run(ready(tmp_path), runtime, CONFIG))
    assert secret not in json.dumps(runtime.requests[1][1]["artifacts"]["rule_violations"])


def test_no_security_nfr_means_security_architecture_may_stay_empty(tmp_path):
    spec = deepcopy(BA_SPEC)
    spec["non_functional_requirements"] = spec["non_functional_requirements"][:1]
    artifacts = approve_ba_spec(Artifacts(tmp_path), spec)
    runtime = FakeRuntime(sa_output(security_architecture={}))
    run(solution_architect.run(artifacts, runtime, CONFIG))
    assert len(runtime.requests) == 1
    assert "_Không có yêu cầu bảo mật riêng._" in artifacts.read_text("solution_architecture.md")


def test_still_wrong_after_the_retries_fails(tmp_path):
    bad = sa_output(modules=_mod(0, depends_on=["MOD-1"]))
    with pytest.raises(FlowError, match="vi phạm luật sau 2 lần"):
        run(solution_architect.run(ready(tmp_path), FakeRuntime(bad, bad), CONFIG))


# ---------------------------------------------------------------- revise, mất duyệt

def test_feedback_revises_the_draft_and_unapproves_the_project_manager(tmp_path):
    artifacts = ready(tmp_path)
    run(solution_architect.run(artifacts, FakeRuntime(sa_output()), CONFIG))
    gate.approve(artifacts, "solution_architect")
    run(project_manager.run(artifacts, FakeRuntime(pm_output()), CONFIG))
    gate.approve(artifacts, "project_manager")

    revised_modules = _mod(1, name="basket")
    revised = FakeRuntime(sa_output(modules=revised_modules, summary="Web app Python, đổi tên giỏ"))
    result = run(solution_architect.run(artifacts, revised, CONFIG, note="đổi tên module giỏ hàng"))
    sent = revised.requests[0][1]["artifacts"]
    assert sent["user_feedback"] == ["đổi tên module giỏ hàng"]
    assert sent["current_draft"]["modules"][1]["name"] == "cart"
    assert result["changes"] == ["~ module MOD-2 basket", "~ summary"]
    assert artifacts.read_json("solution_architecture.json")["modules"][1]["name"] == "basket"
    assert "### MOD-2 · basket" in artifacts.read_text("solution_architecture.md")
    assert gate.state(artifacts, "solution_architect") == "draft"
    assert gate.state(artifacts, "project_manager") == "draft" and not gate.can_run(artifacts, "project_manager")


# ---------------------------------------------------------------- CLI

def test_cli_solution_architect_revise_approve(tmp_path, capsys):
    artifacts = ready(tmp_path)
    runtime = FakeRuntime(sa_output(), sa_output(summary="bản sửa"))

    async def open_runtime(workspace, request_text, flow, model=None, **kwargs):
        return runtime

    assert cli.main(["solution_architect", "--workspace", str(tmp_path), "--observe", "none"],
                    open_runtime=open_runtime) == 0
    assert "solution_architect: bản nháp Python, 3 module" in capsys.readouterr().out
    assert cli.main(["revise", "solution_architect", "--workspace", str(tmp_path), "--note", "đổi tóm tắt",
                     "--observe", "none"], open_runtime=open_runtime) == 0
    assert "~ summary" in capsys.readouterr().out
    assert cli.main(["approve", "solution_architect", "--workspace", str(tmp_path)]) == 0
    assert gate.state(artifacts, "solution_architect") == "approved"
    capsys.readouterr()
    assert cli.main(["status", "--workspace", str(tmp_path)]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert [line.split()[:2] for line in lines[:4]] == [
        ["clarify", "approved"], ["business_analyst", "approved"], ["solution_architect", "approved"],
        ["project_manager", "missing"]]
    assert not any("chưa triển khai" in line for line in lines)


# ---------------------------------------------------------------- hạ tầng MAS thật

def test_runs_through_the_real_agent_runner_with_a_viewable_log(tmp_path):
    from raac.llm.config import LLMConfig
    from raac.pre_executor.runtime import open_runtime

    registry = tmp_path / "registry"
    shutil.copytree(ROOT / "registry", registry)
    seen = []

    def respond(http_request):
        seen.append(json.loads(http_request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(sa_output(), ensure_ascii=False)}}],
                                         "usage": {"prompt_tokens": 70, "completion_tokens": 40}})

    repo = tmp_path / "shop"
    repo.mkdir()
    artifacts = ready(repo)

    async def scenario():
        runtime = await open_runtime(repo, artifacts.request(), "solution_architect", stream=False,
                                     registry_dir=registry, results_dir=tmp_path / "results",
                                     llm_config=LLMConfig(model="fixture"), transport=httpx.MockTransport(respond))
        try:
            result = await solution_architect.run(artifacts, runtime, CONFIG)
        finally:
            await runtime.aclose()
        return runtime, result

    runtime, result = run(scenario())
    saved = artifacts.read_json("solution_architecture.json")
    assert result["status"] == "draft" and [m["id"] for m in saved["modules"]] == ["MOD-1", "MOD-2", "MOD-3"]
    system = seen[0]["messages"][0]["content"]
    assert "Solution Architect" in system and "MOD-" in system
    assert "Giỏ hàng" in json.dumps(seen[0], ensure_ascii=False)
    events = [json.loads(line)["event"] for line in runtime.log_path.read_text(encoding="utf-8").splitlines()]
    assert events[:2] == ["run_started", "flow_started"] and "llm_request" in events and events[-1] == "flow_completed"
