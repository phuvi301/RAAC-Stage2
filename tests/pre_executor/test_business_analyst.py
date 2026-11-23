"""Flow business_analyst (gộp requirements + profile): ba_spec.json với F/SC/ENT/NFR/INT/UF/BR/FR/AC/EC/OQ."""

import asyncio
import json
import shutil
from copy import deepcopy
from pathlib import Path

import httpx
import pytest
from pe_helpers import BA_SPEC, pm_output, sa_output, with_coverage

from raac.pre_executor import cli, gate
from raac.pre_executor.artifacts import Artifacts
from raac.pre_executor.config import PreExecutorConfig
from raac.pre_executor.flows import (
    business_analyst,
    clarify,
    project_manager,
    solution_architect,
)
from raac.pre_executor.flows.base import FlowError

ROOT = Path(__file__).resolve().parents[2]
CONFIG = PreExecutorConfig(max_flow_retries=1, max_clarify_rounds=2)


class FakeRuntime:
    def __init__(self, *outputs):
        self.outputs, self.requests = list(outputs), []
        self.run_id, self.task_id = "chain-fake", "task-fake"

    async def run(self, role_name, request):
        self.requests.append((role_name, request))
        return with_coverage(self.outputs.pop(0))

    async def aclose(self, *, status="completed"):
        pass


def run(coro):
    return asyncio.run(coro)


def spec(**changes):
    data = deepcopy(BA_SPEC)
    data.update(changes)
    return data


def clarified(tmp_path, *, assumed_question=False) -> Artifacts:
    """Chạy clarify thật (FakeRuntime). assumed_question: thêm một câu Q1 mà người dùng giữ khuyến nghị."""
    artifacts = Artifacts(tmp_path)
    artifacts.save_request("Làm website bán quần áo")
    questions = [{"id": "a", "text": "Giao hàng ở đâu?", "default_answer": "toàn quốc"}] if assumed_question else []
    run(clarify.run(artifacts, FakeRuntime({"complete": True, "questions": questions}), CONFIG))
    if assumed_question:
        run(clarify.run(artifacts, FakeRuntime(), CONFIG))  # chốt với câu trả lời khuyến nghị
    assert gate.state(artifacts, "clarify") == "approved"
    return artifacts


# ---------------------------------------------------------------- gate

def test_needs_clarify_to_be_done(tmp_path):
    artifacts = Artifacts(tmp_path)
    artifacts.save_request("Làm website")
    with pytest.raises(gate.GateError):
        run(business_analyst.run(artifacts, FakeRuntime(spec()), CONFIG))


def test_writes_the_ba_spec_from_the_clarification(tmp_path):
    artifacts = clarified(tmp_path)
    runtime = FakeRuntime(spec())
    result = run(business_analyst.run(artifacts, runtime, CONFIG))
    role, request = runtime.requests[0]
    assert role == "BusinessAnalyst" and request["output_schema"]["title"] == "BusinessAnalystOutput"
    assert set(request["artifacts"]) == {"request", "clarification", "coverage", "workspace_mode", "user_feedback"}
    assert request["artifacts"]["workspace_mode"] == "greenfield"
    data = artifacts.read_json("ba_spec.json")
    assert data["key_entities"][1]["id"] == "ENT-2" and data["external_integrations"][0]["id"] == "INT-1"
    assert data["open_questions"][0] == {"id": "OQ-1", "text": "admin có cần xuất báo cáo?", "feature_ids": ["F-3"]}
    assert not artifacts.path("requirement_profile.json").exists()
    assert gate.state(artifacts, "business_analyst") == "draft" and artifacts.read_ba_spec() == data
    assert ("business_analyst: bản nháp 3 chức năng, 4 yêu cầu chi tiết, 5 AC, 2 NFR, 2 trường hợp biên, "
            "2 thực thể, 1 tích hợp ngoài, 2 câu hỏi mở") in result["message"]
    markdown = artifacts.read_text("ba_spec.md")
    for text in ("# Đặc tả nghiệp vụ (Business Analyst)", "### F-1 · Giỏ hàng — ưu tiên cao",
                 "- FR-1.AC2: **Cho** bối cảnh FR-1.AC2, **khi** thao tác, **thì** kết quả FR-1.AC2",
                 "- BR-1: tối đa 10 món", "- EC-1: hết hàng → báo lỗi", "**UF-1 · Mua hàng**",
                 "- **ENT-2 · Đơn hàng**: đơn của khách",
                 "| NFR-2 | security | chỉ admin xử lý đơn | 100% route admin | F-3 |",
                 "| INT-1 | Dịch vụ giao hàng | tính phí ship | dùng phí cố định | F-2 |",
                 "- admin là nhân viên nội bộ _(F-3)_", "- OQ-2: có voucher? _(toàn hệ thống)_"):
        assert text in markdown, text


