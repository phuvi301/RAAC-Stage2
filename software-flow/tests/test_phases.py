import asyncio
import json

import pytest

from swflow import cli, phases
from swflow.store import Store

CLARIFY = {"understanding": "Ứng dụng quản lý công việc.", "questions": [
    {"id": "Q1", "question": "Có nhiều người dùng không?", "why": "quyết định đăng nhập",
     "default_answer": "Một người dùng", "blocking": False},
    {"id": "Q2", "question": "Lưu dữ liệu ở đâu?", "why": "chọn kho dữ liệu",
     "default_answer": "File cục bộ", "blocking": False}]}
SPEC = {"summary": "Quản lý công việc", "requirements": [
    {"id": "FR-1", "kind": "functional", "title": "Thêm việc", "statement": "Người dùng thêm việc",
     "priority": "must", "acceptance_criteria": ["Thêm 'mua sữa' thì danh sách có 'mua sữa'"]},
    {"id": "NFR-1", "kind": "non_functional", "title": "Nhanh", "statement": "Phản hồi nhanh",
     "priority": "should", "acceptance_criteria": ["Thêm việc dưới 200 ms"],
     "metric": "thời gian thêm 1 việc", "target": "< 200 ms"}]}
ARCH = {"context": "new_project", "overview": "CLI Python", "decisions": [
    {"id": "ADR-1", "topic": "ngôn ngữ", "choice": "Python 3.12", "rationale": "đơn giản",
     "requirement_ids": ["FR-1"]}]}
TASKS = {"tasks": [
    {"id": "T1", "title": "Khung dự án", "description": "tạo package", "acceptance_criteria": ["NFR-1.AC1"],
     "priority": 1, "size": "S"},
    {"id": "T2", "title": "Thêm việc", "description": "lệnh add", "depends_on": ["T1"],
     "acceptance_criteria": ["FR-1.AC1"], "requirement_ids": ["FR-1"], "priority": 1, "size": "M"}]}


class FakeModel:
    """Replays replies per phase and records what the flow sent."""

    def __init__(self, replies):
        self.replies = {phase: list(items) for phase, items in replies.items()}
        self.calls = []

    async def complete_json(self, *, phase, system, user):
        self.calls.append((phase, system, user))
        reply = self.replies[phase].pop(0)
        return reply if isinstance(reply, str) else json.dumps(reply, ensure_ascii=False)

    def usage(self):
        return {"prompt_tokens": 0, "completion_tokens": 0, "calls": len(self.calls)}

    async def aclose(self):
        pass


def make_store(tmp_path):
    store = Store(tmp_path / "todo")
    store.write_text("request.md", "Xây ứng dụng quản lý công việc\n")
    return store


def test_answers_keep_defaults_as_assumptions_and_user_edits_as_decisions(tmp_path):
    store = make_store(tmp_path)
    asyncio.run(phases.clarify(store, FakeModel({"clarify": [CLARIFY]})))
    text = store.read_text("answers.md").replace("A: File cục bộ", "A: SQLite")
    store.write_text("answers.md", text)
    answers = {a.question_id: (a.answer, a.source) for a in store.answers()}
    assert answers == {"Q1": ("Một người dùng", "default"), "Q2": ("SQLite", "user")}


def test_follow_up_round_keeps_earlier_questions_and_the_users_answers(tmp_path):
    store = make_store(tmp_path)
    follow_up = {"understanding": "u", "questions": [
        {"id": "Q1", "question": "Cần xuất CSV?", "why": "phạm vi", "default_answer": "Không", "blocking": False}]}
    model = FakeModel({"clarify": [CLARIFY, follow_up]})
    asyncio.run(phases.clarify(store, model))
    store.write_text("answers.md", store.read_text("answers.md").replace("A: File cục bộ", "A: SQLite"))
    asyncio.run(phases.clarify(store, model))
    answers = {a.question_id: (a.answer, a.source) for a in store.answers()}
    assert answers == {"Q1": ("Một người dùng", "default"), "Q2": ("SQLite", "user"), "Q3": ("Không", "default")}
    assert "SQLite" in model.calls[1][2]  # the second round saw the earlier answer


def test_invalid_replies_are_retried_with_the_problems_listed(tmp_path):
    store = make_store(tmp_path)
    vague = json.loads(json.dumps(SPEC))
    vague["requirements"][1].pop("metric")  # a non-functional requirement without a metric
    model = FakeModel({"specify": ["not json", vague, "```json\n" + json.dumps(SPEC) + "\n```"]})
    spec = asyncio.run(phases.specify(store, model))
    assert [r.id for r in spec.requirements] == ["FR-1", "NFR-1"]
    assert "no JSON object" in model.calls[1][2]
    assert "NFR-1 is non-functional and needs both metric and target" in model.calls[2][2]
    assert "`FR-1.AC1`" in store.read_text("spec.md")


