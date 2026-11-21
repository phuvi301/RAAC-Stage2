"""Flow spec (Business Analyst): sinh thẳng Requirement Spec; luật, góp ý, approve, CLI, TUI."""

import asyncio

import pytest

from raac.pre_executor import cli, gate
from raac.pre_executor.artifacts import Artifacts
from pe_helpers import with_coverage
from raac.pre_executor.config import PreExecutorConfig
from raac.pre_executor.flows import clarify, spec
from raac.pre_executor.flows.base import FlowError

CONFIG = PreExecutorConfig(max_flow_retries=1, max_clarify_rounds=2)


def feature(fid, name, *, sources=("request",), priority="high"):
    return {"id": fid, "name": name, "description": f"{name} cho khách", "priority": priority,
            "why_priority": "cần cho bán hàng", "independent_test": f"thử riêng {name}", "sources": list(sources)}


def fr(frid, feature_id, *criteria, priority="high"):
    criteria = criteria or (f"{frid}.AC1",)
    return {"id": frid, "feature_id": feature_id, "description": f"Hệ thống phải {frid}", "priority": priority,
            "acceptance_criteria": [{"id": cid, "given": f"có giỏ {cid}", "when": "thêm món", "then": f"kiểm {cid}"}
                                    for cid in criteria]}


def draft(**changes):
    """Một Requirement Spec hợp lệ với 2 chức năng: F-1 (cao), F-2 (trung bình)."""
    data = {
        "overview": {"summary": "Website bán quần áo", "product_goal": "Bán online", "target_users": ["khách lẻ"],
                     "platforms": ["web"], "scope": "bản đầu"},
        "features": [feature("F-1", "Giỏ hàng"), feature("F-2", "Quản lý đơn", priority="medium")],
        "success_criteria": [{"id": "SC-1", "outcome": "Khách đặt hàng nhanh", "metric": "dưới 3 phút"}],
        "key_entities": [{"name": "Đơn hàng", "description": "đơn của khách", "attributes": ["mã"], "relations": []}],
        "non_functional_requirements": [{"id": "NFR-1", "category": "performance_efficiency",
                                         "requirement": "Trang nhanh", "metric": "p95 < 2 s"}],
        "user_flows": [{"id": "UF-1", "name": "Mua hàng", "actor": "khách", "feature_ids": ["F-1"],
                        "steps": ["chọn", "thêm vào giỏ"]}],
        "business_rules": [{"id": "BR-1", "feature_id": "F-1", "rule": "tối đa 10 món"}],
        "functional_requirements": [fr("FR-1", "F-1", "FR-1.AC1", "FR-1.AC2"), fr("FR-2", "F-2")],
        "edge_cases": [{"id": "EC-1", "feature_id": "F-1", "situation": "hết hàng", "expected": "báo lỗi"}],
        "constraints": ["chỉ COD"],
        "assumptions": [{"text": "Thanh toán VNPay", "clarification_id": "Q2"}],
        "open_questions": ["có voucher?"],
    }
    data.update(changes)
    return data


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


def clarified(tmp_path):
    """Workspace đã qua clarify: Q1 người dùng tự trả lời, Q2 để nguyên khuyến nghị."""
    artifacts = Artifacts(tmp_path)
    artifacts.save_request("Làm website bán hàng")
    run(clarify.run(artifacts, FakeRuntime({"complete": True, "questions": [
        {"id": "a", "text": "Bán gì?", "default_answer": "đồ điện tử"},
        {"id": "b", "text": "Thanh toán?", "default_answer": "VNPay"}]}), CONFIG))
    clarify.save_answers(artifacts, {"Q1": "quần áo"})
    run(clarify.run(artifacts, FakeRuntime(), CONFIG))  # tự chốt
    assert gate.state(artifacts, "clarify") == "approved"
    return artifacts


def test_needs_clarify_to_be_finished_first(tmp_path):
    artifacts = Artifacts(tmp_path)
    artifacts.save_request("Làm website bán hàng")
    with pytest.raises(gate.GateError):
        run(spec.run(artifacts, FakeRuntime(draft()), CONFIG))