def test_stale_and_revise_unapprove_the_later_flows(tmp_path):
    artifacts = clarified(tmp_path)
    run(business_analyst.run(artifacts, FakeRuntime(spec()), CONFIG))
    gate.approve(artifacts, "business_analyst")
    run(solution_architect.run(artifacts, FakeRuntime(sa_output()), CONFIG))
    gate.approve(artifacts, "solution_architect")
    run(project_manager.run(artifacts, FakeRuntime(pm_output()), CONFIG))
    gate.approve(artifacts, "project_manager")

    revised = spec(key_entities=[*BA_SPEC["key_entities"],
                                 {"id": "ENT-3", "name": "Khách", "description": "người mua"}])
    runtime = FakeRuntime(revised)
    result = run(business_analyst.run(artifacts, runtime, CONFIG, note="thêm thực thể khách hàng"))
    sent = runtime.requests[0][1]["artifacts"]
    assert sent["user_feedback"] == ["thêm thực thể khách hàng"] and sent["current_draft"] == artifacts.read_json(
        "ba_spec.json") | {"key_entities": BA_SPEC["key_entities"]}
    assert result["changes"] == ["+ thực thể ENT-3 Khách"]
    assert [gate.state(artifacts, flow) for flow in ("business_analyst", "solution_architect", "project_manager")] == [
        "draft", "draft", "draft"]

    gate.approve(artifacts, "business_analyst")
    data = artifacts.read_json("ba_spec.json")
    artifacts.write_json("ba_spec.json", {**data, "constraints": ["sửa tay"]})
    assert gate.state(artifacts, "business_analyst") == "stale" and not gate.can_run(artifacts, "solution_architect")


# ---------------------------------------------------------------- schema

def _entity_without_id():
    data = spec()
    del data["key_entities"][0]["id"]
    return data


@pytest.mark.parametrize("bad", [spec(extra="x"), _entity_without_id(), spec(features=[]), spec(user_flows=[]),
                                 spec(open_questions=["chuỗi thay vì OQ"]),
                                 spec(external_integrations=[{"id": "INT-x", "name": "a", "purpose": "b"}])])
def test_schema_errors_fail_the_flow(tmp_path, bad):
    with pytest.raises(FlowError):
        run(business_analyst.run(clarified(tmp_path), FakeRuntime(bad), CONFIG))


# ---------------------------------------------------------------- luật kiểm bằng code

def _dup(field, index=0, **changes):
    items = deepcopy(BA_SPEC[field])
    return spec(**{field: [*items, {**items[index], **changes}]})


def _with(field, index, **changes):
    items = deepcopy(BA_SPEC[field])
    items[index].update(changes)
    return spec(**{field: items})


def _frs(index, **changes):
    return _with("functional_requirements", index, **changes)


