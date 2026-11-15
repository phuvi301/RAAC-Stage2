"""Flow profile (Requirement Profile): user flow, quy tắc nghiệp vụ, FR + AC cho từng chức năng đã duyệt."""

import asyncio

import pytest

from raac.pre_executor import cli, gate
from raac.pre_executor.artifacts import Artifacts
from pe_helpers import with_coverage
from raac.pre_executor.config import PreExecutorConfig
from raac.pre_executor.flows import clarify, profile, requirements
from raac.pre_executor.flows.base import FlowError

CONFIG = PreExecutorConfig(max_flow_retries=1, max_clarify_rounds=2)
REQUIREMENTS = {
    "overview": {"summary": "Website bán quần áo", "product_goal": "Bán online", "target_users": ["khách lẻ"]},
    "features": [
        {"id": "F-1", "name": "Giỏ hàng", "description": "thêm, sửa, xoá", "priority": "high", "why_priority": "lõi",
         "independent_test": "thêm 1 món", "sources": ["request"]},
        {"id": "F-2", "name": "Quản lý đơn", "description": "admin xử lý đơn", "priority": "medium", "why_priority": "cần",
         "independent_test": "đổi trạng thái", "sources": ["request"]},
    ],
    "success_criteria": [{"id": "SC-1", "outcome": "Khách đặt hàng nhanh", "metric": "dưới 3 phút"}],
    "key_entities": [{"name": "Đơn hàng", "description": "đơn của khách", "attributes": ["mã"], "relations": []}],
    "non_functional_requirements": [{"id": "NFR-1", "category": "performance_efficiency", "requirement": "nhanh",
                                     "metric": "p95 < 2 s"}],
    "constraints": ["chỉ COD"], "assumptions": [], "out_of_scope": ["app mobile"], "open_questions": ["có voucher?"],
}


def fr(frid, feature_id, *criteria, priority="high"):
    criteria = criteria or (f"{frid}.AC1",)
    return {"id": frid, "feature_id": feature_id, "description": f"mô tả {frid}", "priority": priority,
            "acceptance_criteria": [{"id": cid, "given": f"có giỏ {cid}", "when": "thêm món", "then": f"kiểm {cid}"}
                                    for cid in criteria]}


def detail(*frs, flows=None, rules=None, cases=None):
    return {
        "edge_cases": cases if cases is not None else [
            {"id": "EC-1", "feature_id": "F-1", "situation": "hết hàng", "expected": "báo lỗi"}],
        "user_flows": flows if flows is not None else [
            {"id": "UF-1", "name": "Mua hàng", "actor": "khách", "feature_ids": ["F-1"], "steps": ["chọn", "thêm vào giỏ"]}],
        "business_rules": rules if rules is not None else [{"id": "BR-1", "feature_id": "F-1", "rule": "tối đa 10 món"}],
        "functional_requirements": list(frs) or [fr("FR-1", "F-1", "FR-1.AC1", "FR-1.AC2"), fr("FR-2", "F-2")],
        "open_questions": ["giới hạn số lượng?"],
    }


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


def approved_requirements(tmp_path):
    artifacts = Artifacts(tmp_path)
    artifacts.save_request("Làm website bán quần áo")
    run(clarify.run(artifacts, FakeRuntime({"complete": True, "questions": []}), CONFIG))
    run(requirements.run(artifacts, FakeRuntime(REQUIREMENTS), CONFIG))
    gate.approve(artifacts, "requirements")
    return artifacts


def test_needs_requirements_to_be_approved(tmp_path):
    artifacts = Artifacts(tmp_path)
    artifacts.save_request("Làm website bán quần áo")
    run(clarify.run(artifacts, FakeRuntime({"complete": True, "questions": []}), CONFIG))
    run(requirements.run(artifacts, FakeRuntime(REQUIREMENTS), CONFIG))  # mới là bản nháp
    with pytest.raises(gate.GateError):
        run(profile.run(artifacts, FakeRuntime(detail()), CONFIG))


def test_details_every_feature_and_carries_the_approved_requirements(tmp_path):
    artifacts = approved_requirements(tmp_path)
    runtime = FakeRuntime(detail())
    result = run(profile.run(artifacts, runtime, CONFIG))
    role, request = runtime.requests[0]
    assert role == "RequirementProfiler" and request["output_schema"]["title"] == "ProfileOutput"
    assert request["artifacts"]["requirements"]["features"][0]["name"] == "Giỏ hàng"
    data = artifacts.read_json("requirement_profile.json")
    assert data["features"] == REQUIREMENTS["features"] and data["constraints"] == ["chỉ COD"]  # chép nguyên
    assert data["success_criteria"] == REQUIREMENTS["success_criteria"] and data["key_entities"][0]["name"] == "Đơn hàng"
    assert data["open_questions"] == ["có voucher?", "giới hạn số lượng?"]
    assert "3 AC" in result["message"] and gate.state(artifacts, "profile") == "draft"
    markdown = artifacts.read_text("requirement_profile.md")
    assert "## F-1 · Giỏ hàng" in markdown and "- FR-1.AC2: **Cho** có giỏ FR-1.AC2, **khi** thêm món, **thì** kiểm FR-1.AC2" in markdown and "BR-1: tối đa 10 món" in markdown
    assert "**UF-1 · Mua hàng**" in markdown and "2. thêm vào giỏ" in markdown
    assert "chép nguyên từ requirements đã duyệt" in markdown and "revise requirements" in markdown


