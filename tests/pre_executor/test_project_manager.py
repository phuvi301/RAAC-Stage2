"""Flow project_manager (PM, tầng Macro): DAG task T1..Tn, kiểm phủ, execution_order + context_slice bằng code."""

import asyncio
import json
import random
import shutil
from copy import deepcopy
from pathlib import Path

import httpx
import pytest
from pe_helpers import (
    BA_SPEC,
    approve_ba_spec,
    pm_output,
    pm_tasks,
    sa_output,
    task,
    with_coverage,
)

from raac.pre_executor import cli, gate
from raac.pre_executor.artifacts import Artifacts
from raac.pre_executor.config import PreExecutorConfig
from raac.pre_executor.flows import project_manager, solution_architect
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


def ready(tmp_path, spec=None, architecture=None) -> Artifacts:
    artifacts = approve_ba_spec(Artifacts(tmp_path), spec)
    run(solution_architect.run(artifacts, FakeRuntime(architecture or sa_output()), CONFIG))
    gate.approve(artifacts, "solution_architect")
    return artifacts


def tasks_with(index, **changes):
    items = pm_tasks()
    items[index].update(changes)
    return items


# ---------------------------------------------------------------- gate

def test_needs_an_approved_architecture_and_loses_approval_when_it_is_revised(tmp_path):
    artifacts = approve_ba_spec(Artifacts(tmp_path))
    with pytest.raises(gate.GateError):  # solution_architect missing
        run(project_manager.run(artifacts, FakeRuntime(pm_output()), CONFIG))
    run(solution_architect.run(artifacts, FakeRuntime(sa_output()), CONFIG))
    with pytest.raises(gate.GateError):  # draft
        run(project_manager.run(artifacts, FakeRuntime(pm_output()), CONFIG))
    gate.approve(artifacts, "solution_architect")
    run(project_manager.run(artifacts, FakeRuntime(pm_output()), CONFIG))
    gate.approve(artifacts, "project_manager")
    assert gate.state(artifacts, "project_manager") == "approved"

    data = artifacts.read_json("solution_architecture.json")
    artifacts.write_json("solution_architecture.json", {**data, "summary": "sửa tay"})  # stale
    with pytest.raises(gate.GateError):
        run(project_manager.run(artifacts, FakeRuntime(pm_output()), CONFIG))
    run(solution_architect.run(artifacts, FakeRuntime(sa_output()), CONFIG, note="viết lại kiến trúc"))
    assert gate.state(artifacts, "project_manager") == "draft"


# ---------------------------------------------------------------- schema: không lấn quyền Analyzer/Controller

@pytest.mark.parametrize("field, value", [("suggested_level", "L2"), ("level", "L1"), ("risk", "high"),
                                          ("complexity", 3), ("ambiguity", 2), ("architecture_impact", 1),
                                          ("security", 4)])
def test_schema_rejects_analyzer_and_controller_fields(tmp_path, field, value):
    with pytest.raises(FlowError):
        run(project_manager.run(ready(tmp_path), FakeRuntime(pm_output(tasks_with(1, **{field: value}))), CONFIG))


@pytest.mark.parametrize("bad", [pm_output(tasks=[]), pm_output(tasks_with(0, id="Task1")),
                                 pm_output(tasks_with(0, kind="setup")), pm_output(tasks_with(0, module_ids=[])),
                                 pm_output(tasks_with(0, target_paths=[])), pm_output(execution_order=["T1"])])
def test_schema_errors_fail_the_flow(tmp_path, bad):
    with pytest.raises(FlowError):
        run(project_manager.run(ready(tmp_path), FakeRuntime(bad), CONFIG))


# ---------------------------------------------------------------- luật kiểm bằng code

def _extra_task(**fields):
    return [*pm_tasks(), task("T5", deps=["T1"], **{"fr_ids": ["FR-1"], "acceptance_criteria_ids": ["FR-1.AC1"],
                                                      **fields})]