def test_one_business_analyst_writes_the_whole_spec(tmp_path):
    artifacts = clarified(tmp_path)
    runtime = FakeRuntime(draft(features=[feature("F-1", "Giỏ hàng"),
                                          feature("F-2", "Quản lý đơn", priority="medium", sources=["request", "Q1"])]))
    result = run(spec.run(artifacts, runtime, CONFIG))
    role, request = runtime.requests[0]
    sent = request["artifacts"]
    assert role == "BusinessAnalyst" and request["output_schema"]["title"] == "RequirementSpecOutput"
    assert sent["request"] == "Làm website bán hàng" and "current_draft" not in sent and "coverage" in sent
    assert [(item["id"], item["answer"], item["assumed"]) for item in sent["clarification"]] == [
        ("Q1", "quần áo", False), ("Q2", "VNPay", True)]
    assert "2 chức năng" in result["message"] and "3 AC" in result["message"]
    assert gate.state(artifacts, "spec") == "draft" and not gate.can_run(artifacts, "tech_select")
    data = artifacts.read_json("requirement_spec.json")
    assert data["features"][1]["sources"] == ["request", "Q1"] and data["edge_cases"][0]["id"] == "EC-1"
    markdown = artifacts.read_text("requirement_spec.md")
    for piece in ("# Requirement Spec", "### F-1 · Giỏ hàng — ưu tiên cao", "**Kiểm độc lập:** thử riêng Giỏ hàng",
                  "| SC-1 | Khách đặt hàng nhanh | dưới 3 phút |", "- FR-1.AC2: **Cho** có giỏ FR-1.AC2, **khi** thêm món",
                  "BR-1: tối đa 10 món", "EC-1: hết hàng → báo lỗi", "**UF-1 · Mua hàng**", "## Thực thể chính",
                  "| NFR-1 | performance_efficiency |"):
        assert piece in markdown, piece


@pytest.mark.parametrize("bad, problem", [
    (draft(features=[feature("F-1", "Giỏ hàng"), feature("F-1", "Đặt hàng")]), "duplicate feature id F-1"),
    (draft(features=[feature("F-1", "Giỏ hàng"), feature("F-2", "giỏ  HÀNG", priority="medium")]),
     "duplicate feature name"),
    (draft(features=[feature("F-1", "Giỏ hàng", sources=["Q9"]), feature("F-2", "Đơn", priority="medium")]),
     "F-1 cites unknown sources ['Q9']"),
    (draft(assumptions=[]), "answer Q2 kept the suggested default"),
    (draft(features=[feature("F-1", "Giỏ hàng") | {"why_priority": ""}, feature("F-2", "Đơn", priority="medium")]),
     "F-1 needs why_priority and independent_test"),
    (draft(success_criteria=[]), "add at least one measurable success criterion"),
    (draft(success_criteria=[{"id": "SC-1", "outcome": "API phản hồi nhanh", "metric": "< 200 ms"}]),
     "SC-1 mentions implementation (API)"),
    (draft(open_questions=["a", "b", "c", "d"]), "at most 3 open questions"),
    (draft(functional_requirements=[fr("FR-1", "F-1")]), "feature F-2 has no functional requirement"),
    (draft(functional_requirements=[fr("FR-1", "F-1"), fr("FR-2", "F-9")]), "FR-2 refers to unknown feature F-9"),
    (draft(functional_requirements=[fr("FR-1", "F-1", "FR-2.AC1"), fr("FR-2", "F-2")]),
     "FR-1 has acceptance criteria named ['FR-2.AC1']"),
    (draft(user_flows=[{"id": "UF-1", "name": "Xử lý đơn", "actor": "admin", "feature_ids": ["F-2"], "steps": ["xem"]}]),
     "high-priority feature F-1 appears in no user flow"),
    (draft(business_rules=[{"id": "BR-1", "feature_id": "F-7", "rule": "x"}]), "BR-1 refers to unknown feature F-7"),
    (draft(edge_cases=[]), "high-priority feature F-1 has no edge case"),
])
def test_rule_violations_are_sent_back_then_fixed(tmp_path, bad, problem):
    artifacts = clarified(tmp_path)
    runtime = FakeRuntime(bad, draft())
    run(spec.run(artifacts, runtime, CONFIG))
    violations = runtime.requests[1][1]["artifacts"]["rule_violations"]
    assert any(problem in item for item in violations), violations
    assert runtime.requests[1][1]["artifacts"]["previous_output"]["features"]


