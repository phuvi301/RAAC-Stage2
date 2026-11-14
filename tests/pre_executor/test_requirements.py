"""Flow requirements (Product Requirement Analyst): tổng quan, chức năng cốt lõi, NFR; góp ý, approve; CLI; TUI."""

import asyncio

import pytest

from raac.pre_executor import cli, gate
from raac.pre_executor.artifacts import Artifacts
from raac.pre_executor.config import PreExecutorConfig
from raac.pre_executor.flows import clarify, requirements
from raac.pre_executor.flows.base import FlowError

CONFIG = PreExecutorConfig(max_flow_retries=1, max_clarify_rounds=2)


def feature(fid, name, *, sources=("request",), priority="high"):
    return {"id": fid, "name": name, "description": f"{name} cho khách", "priority": priority, "sources": list(sources)}


def draft(*features, assumptions=None, nfrs=None):
    return {
        "overview": {"summary": "Website bán quần áo", "product_goal": "Bán online", "target_users": ["khách lẻ"],
                     "platforms": ["web"], "scope": "bản đầu"},
        "features": list(features) or [feature("F-1", "Giỏ hàng")],
        "non_functional_requirements": nfrs if nfrs is not None else [
            {"id": "NFR-1", "category": "performance", "requirement": "Trang nhanh", "metric": "p95 < 2 s"}],
        "assumptions": assumptions if assumptions is not None else [{"text": "Thanh toán VNPay", "clarification_id": "Q2"}],
    }


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


def clarified(tmp_path):
    """Workspace đã qua clarify: Q1 người dùng tự trả lời, Q2 để nguyên gợi ý."""
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
        run(requirements.run(artifacts, FakeRuntime(draft()), CONFIG))


def test_writes_core_features_and_nfrs_from_the_clarification(tmp_path):
    artifacts = clarified(tmp_path)
    runtime = FakeRuntime(draft(feature("F-1", "Giỏ hàng"), feature("F-2", "Đặt hàng", sources=["request", "Q1"])))
    result = run(requirements.run(artifacts, runtime, CONFIG))
    role, request = runtime.requests[0]
    sent = request["artifacts"]
    assert role == "RequirementAnalyst" and request["output_schema"]["title"] == "RequirementsOutput"
    assert sent["request"] == "Làm website bán hàng" and "current_draft" not in sent
    assert [(item["id"], item["answer"], item["assumed"]) for item in sent["clarification"]] == [
        ("Q1", "quần áo", False), ("Q2", "VNPay", True)]
    assert result["features"] == 2 and result["nfrs"] == 1 and gate.state(artifacts, "requirements") == "draft"
    markdown = artifacts.read_text("requirements.md")
    assert "## Chức năng cốt lõi" in markdown and "| F-2 | Đặt hàng |" in markdown and "p95 < 2 s" in markdown
    assert artifacts.read_json("requirements.json")["features"][1]["sources"] == ["request", "Q1"]


@pytest.mark.parametrize("bad, problem", [
    (draft(feature("F-1", "Giỏ hàng"), feature("F-1", "Đặt hàng")), "duplicate feature id F-1"),
    (draft(feature("F-1", "Giỏ hàng"), feature("F-2", "giỏ  HÀNG")), "duplicate feature name"),
    (draft(feature("F-1", "Giỏ hàng", sources=["Q9"])), "F-1 cites unknown sources ['Q9']"),
    (draft(assumptions=[]), "answer Q2 kept the suggested default"),
])
def test_rule_violations_are_sent_back_once_then_fixed(tmp_path, bad, problem):
    artifacts = clarified(tmp_path)
    runtime = FakeRuntime(bad, draft())
    run(requirements.run(artifacts, runtime, CONFIG))
    violations = runtime.requests[1][1]["artifacts"]["rule_violations"]
    assert any(problem in item for item in violations)
    assert runtime.requests[1][1]["artifacts"]["previous_output"]["features"]