@pytest.mark.parametrize("bad, problem", [
    (pm_output([*pm_tasks(), task("T2", deps=["T1"], title="Khác", fr_ids=["FR-1"])]), "duplicate task id T2"),
    (pm_output(_extra_task(title="task t2")), "duplicate task title task t2"),
    (pm_output(milestones=[{"id": "M1", "name": "a", "goal": "a", "task_ids": ["T1"]},
                           {"id": "M1", "name": "b", "goal": "b", "task_ids": ["T2"]}]), "duplicate milestone id M1"),
    (pm_output(tasks_with(1, depends_on=["T1", "T9"])), "task T2 depends on unknown tasks ['T9']"),
    (pm_output(tasks_with(1, depends_on=["T1", "T2"])), "task T2 depends on itself"),
    (pm_output(tasks_with(1, depends_on=["T1", "T3"])), "task dependency cycle T2 -> T3 -> T2"),
    (pm_output(tasks_with(1, depends_on=["T1", "T4"])), "task dependency cycle T2 -> T4 -> T3 -> T2"),
    (pm_output(tasks_with(0, depends_on=["T4"])), "at least one task must have no dependencies"),
    (pm_output(tasks_with(0, kind="feature", fr_ids=["FR-1"])), "greenfield workspace: add a bootstrap task"),
    (pm_output(tasks_with(2, depends_on=[])), "task T3 must depend directly or indirectly on a bootstrap task"),
    (pm_output(tasks_with(1, target_paths=["../x"])), "task T2 target_paths: path '../x' must not contain '..'"),
    (pm_output(tasks_with(1, target_paths=["/etc"])), "task T2 target_paths: path '/etc' must be relative"),
    (pm_output(_extra_task(feature_ids=["F-9"])), "task T5 refers to unknown features ['F-9']"),
    (pm_output(_extra_task(fr_ids=["FR-1", "FR-9"])), "task T5 refers to unknown functional requirements ['FR-9']"),
    (pm_output(_extra_task(acceptance_criteria_ids=["FR-1.AC9"])),
     "task T5 refers to unknown acceptance criteria ['FR-1.AC9']"),
    (pm_output(_extra_task(edge_case_ids=["EC-9"])), "task T5 refers to unknown edge cases ['EC-9']"),
    (pm_output(_extra_task(nfr_ids=["NFR-9"])), "task T5 refers to unknown NFRs ['NFR-9']"),
    (pm_output(_extra_task(module_ids=["MOD-9"])), "task T5 refers to unknown modules ['MOD-9']"),
    (pm_output(_extra_task(api_contract_ids=["API-9"])), "task T5 refers to unknown API contracts ['API-9']"),
    (pm_output(_extra_task(data_model_ids=["DM-9"])), "task T5 refers to unknown data models ['DM-9']"),
    (pm_output(_extra_task(constraint_ids=["CON-9"])), "task T5 refers to unknown global constraints ['CON-9']"),
    (pm_output(_extra_task(fr_ids=["FR-1"], acceptance_criteria_ids=["FR-1.AC1", "FR-2.AC1"])),
     "task T5 lists acceptance criteria of ['FR-2'] but not those ids in fr_ids"),
    (pm_output(milestones=[{"id": "M1", "name": "a", "goal": "a", "task_ids": ["T1", "T7"]}]),
     "milestone M1 refers to unknown tasks ['T7']"),
    (pm_output(milestones=[{"id": "M1", "name": "a", "goal": "a", "task_ids": ["T1", "T2"]},
                           {"id": "M2", "name": "b", "goal": "b", "task_ids": ["T2"]}]),
     "task T2 appears in several milestones ['M1', 'M2']"),
    (pm_output(tasks_with(1, acceptance_criteria_ids=["FR-1.AC1", "FR-2.AC1"])),
     "acceptance criterion FR-1.AC2 is not covered by any task"),
    (pm_output(tasks_with(1, module_ids=["MOD-1"])), "module MOD-2 is not covered by any task"),
    (pm_output(tasks_with(3, nfr_ids=[])), "security NFR NFR-2 is not covered by any task"),
    (pm_output(tasks_with(3, feature_ids=[])),
     "security NFR NFR-2 belongs to features ['F-3']: put it on a task that covers one of them"),
    (pm_output(tasks_with(1, edge_case_ids=[])), "edge case EC-1 of high-priority feature F-1 is not covered"),
    (pm_output(_extra_task(fr_ids=[], acceptance_criteria_ids=[])), "feature task T5 needs at least one fr_id"),
])
def test_rule_violations_are_sent_back_then_fixed(tmp_path, bad, problem):
    artifacts = ready(tmp_path)
    runtime = FakeRuntime(bad, pm_output())
    run(project_manager.run(artifacts, runtime, CONFIG))
    violations = runtime.requests[1][1]["artifacts"]["rule_violations"]
    assert any(problem in item for item in violations), violations