def test_schema_rejects_bad_ids_missing_metric_and_requirements_without_criteria(tmp_path):
    artifacts = clarified(tmp_path)
    no_ac = draft()
    no_ac["functional_requirements"][1]["acceptance_criteria"] = []
    for bad in (draft(features=[feature("1", "Giỏ hàng")]),
                draft(non_functional_requirements=[{"id": "NFR-1", "category": "performance_efficiency",
                                                    "requirement": "nhanh", "metric": ""}]),
                no_ac):
        with pytest.raises(FlowError):
            run(spec.run(artifacts, FakeRuntime(bad), CONFIG))


def test_feedback_revises_the_draft_shows_changes_and_approve_unlocks_design(tmp_path):
    artifacts = clarified(tmp_path)
    run(spec.run(artifacts, FakeRuntime(draft()), CONFIG))
    revised = draft(features=[feature("F-1", "Giỏ hàng miễn phí giao hàng"), feature("F-2", "Quản lý đơn",
                                                                                     priority="medium")])
    runtime = FakeRuntime(revised)
    result = run(spec.run(artifacts, runtime, CONFIG, note="F-1 miễn phí giao hàng"))
    sent = runtime.requests[0][1]["artifacts"]
    assert sent["user_feedback"] == ["F-1 miễn phí giao hàng"] and sent["current_draft"]["features"][0]["name"] == "Giỏ hàng"
    assert result["changes"] == ["~ chức năng F-1 Giỏ hàng miễn phí giao hàng"]
    gate.approve(artifacts, "spec")
    assert gate.can_run(artifacts, "tech_select")
    artifacts.write_text("requirement_spec.json", artifacts.read_text("requirement_spec.json").replace("miễn phí", "có phí"))
    assert gate.state(artifacts, "spec") == "stale"  # sửa tay sau khi duyệt thì phải duyệt lại


def test_cli_spec_revise_approve(tmp_path, capsys):
    artifacts = clarified(tmp_path)
    runtime = FakeRuntime(draft(), draft(open_questions=[]))

    async def open_runtime(workspace, request_text, flow, model=None, **kwargs):
        return runtime

    assert cli.main(["spec", "--workspace", str(tmp_path)], open_runtime=open_runtime) == 0
    assert "spec: bản nháp 2 chức năng" in capsys.readouterr().out
    assert cli.main(["revise", "spec", "--workspace", str(tmp_path), "--note", "x"], open_runtime=open_runtime) == 2
    assert cli.main(["revise", "spec", "--workspace", str(tmp_path), "--note", "bỏ câu hỏi mở"],
                    open_runtime=open_runtime) == 0
    assert "~ open_questions" in capsys.readouterr().out
    assert cli.main(["approve", "spec", "--workspace", str(tmp_path)]) == 0
    assert gate.state(artifacts, "spec") == "approved"
    with pytest.raises(SystemExit):  # flow cũ không còn
        cli.main(["requirements", "--workspace", str(tmp_path)])


def test_tui_goes_from_clarify_to_the_spec_draft_and_approves(tmp_path):
    pytest.importorskip("textual")
    from textual.widgets import Input

    from raac.pre_executor.tui import DraftScreen, MessageScreen, PreExecutorApp

    artifacts = Artifacts(tmp_path)
    artifacts.save_request("Làm website bán quần áo")
    run(clarify.run(artifacts, FakeRuntime({"complete": True, "questions": []}), CONFIG))
    runtime = FakeRuntime(draft(assumptions=[]), draft(assumptions=[], open_questions=[]))

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
            screen = await wait_for(pilot, DraftScreen, "# Requirement Spec")
            assert "Requirement Spec" in screen.title_text
            screen.query_one(Input).value = "bỏ câu hỏi mở"
            await pilot.press("enter")
            await pilot.pause()
            for _ in range(60):
                await pilot.pause()
                if isinstance(pilot.app.screen, DraftScreen) and "Câu hỏi mở" not in pilot.app.screen.markdown:
                    break
            await pilot.click("#approve")
            done = await wait_for(pilot, MessageScreen, "Đã duyệt requirement spec")
            assert "2 chức năng" in done.message and "3 AC" in done.message
            await pilot.click("#close")
            await pilot.pause()

    asyncio.run(scenario())
    assert runtime.requests[1][1]["artifacts"]["user_feedback"] == ["bỏ câu hỏi mở"]
    assert gate.state(artifacts, "spec") == "approved" and len(app.spec["features"]) == 2