@pytest.mark.parametrize("bad, problem", [
    (_dup("features", name="Khác"), "duplicate feature id F-1"),
    (_dup("success_criteria", outcome="khác"), "duplicate success criterion id SC-1"),
    (_dup("key_entities", name="Khác"), "duplicate entity id ENT-1"),
    (_dup("non_functional_requirements", requirement="khác"), "duplicate NFR id NFR-1"),
    (_dup("external_integrations", name="Khác"), "duplicate integration id INT-1"),
    (_dup("user_flows", name="Khác"), "duplicate user flow id UF-1"),
    (_dup("business_rules", rule="khác"), "duplicate business rule id BR-1"),
    (_dup("functional_requirements", description="khác"), "duplicate functional requirement id FR-1"),
    (_frs(0, acceptance_criteria=[BA_SPEC["functional_requirements"][0]["acceptance_criteria"][0]] * 2),
     "duplicate acceptance criterion id FR-1.AC1"),
    (_dup("edge_cases", situation="khác"), "duplicate edge case id EC-1"),
    (_dup("open_questions", text="khác"), "duplicate open question id OQ-1"),
    (_with("features", 1, name="  giỏ HÀNG "), "duplicate feature name giỏ hàng"),
    (_with("key_entities", 1, name="giỏ"), "duplicate entity name giỏ"),
    (_with("features", 0, sources=["request", "Q9"]), "F-1 cites unknown sources ['Q9']"),
    (_with("features", 0, why_priority=""), "F-1 needs why_priority and independent_test"),
    (_with("features", 0, independent_test=" "), "F-1 needs why_priority and independent_test"),
    (spec(success_criteria=[]), "add at least one measurable success criterion"),
    (_with("success_criteria", 0, outcome="Trang chạy trên React"), "SC-1 names a technology (React)"),
    (_with("success_criteria", 0, metric="truy vấn PostgreSQL < 1 s"), "SC-1 names a technology (PostgreSQL)"),
    (_with("success_criteria", 0, outcome="chạy được trong Docker"), "SC-1 names a technology (Docker)"),
    (_with("assumptions", 0, clarification_id="Q7"), "assumption cites unknown clarification id Q7"),
    (spec(open_questions=[{"id": f"OQ-{n}", "text": f"câu {n}"} for n in range(1, 7)]), "at most 5 open questions"),
    (_with("non_functional_requirements", 0, feature_ids=["F-9"]), "NFR NFR-1 refers to unknown features ['F-9']"),
    (_with("external_integrations", 0, feature_ids=["F-8"]), "integration INT-1 refers to unknown features ['F-8']"),
    (_with("assumptions", 1, feature_ids=["F-7"]), "assumption #2 refers to unknown features ['F-7']"),
    (_with("open_questions", 0, feature_ids=["F-6"]), "open question OQ-1 refers to unknown features ['F-6']"),
    (_with("user_flows", 0, feature_ids=["F-1", "F-5"]), "user flow UF-1 refers to unknown features ['F-5']"),
    (_with("business_rules", 0, feature_id="F-4"), "business rule BR-1 refers to unknown features ['F-4']"),
    (_frs(3, feature_id="F-9"), "functional requirement FR-4 refers to unknown features ['F-9']"),
    (_with("edge_cases", 1, feature_id="F-9"), "edge case EC-2 refers to unknown features ['F-9']"),
    (_frs(1, acceptance_criteria=[{**BA_SPEC["functional_requirements"][1]["acceptance_criteria"][0],
                                   "id": "FR-1.AC9"}]), "FR-2 has acceptance criteria named ['FR-1.AC9']"),
    (spec(functional_requirements=BA_SPEC["functional_requirements"][:3]), "feature F-3 has no functional requirement"),
    (spec(user_flows=[{**BA_SPEC["user_flows"][0], "feature_ids": ["F-2"]}]),
     "high-priority feature F-1 appears in no user flow"),
    (spec(edge_cases=BA_SPEC["edge_cases"][1:]), "high-priority feature F-1 has no edge case"),
])
def test_rule_violations_are_sent_back_then_fixed(tmp_path, bad, problem):
    artifacts = clarified(tmp_path)
    runtime = FakeRuntime(bad, spec())
    run(business_analyst.run(artifacts, runtime, CONFIG))
    violations = runtime.requests[1][1]["artifacts"]["rule_violations"]
    assert any(problem in item for item in violations), violations