def test_one_task_for_a_whole_project_is_too_coarse(tmp_path):
    everything = task("T1", "bootstrap", feature_ids=["F-1", "F-2", "F-3"], fr_ids=["FR-1", "FR-2", "FR-3", "FR-4"],
                      acceptance_criteria_ids=["FR-1.AC1", "FR-1.AC2", "FR-2.AC1", "FR-3.AC1", "FR-4.AC1"],
                      edge_case_ids=["EC-1"], nfr_ids=["NFR-2"], module_ids=["MOD-1", "MOD-2", "MOD-3"])
    runtime = FakeRuntime(pm_output([everything], milestones=[]), pm_output())
    run(project_manager.run(ready(tmp_path), runtime, CONFIG))
    assert runtime.requests[1][1]["artifacts"]["rule_violations"] == [
        "the project has 3+ features and 4+ functional requirements: split it into several tasks"]


def test_brownfield_does_not_require_a_bootstrap_task(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='shop'\n", encoding="utf-8")
    artifacts = ready(tmp_path)
    items = tasks_with(0, kind="integration")
    items[2]["depends_on"] = []
    runtime = FakeRuntime(pm_output(items))
    run(project_manager.run(artifacts, runtime, CONFIG))
    assert len(runtime.requests) == 1 and runtime.requests[0][1]["artifacts"]["workspace_mode"] == "brownfield"
    assert artifacts.read_json("tasks.json")["workspace_mode"] == "brownfield"


# ---------------------------------------------------------------- hydrate bằng code

def test_tasks_json_is_hydrated_by_code(tmp_path):
    artifacts = ready(tmp_path)
    runtime = FakeRuntime(pm_output())
    result = run(project_manager.run(artifacts, runtime, CONFIG))
    role, request = runtime.requests[0]
    assert role == "ProjectManager" and request["output_schema"]["title"] == "ProjectManagerOutput"
    assert set(request["artifacts"]) == {"request", "ba_spec", "solution_architecture", "workspace_mode",
                                         "user_feedback"}
    sa_data = artifacts.read_json("solution_architecture.json")
    data = artifacts.read_json("tasks.json")
    assert data["schema_version"] == "tasks-v2" and data["workspace_mode"] == "greenfield"
    assert data["execution_order"] == ["T1", "T2", "T3", "T4"] == result["execution_order"]
    assert data["global_tech_stack"] == sa_data["tech_stack"]
    assert data["global_runtime_contract"] == sa_data["runtime_contract"]
    assert [t["id"] for t in data["tasks"]] == data["execution_order"]

    t2 = data["tasks"][1]["context_slice"]
    assert t2["features"] == [BA_SPEC["features"][0]]
    assert t2["functional_requirements"] == BA_SPEC["functional_requirements"][:2]  # nguyên văn, kèm đủ AC
    assert t2["business_rules"] == [BA_SPEC["business_rules"][0]]
    assert t2["edge_cases"] == [BA_SPEC["edge_cases"][0]] and t2["non_functional_requirements"] == []
    assert t2["modules"] == [sa_data["modules"][1]] and t2["api_contracts"] == [sa_data["api_contracts"][0]]
    assert t2["data_models"] == []
    assert t2["global_constraints"] == [sa_data["global_constraints"][0]]  # chỉ CON architecture dùng chung
    assert t2["security_architecture"] is None
    assert t2["relevant_assumptions"] == ["giao hàng toàn quốc", "giới hạn 10 món áp dụng mỗi giỏ"]

    assert t2["relevant_open_questions"] == ["có voucher?"]  # OQ-2 toàn cục; OQ-1 chỉ thuộc F-3
    assert t2["external_integrations"] == []  # INT-1 chỉ thuộc F-2
    t3 = data["tasks"][2]["context_slice"]
    assert t3["external_integrations"] == BA_SPEC["external_integrations"]
    assert t3["relevant_assumptions"] == ["giao hàng toàn quốc"] and t3["security_architecture"] is None

    t4 = data["tasks"][3]["context_slice"]
    assert t4["relevant_assumptions"] == ["giao hàng toàn quốc", "admin là nhân viên nội bộ"]
    assert t4["relevant_open_questions"] == ["admin có cần xuất báo cáo?", "có voucher?"]
    assert t4["security_architecture"] == sa_data["security_architecture"]
    assert t4["global_constraints"] == [sa_data["global_constraints"][0], sa_data["global_constraints"][1]]
    assert t4["non_functional_requirements"] == [BA_SPEC["non_functional_requirements"][1]]
    t1 = data["tasks"][0]["context_slice"]
    assert t1["global_constraints"] == [sa_data["global_constraints"][0], sa_data["global_constraints"][2]]


def test_a_task_on_a_security_scoped_feature_gets_the_security_architecture(tmp_path):
    items = pm_tasks()
    items.append(task("T5", deps=["T1"], feature_ids=["F-3"], fr_ids=["FR-4"], acceptance_criteria_ids=["FR-4.AC1"],
                      module_ids=["MOD-3"]))
    artifacts = ready(tmp_path)
    run(project_manager.run(artifacts, FakeRuntime(pm_output(items)), CONFIG))
    t5 = next(item for item in artifacts.read_json("tasks.json")["tasks"] if item["id"] == "T5")
    assert t5["context_slice"]["security_architecture"] == sa_output()["security_architecture"] | {
        "sensitive_data_rules": [], "trust_boundaries": []}


def test_old_ba_spec_without_feature_scopes_still_slices(tmp_path):
    spec = deepcopy(BA_SPEC)
    spec["assumptions"] = [{"text": "giao hàng toàn quốc"}]
    spec["open_questions"] = ["có voucher?"]  # dạng chuỗi của workspace cũ
    for nfr in spec["non_functional_requirements"]:
        nfr.pop("feature_ids")
    spec.pop("external_integrations")
    artifacts = ready(tmp_path, spec, sa_output(api_contracts=[{**c, "integration_ids": []}
                                                               for c in sa_output()["api_contracts"]]))
    run(project_manager.run(artifacts, FakeRuntime(pm_output()), CONFIG))
    t2 = artifacts.read_json("tasks.json")["tasks"][1]["context_slice"]
    assert t2["relevant_open_questions"] == ["có voucher?"] and t2["external_integrations"] == []
    assert t2["relevant_assumptions"][0] == "giao hàng toàn quốc"


def test_execution_order_is_reproducible_when_the_model_shuffles_tasks(tmp_path):
    branching = [task("T1", "bootstrap"), task("T10", deps=["T1"], fr_ids=["FR-4"], acceptance_criteria_ids=["FR-4.AC1"],
                                               nfr_ids=["NFR-2"], feature_ids=["F-3"], module_ids=["MOD-3"]),
                 task("T3", deps=["T1"], fr_ids=["FR-3"], acceptance_criteria_ids=["FR-3.AC1"], module_ids=["MOD-3"]),
                 task("T2", deps=["T1"], fr_ids=["FR-1", "FR-2"], edge_case_ids=["EC-1"], module_ids=["MOD-2"],
                      acceptance_criteria_ids=["FR-1.AC1", "FR-1.AC2", "FR-2.AC1"]),
                 task("T4", "integration", deps=["T3", "T2"])]
    orders = []
    for seed in range(4):
        items = deepcopy(branching)
        random.Random(seed).shuffle(items)
        workspace = tmp_path / f"w{seed}"
        workspace.mkdir()
        artifacts = ready(workspace)
        run(project_manager.run(artifacts, FakeRuntime(pm_output(items, milestones=[])), CONFIG))
        orders.append(artifacts.read_json("tasks.json")["execution_order"])
    assert orders == [["T1", "T2", "T3", "T4", "T10"]] * 4


def test_revise_sends_only_the_model_part_and_reports_changes(tmp_path):
    artifacts = ready(tmp_path)
    run(project_manager.run(artifacts, FakeRuntime(pm_output()), CONFIG))
    revised = FakeRuntime(pm_output(tasks_with(2, title="Đặt hàng COD")))
    result = run(project_manager.run(artifacts, revised, CONFIG, note="đặt tên rõ cho T3"))
    sent = revised.requests[0][1]["artifacts"]
    assert sent["user_feedback"] == ["đặt tên rõ cho T3"]
    assert set(sent["current_draft"]) == {"summary", "milestones", "tasks", "open_questions"}
    assert all("context_slice" not in item for item in sent["current_draft"]["tasks"])
    assert "execution_order" not in sent["current_draft"] and "global_tech_stack" not in sent["current_draft"]
    assert result["changes"] == ["~ task T3 Đặt hàng COD"]


def test_tasks_markdown_has_the_order_table_details_and_coverage_matrix(tmp_path):
    artifacts = ready(tmp_path)
    run(project_manager.run(artifacts, FakeRuntime(pm_output(open_questions=["cần SSO?"])), CONFIG))
    markdown = artifacts.read_text("tasks.md")
    for text in ("**Lệnh test chuẩn:** `python -m pytest -q`", "T1 → T2 → T3 → T4",
                 "| T2 | chức năng | Task T2 | T1 | MOD-2 | 3 |", "### T4 · Task T4 (chức năng)",
                 "- **Ràng buộc:** CON-2", "**Vùng đường dẫn:** `src/shop/cart`", "| FR-1.AC2 | T2 |",
                 "| FR-4.AC1 | T4 |", "- **M1 · MVP**: mua được hàng (T1, T2, T3)", "## Câu hỏi mở", "- cần SSO?"):
        assert text in markdown


# ---------------------------------------------------------------- CLI và TUI

def test_cli_project_manager_revise_approve(tmp_path, capsys):
    artifacts = ready(tmp_path)
    runtime = FakeRuntime(pm_output(), pm_output(summary="bản sửa"))

    async def open_runtime(workspace, request_text, flow, model=None, **kwargs):
        return runtime

    assert cli.main(["project_manager", "--workspace", str(tmp_path), "--observe", "none"],
                    open_runtime=open_runtime) == 0
    assert "project_manager: bản nháp 4 task, 1 milestone (greenfield). Thứ tự: T1 → T2 → T3 → T4" in \
        capsys.readouterr().out
    assert cli.main(["revise", "project_manager", "--workspace", str(tmp_path), "--note", "x"],
                    open_runtime=open_runtime) == 2
    assert cli.main(["revise", "project_manager", "--workspace", str(tmp_path), "--note", "đổi tóm tắt",
                     "--observe", "none"], open_runtime=open_runtime) == 0
    assert cli.main(["approve", "project_manager", "--workspace", str(tmp_path)]) == 0
    assert gate.state(artifacts, "project_manager") == "approved"
    assert artifacts.read_json("tasks.json")["summary"] == "bản sửa"


def test_tui_goes_from_clarify_to_the_task_plan(tmp_path):
    pytest.importorskip("textual")
    from raac.pre_executor.tui import DraftScreen, MessageScreen, PreExecutorApp

    artifacts = Artifacts(tmp_path)
    artifacts.save_request("Làm website bán quần áo")
    runtime = FakeRuntime({"complete": True, "questions": []}, BA_SPEC, sa_output(), pm_output())

    async def open_runtime(workspace, request_text, flow, model=None, **kwargs):
        return runtime

    app = PreExecutorApp(artifacts, open_runtime=open_runtime)

    async def wait_for(pilot, screen_type, text=None):
        for _ in range(80):
            await pilot.pause()
            screen = pilot.app.screen
            if isinstance(screen, screen_type) and (text is None or text in getattr(screen, "markdown", "")
                                                     + getattr(screen, "message", "")):
                return screen
        raise AssertionError(type(pilot.app.screen).__name__)

    async def scenario():
        async with app.run_test(size=(120, 40)) as pilot:
            done = await wait_for(pilot, MessageScreen, "Làm rõ xong")
            assert "Tiếp: Business Analyst" in str(done.buttons)
            await pilot.click("#next")
            for draft_text, done_text, button in (
                    ("FR-1.AC1", ("Đã duyệt đặc tả nghiệp vụ: 3 chức năng, 4 yêu cầu chi tiết, 5 AC, 2 NFR, "
                                  "2 trường hợp biên"), "#next"),
                    ("MOD-3 · orders", "Đã duyệt kiến trúc giải pháp: Python, 3 module, 2 hợp đồng API, 3 ràng buộc",
                     "#next"),
                    ("T1 → T2 → T3 → T4", "Đã duyệt kế hoạch công việc (pm): 4 task, 1 milestone", "#close")):
                await wait_for(pilot, DraftScreen, draft_text)
                await pilot.click("#approve")
                await wait_for(pilot, MessageScreen, done_text)
                await pilot.click(button)
            await pilot.pause()

    asyncio.run(scenario())
    assert all(state == "approved" for state in gate.status(artifacts).values())
    assert app.ba_spec["key_entities"][0]["id"] == "ENT-1"
    assert app.solution_architecture["modules"][2]["name"] == "orders"
    assert app.project_manager["execution_order"] == ["T1", "T2", "T3", "T4"]
    assert app.return_value == app.project_manager
    assert artifacts.read_text("tasks.md").startswith("# Kế hoạch công việc (Project Manager)")


# ---------------------------------------------------------------- hạ tầng MAS thật

def test_runs_through_the_real_agent_runner_with_a_viewable_log(tmp_path):
    from raac.llm.config import LLMConfig
    from raac.pre_executor.runtime import open_runtime

    registry = tmp_path / "registry"
    shutil.copytree(ROOT / "registry", registry)
    seen = []

    def respond(http_request):
        seen.append(json.loads(http_request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(pm_output(), ensure_ascii=False)}}],
                                         "usage": {"prompt_tokens": 90, "completion_tokens": 60}})

    repo = tmp_path / "shop"
    repo.mkdir()
    artifacts = ready(repo)

    async def scenario():
        runtime = await open_runtime(repo, artifacts.request(), "project_manager", stream=False,
                                     registry_dir=registry, results_dir=tmp_path / "results",
                                     llm_config=LLMConfig(model="fixture"), transport=httpx.MockTransport(respond))
        try:
            result = await project_manager.run(artifacts, runtime, CONFIG)
        finally:
            await runtime.aclose()
        return runtime, result

    runtime, result = run(scenario())
    assert result["execution_order"] == ["T1", "T2", "T3", "T4"]
    assert artifacts.read_json("tasks.json")["tasks"][0]["context_slice"]["modules"][0]["id"] == "MOD-1"
    system = seen[0]["messages"][0]["content"]
    assert "Project Manager" in system and "suggested_level" in system
    assert "solution_architecture" in json.dumps(seen[0], ensure_ascii=False)
    events = [json.loads(line)["event"] for line in runtime.log_path.read_text(encoding="utf-8").splitlines()]
    assert events[:2] == ["run_started", "flow_started"] and "llm_request" in events and events[-1] == "flow_completed"