def test_plan_rejects_uncovered_criteria_until_fixed_and_gives_up_after_three_attempts(tmp_path):
    store = make_store(tmp_path)
    store.save_spec(phases.Spec.model_validate(SPEC))
    store.save_architecture(phases.Architecture.model_validate(ARCH))
    partial = {"tasks": [TASKS["tasks"][0]]}
    model = FakeModel({"plan": [partial, TASKS]})
    graph = asyncio.run(phases.plan(store, model))
    assert [t.id for t in graph.tasks] == ["T1", "T2"]
    assert "not covered by any task: FR-1.AC1" in model.calls[1][2]
    with pytest.raises(phases.PhaseError):
        asyncio.run(phases.plan(store, FakeModel({"plan": [partial] * 3})))


def test_cli_walks_the_phases_with_approval_stops(tmp_path, monkeypatch, capsys):
    model = FakeModel({"clarify": [CLARIFY], "specify": [SPEC], "architect": [ARCH], "plan": [TASKS]})
    monkeypatch.setattr("swflow.llm_raac.RaacModel", lambda **kwargs: model)
    run = lambda *args: cli.main(["--runs-dir", str(tmp_path), *args])  # noqa: E731
    assert run("new", "todo", "--request", "Xây ứng dụng quản lý công việc") == 0
    assert run("clarify", "todo") == 0
    assert run("spec", "todo") == 0
    assert run("architect", "todo") == 2  # spec not approved yet
    assert run("approve", "todo", "spec") == 0
    assert run("architect", "todo") == 0
    assert run("plan", "todo", "--force") == 0
    assert run("status", "todo") == 0
    out = capsys.readouterr().out
    assert "Cần duyệt spec trước" in out
    assert "2 task, thứ tự: T1 → T2" in out
    assert "Task sẵn sàng: T1" in out


def test_mas_project_folder_supplies_request_and_nested_repo(tmp_path, monkeypatch):
    project = tmp_path / "currentProject"
    (project / "requirement").mkdir(parents=True)
    (project / "requirement" / "user_chat.txt").write_text("Sửa lỗi X", encoding="utf-8")
    (project / "repo" / "django").mkdir(parents=True)
    (project / "repo" / "django" / "setup.py").write_text("", encoding="utf-8")
    runs = tmp_path / "runs"
    assert cli.main(["--runs-dir", str(runs), "new", "fix", "--from-project", str(project)]) == 0
    store = Store(runs / "fix")
    assert store.request() == "Sửa lỗi X\n"
    assert store.state().repo.endswith("django")
    overview = phases.repo_overview(project / "repo" / "django")
    assert overview["manifests"] == ["setup.py"]


def test_export_writes_one_request_per_task_and_an_ordered_run_script(tmp_path):
    store = make_store(tmp_path)
    store.save_spec(phases.Spec.model_validate(SPEC))
    store.save_architecture(phases.Architecture.model_validate(ARCH))
    store.save_tasks(phases.TaskGraph.model_validate(TASKS))
    mas = tmp_path / "shopProject"
    assert cli.main(["--runs-dir", str(tmp_path), "export", "todo", "--mas-project", str(mas),
                     "--repo-name", "shop"]) == 0
    t2 = (store.path("mas") / "T2.user_chat.txt").read_text(encoding="utf-8")
    assert "# T2: Thêm việc" in t2
    assert "- ngôn ngữ: Python 3.12" in t2  # technology decisions travel with every task
    assert "`FR-1.AC1` Thêm 'mua sữa' thì danh sách có 'mua sữa'" in t2  # full AC text, not only the ID
    assert "- T1: Khung dự án" in t2  # finished dependency is described
    t1 = (store.path("mas") / "T1.user_chat.txt").read_text(encoding="utf-8")
    assert "Không làm trong task này" in t1 and "- T2: Thêm việc" in t1
    assert "NFR-1" in t1 and "< 200 ms" in t1
    script = store.path("mas").joinpath("run_tasks.ps1").read_text(encoding="utf-8-sig")
    assert script.index("Id='T1'") < script.index("Id='T2'")
    assert "--level L3 --project-dir $Mas --observe none" in script
    assert r"'repo\shop'" in script


def test_export_refuses_an_invalid_plan(tmp_path):
    store = make_store(tmp_path)
    store.save_spec(phases.Spec.model_validate(SPEC))
    store.save_architecture(phases.Architecture.model_validate(ARCH))
    store.save_tasks(phases.TaskGraph.model_validate({"tasks": [TASKS["tasks"][0]]}))  # FR-1.AC1 uncovered
    assert cli.main(["--runs-dir", str(tmp_path), "export", "todo", "--mas-project", str(tmp_path / "m")]) == 2