def test_generic_product_words_are_not_treated_as_technology(tmp_path):
    good = _with("success_criteria", 0, outcome="API trả về kết quả trong < 200 ms",
                 metric="endpoint, CLI và server đều trả lời p95 < 200 ms")
    runtime = FakeRuntime(good)
    run(business_analyst.run(clarified(tmp_path), runtime, CONFIG))
    assert len(runtime.requests) == 1


def test_a_kept_recommendation_must_become_an_assumption(tmp_path):
    artifacts = clarified(tmp_path, assumed_question=True)
    assert artifacts.read_json("clarification.json")[0]["assumed"] is True
    fixed = _with("assumptions", 0, clarification_id="Q1")
    runtime = FakeRuntime(spec(), fixed)
    run(business_analyst.run(artifacts, runtime, CONFIG))
    assert runtime.requests[1][1]["artifacts"]["rule_violations"] == [
        "answer Q1 kept the suggested default: record it in assumptions with clarification_id"]
    assert artifacts.read_json("ba_spec.json")["assumptions"][0]["clarification_id"] == "Q1"


def test_still_wrong_after_the_retries_fails(tmp_path):
    bad = spec(success_criteria=[])
    with pytest.raises(FlowError, match="vi phạm luật sau 2 lần"):
        run(business_analyst.run(clarified(tmp_path), FakeRuntime(bad, bad), CONFIG))


# ---------------------------------------------------------------- CLI

def test_cli_business_analyst_revise_approve(tmp_path, capsys):
    artifacts = clarified(tmp_path)
    runtime = FakeRuntime(spec(), spec(constraints=["chỉ COD", "chỉ giao nội thành"]))

    async def open_runtime(workspace, request_text, flow, model=None, **kwargs):
        return runtime

    assert cli.main(["business_analyst", "--workspace", str(tmp_path), "--observe", "none"],
                    open_runtime=open_runtime) == 0
    assert "business_analyst: bản nháp 3 chức năng" in capsys.readouterr().out
    assert cli.main(["revise", "business_analyst", "--workspace", str(tmp_path), "--note", "x"],
                    open_runtime=open_runtime) == 2
    assert cli.main(["revise", "business_analyst", "--workspace", str(tmp_path), "--note", "chỉ giao nội thành",
                     "--observe", "none"], open_runtime=open_runtime) == 0
    assert "~ constraints" in capsys.readouterr().out
    assert cli.main(["approve", "business_analyst", "--workspace", str(tmp_path)]) == 0
    assert gate.state(artifacts, "business_analyst") == "approved" and gate.can_run(artifacts, "solution_architect")


# ---------------------------------------------------------------- hạ tầng MAS thật

def test_runs_through_the_real_agent_runner_with_a_viewable_log(tmp_path):
    from raac.llm.config import LLMConfig
    from raac.pre_executor.runtime import open_runtime

    registry = tmp_path / "registry"
    shutil.copytree(ROOT / "registry", registry)
    seen = []

    def respond(http_request):
        seen.append(json.loads(http_request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(spec(), ensure_ascii=False)}}],
                                         "usage": {"prompt_tokens": 80, "completion_tokens": 50}})

    repo = tmp_path / "shop"
    repo.mkdir()
    artifacts = clarified(repo)

    async def scenario():
        runtime = await open_runtime(repo, artifacts.request(), "business_analyst", stream=False,
                                     registry_dir=registry, results_dir=tmp_path / "results",
                                     llm_config=LLMConfig(model="fixture"), transport=httpx.MockTransport(respond))
        try:
            result = await business_analyst.run(artifacts, runtime, CONFIG)
        finally:
            await runtime.aclose()
        return runtime, result

    runtime, result = run(scenario())
    assert result["status"] == "draft" and artifacts.read_json("ba_spec.json")["features"][0]["id"] == "F-1"
    system = seen[0]["messages"][0]["content"]
    assert "Business Analyst" in system and "feature_ids" in system
    events = [json.loads(line)["event"] for line in runtime.log_path.read_text(encoding="utf-8").splitlines()]
    assert events[:2] == ["run_started", "flow_started"] and "llm_request" in events and events[-1] == "flow_completed"