def test_schema_rejects_bad_ids_and_missing_metric(tmp_path):
    artifacts = clarified(tmp_path)
    for bad in (draft(feature("1", "Giỏ hàng")),
                draft(nfrs=[{"id": "NFR-1", "category": "performance", "requirement": "nhanh", "metric": ""}])):
        with pytest.raises(FlowError):
            run(requirements.run(artifacts, FakeRuntime(bad), CONFIG))


def test_feedback_revises_the_current_draft_then_approve_unlocks_profile(tmp_path):
    artifacts = clarified(tmp_path)
    run(requirements.run(artifacts, FakeRuntime(draft()), CONFIG))
    revised = FakeRuntime(draft(feature("F-1", "Giỏ hàng"), feature("F-2", "Mã giảm giá", priority="medium")))
    run(requirements.run(artifacts, revised, CONFIG, note="thêm mã giảm giá"))
    sent = revised.requests[0][1]["artifacts"]
    assert sent["user_feedback"] == ["thêm mã giảm giá"] and sent["current_draft"]["features"][0]["name"] == "Giỏ hàng"
    assert not gate.can_run(artifacts, "profile")
    gate.approve(artifacts, "requirements")
    assert gate.can_run(artifacts, "profile")

    artifacts.write_text("requirements.json", artifacts.read_text("requirements.json").replace("Mã giảm giá", "Voucher"))
    assert gate.state(artifacts, "requirements") == "stale"  # sửa tay sau khi duyệt thì phải duyệt lại


def test_cli_revise_and_approve_requirements(tmp_path, capsys):
    artifacts = clarified(tmp_path)
    runtime = FakeRuntime(draft(), draft(feature("F-1", "Giỏ hàng"), feature("F-2", "Voucher")))

    async def open_runtime(workspace, request_text, flow, model=None):
        return runtime

    assert cli.main(["requirements", "--workspace", str(tmp_path)], open_runtime=open_runtime) == 0
    assert "bản nháp 1 chức năng cốt lõi, 1 NFR" in capsys.readouterr().out
    assert cli.main(["revise", "requirements", "--workspace", str(tmp_path), "--note", "thêm voucher"],
                    open_runtime=open_runtime) == 0
    assert cli.main(["approve", "requirements", "--workspace", str(tmp_path)]) == 0
    assert gate.state(artifacts, "requirements") == "approved"
    assert [f["name"] for f in artifacts.read_json("requirements.json")["features"]] == ["Giỏ hàng", "Voucher"]


def test_tui_shows_the_draft_takes_feedback_and_approves(tmp_path):
    pytest.importorskip("textual")
    from textual.widgets import Input

    from raac.pre_executor.tui import DraftScreen, MessageScreen, PreExecutorApp

    artifacts = clarified(tmp_path)
    runtime = FakeRuntime(draft(), draft(feature("F-1", "Giỏ hàng"), feature("F-2", "Voucher")))

    async def open_runtime(workspace, request_text, flow, model=None):
        return runtime

    app = PreExecutorApp(artifacts, open_runtime=open_runtime)

    async def wait_for(pilot, screen_type):
        for _ in range(50):
            await pilot.pause()
            if isinstance(pilot.app.screen, screen_type):
                return pilot.app.screen
        raise AssertionError(type(pilot.app.screen).__name__)

    async def scenario():
        async with app.run_test(size=(120, 40)) as pilot:
            screen = await wait_for(pilot, DraftScreen)  # clarify đã chốt: vào thẳng requirements
            assert "Giỏ hàng" in screen.markdown
            screen.query_one(Input).value = "thêm voucher"
            await pilot.press("enter")
            await pilot.pause()
            for _ in range(50):
                await pilot.pause()
                if isinstance(pilot.app.screen, DraftScreen) and "Voucher" in pilot.app.screen.markdown:
                    break
            await pilot.click("#approve")
            await wait_for(pilot, MessageScreen)
            await pilot.click("#close")
            await pilot.pause()

    asyncio.run(scenario())
    assert runtime.requests[1][1]["artifacts"]["user_feedback"] == ["thêm voucher"]
    assert gate.state(artifacts, "requirements") == "approved" and len(app.requirements["features"]) == 2