@pytest.mark.parametrize("bad, problem", [
    (detail(fr("FR-1", "F-1")), "feature F-2 has no functional requirement"),
    (detail(fr("FR-1", "F-1"), fr("FR-2", "F-9")), "FR-2 refers to unknown feature F-9"),
    (detail(fr("FR-1", "F-1", "FR-2.AC1"), fr("FR-2", "F-2")), "FR-1 has acceptance criteria named ['FR-2.AC1']"),
    (detail(fr("FR-1", "F-1"), fr("FR-1", "F-2")), "duplicate functional requirement id FR-1"),
    (detail(flows=[{"id": "UF-1", "name": "Xử lý đơn", "actor": "admin", "feature_ids": ["F-2"], "steps": ["xem"]}]),
     "high-priority feature F-1 appears in no user flow"),
    (detail(rules=[{"id": "BR-1", "feature_id": "F-7", "rule": "x"}]), "BR-1 refers to unknown feature F-7"),
    (detail(cases=[]), "high-priority feature F-1 has no edge case"),
    (detail(cases=[{"id": "EC-1", "feature_id": "F-9", "situation": "x", "expected": "y"}]),
     "EC-1 refers to unknown feature F-9"),
])
def test_rule_violations_are_sent_back_then_fixed(tmp_path, bad, problem):
    artifacts = approved_requirements(tmp_path)
    runtime = FakeRuntime(bad, detail())
    run(profile.run(artifacts, runtime, CONFIG))
    assert any(problem in item for item in runtime.requests[1][1]["artifacts"]["rule_violations"])


def test_schema_rejects_a_requirement_without_acceptance_criteria(tmp_path):
    artifacts = approved_requirements(tmp_path)
    bad = detail(fr("FR-1", "F-1"), fr("FR-2", "F-2"))
    bad["functional_requirements"][1]["acceptance_criteria"] = []
    with pytest.raises(FlowError):
        run(profile.run(artifacts, FakeRuntime(bad), CONFIG))


def test_feedback_revises_the_draft_and_changing_requirements_unapproves_the_profile(tmp_path):
    artifacts = approved_requirements(tmp_path)
    run(profile.run(artifacts, FakeRuntime(detail()), CONFIG))
    revised = FakeRuntime(detail(fr("FR-1", "F-1", "FR-1.AC1", "FR-1.AC2", "FR-1.AC3"), fr("FR-2", "F-2")))
    run(profile.run(artifacts, revised, CONFIG, note="thêm AC cho giỏ hàng trống"))
    sent = revised.requests[0][1]["artifacts"]
    assert sent["user_feedback"] == ["thêm AC cho giỏ hàng trống"]
    assert set(sent["current_draft"]) == {"user_flows", "business_rules", "functional_requirements", "edge_cases",
                                         "open_questions"}
    gate.approve(artifacts, "profile")
    assert gate.can_run(artifacts, "tech_select")

    run(requirements.run(artifacts, FakeRuntime(REQUIREMENTS), CONFIG, note="đổi tên"))  # sửa requirements
    assert gate.state(artifacts, "profile") == "draft" and not gate.can_run(artifacts, "profile")


def test_cli_profile_revise_approve(tmp_path, capsys):
    artifacts = approved_requirements(tmp_path)
    runtime = FakeRuntime(detail(), detail())

    async def open_runtime(workspace, request_text, flow, model=None, **kwargs):
        return runtime

    assert cli.main(["profile", "--workspace", str(tmp_path)], open_runtime=open_runtime) == 0
    assert "profile: bản nháp 1 user flow, 1 quy tắc nghiệp vụ, 2 yêu cầu chi tiết, 3 AC, 1 trường hợp biên" in capsys.readouterr().out
    assert cli.main(["revise", "profile", "--workspace", str(tmp_path), "--note", "x"], open_runtime=open_runtime) == 2
    assert cli.main(["revise", "profile", "--workspace", str(tmp_path), "--note", "thêm AC giỏ trống"],
                    open_runtime=open_runtime) == 0
    assert cli.main(["approve", "profile", "--workspace", str(tmp_path)]) == 0
    assert gate.state(artifacts, "profile") == "approved"


def test_tui_goes_from_requirements_to_profile(tmp_path):
    pytest.importorskip("textual")
    from raac.pre_executor.tui import DraftScreen, MessageScreen, PreExecutorApp

    artifacts = Artifacts(tmp_path)
    artifacts.save_request("Làm website bán quần áo")
    run(clarify.run(artifacts, FakeRuntime({"complete": True, "questions": []}), CONFIG))
    runtime = FakeRuntime(REQUIREMENTS, detail())

    async def open_runtime(workspace, request_text, flow, model=None, **kwargs):
        return runtime

    app = PreExecutorApp(artifacts, open_runtime=open_runtime)

    async def wait_for(pilot, screen_type, text=None):
        for _ in range(60):
            await pilot.pause()
            screen = pilot.app.screen
            if isinstance(screen, screen_type) and (text is None or text in getattr(screen, "markdown", "")
                                                     + getattr(screen, "message", "")):
                return screen
        raise AssertionError(type(pilot.app.screen).__name__)

    async def scenario():
        async with app.run_test(size=(120, 40)) as pilot:
            await wait_for(pilot, DraftScreen, "Giỏ hàng")
            await pilot.click("#approve")
            await wait_for(pilot, MessageScreen, "Đã duyệt yêu cầu sản phẩm")
            await pilot.click("#next")
            await wait_for(pilot, DraftScreen, "FR-1.AC1")
            await pilot.click("#approve")
            done = await wait_for(pilot, MessageScreen, "Đã duyệt hồ sơ yêu cầu")
            assert "3 AC" in done.message
            await pilot.click("#close")
            await pilot.pause()

    asyncio.run(scenario())
    assert gate.state(artifacts, "requirements") == "approved" and gate.state(artifacts, "profile") == "approved"
    assert len(app.profile["functional_requirements"]) == 2

