"""Requirement Analyzer (raac.ra): gom ngữ cảnh, luật, ghi requirement_profile.json, CLI, chạy qua AgentRunner thật."""

import asyncio
import json
import shutil
from pathlib import Path

import httpx
import pytest

from raac.pre_executor.artifacts import Artifacts
from raac.pre_executor.flows.base import FlowError
from raac.ra import analyze, cli
from raac.ra.context import ContextError, architecture_outline, rubric_text, select_task, spec_context
from raac.ra.contracts import RUBRIC_FILE

ROOT = Path(__file__).resolve().parents[2]
SAMPLE = ROOT / "data/ra/sample"
CONFIG = cli.RaConfig(max_flow_retries=1)


def workspace(tmp_path) -> Path:
    folder = tmp_path / "ws" / ".raac" / "pre_executor"
    folder.mkdir(parents=True)
    for name in ("tasks.json", "requirement_spec.json", "architecture.md"):
        shutil.copy(SAMPLE / name, folder / name)
    return tmp_path / "ws"


def axis(score, *factors, evidence="bằng chứng cụ thể"):
    return {"score": score, "factors": list(factors), "evidence": evidence, "confidence": 0.8}


def profile(task_id="T3", sections=("accounts",), **changes):
    data = {"task_id": task_id, "complexity": axis(3, "business_logic", "dependencies"),
            "testability": axis(2, "acceptance_criteria"), "security": axis(4, "authentication", "data_sensitivity"),
            "architecture_impact": axis(2, "system_structure"), "architecture_sections_read": list(sections),
            "summary": "Đổi email chạm tới định danh tài khoản."}
    data.update(changes)
    return data


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


def analyze_task(ws, runtime, task_id="T3"):
    return run(analyze.run(Artifacts(ws), cli.RaArtifacts(ws), runtime, CONFIG, task_id, rubric_path=ROOT / RUBRIC_FILE))


# ---------------------------------------------------------------- gom ngữ cảnh bằng code

def test_select_task_and_related_spec_only():
    tasks = json.loads((SAMPLE / "tasks.json").read_text(encoding="utf-8"))
    spec = json.loads((SAMPLE / "requirement_spec.json").read_text(encoding="utf-8"))
    task, dependencies = select_task(tasks, "T3")
    assert task.title == "Đổi email có xác nhận" and dependencies == [{"id": "T1", "title": "Dựng khung dự án"}]
    context = spec_context(spec, task)
    assert [fr["id"] for fr in context["functional_requirements"]] == ["FR-1"]
    assert [f["id"] for f in context["features"]] == ["F-1"]
    assert [r["id"] for r in context["business_rules"]] == ["BR-1"] and [c["id"] for c in context["edge_cases"]] == ["EC-1"]
    assert [u["id"] for u in context["user_flows"]] == ["UF-1"]
    assert len(context["non_functional_requirements"]) == 2 and context["constraints"]  # NFR, ràng buộc đi kèm


def test_clear_errors_for_unknown_task_unknown_acceptance_criterion_and_bad_task():
    spec = json.loads((SAMPLE / "requirement_spec.json").read_text(encoding="utf-8"))
    with pytest.raises(ContextError, match="Không có task T9"):
        select_task({"tasks": [{"id": "T1", "title": "a", "description": "b"}]}, "T9")
    task, _ = select_task({"tasks": [{"id": "T1", "title": "a", "description": "b",
                                      "acceptance_criteria": ["FR-1.AC1", "FR-9.AC1"]}]}, "T1")
    with pytest.raises(ContextError, match="FR-9.AC1"):
        spec_context(spec, task)
    with pytest.raises(ContextError, match="thiếu hoặc sai trường"):
        select_task({"tasks": [{"id": "T1"}]}, "T1")


def test_architecture_outline_has_line_ranges_and_skips_code_blocks():
    outline = architecture_outline("# A\nx\n## B\n```\n# not a heading\n```\n### C\ny\n")
    assert [(i["title"], i["line"], i["end_line"]) for i in outline] == [("A", 1, 2), ("B", 3, 6), ("C", 7, 8)]


def test_rubric_text_keeps_the_scoring_sections_only():
    text = rubric_text(ROOT / RUBRIC_FILE)
    assert text.startswith("# 2.") and "# 6. Architecture Impact" in text and "# 7. Ví dụ" in text
    assert "Tài liệu tham khảo" not in text and "# 8." not in text


# ---------------------------------------------------------------- flow analyze

def test_payload_has_outline_not_the_whole_architecture_and_profile_is_written(tmp_path):
    ws = workspace(tmp_path)
    runtime = FakeRuntime(profile())
    result = analyze_task(ws, runtime)
    role, request = runtime.requests[0]
    sent = request["artifacts"]
    assert role == "RequirementAnalyzer" and request["output_schema"]["title"] == "RequirementProfileOutput"
    assert sent["architecture"]["path"] == ".raac/pre_executor/architecture.md"
    assert "accounts" in [item["title"] for item in sent["architecture"]["outline"]]
    assert "HMAC-SHA512" not in json.dumps(sent, ensure_ascii=False)  # nội dung kiến trúc không gửi kèm
    assert sent["rubric"].startswith("# 2.") and sent["axis_factors"]["security"][0] == "data_sensitivity"
    assert result["profile"] == [3, 2, 4, 2]
    stored = cli.RaArtifacts(ws).read_json("requirement_profile.json")["profiles"]["T3"]
    assert stored["profile"] == [3, 2, 4, 2] and stored["architecture_sections_read"] == ["accounts"]
    assert stored["rubric_version"] == "RAAC-4Axis-v1.0" and stored["human"] is None and set(stored["inputs"]) == {
        "task", "spec_context", "architecture"}
    assert "| T3 | 3 | 2 | 4 | 2 |" in cli.RaArtifacts(ws).read_text("requirement_profile.md")


def test_each_task_gets_its_own_entry(tmp_path):
    ws = workspace(tmp_path)
    analyze_task(ws, FakeRuntime(profile()))
    analyze_task(ws, FakeRuntime(profile("T2", ("cart",), security=axis(1, "data_sensitivity"))), "T2")
    profiles = cli.RaArtifacts(ws).read_json("requirement_profile.json")["profiles"]
    assert sorted(profiles) == ["T2", "T3"] and profiles["T2"]["profile"][2] == 1


@pytest.mark.parametrize("bad, problem", [
    (profile(security=axis(4, "business_logic")), "security.factors ['business_logic'] are not security factors"),
    (profile(sections=()), "read the relevant sections of architecture.md"),
    (profile(sections=("Bảo mật hệ thống",)), "are not headings of architecture.md"),
    (profile(task_id="T2"), "task_id must be T3"),
])
def test_rule_violations_are_sent_back_then_fixed(tmp_path, bad, problem):
    runtime = FakeRuntime(bad, profile())
    analyze_task(workspace(tmp_path), runtime)
    assert any(problem in item for item in runtime.requests[1][1]["artifacts"]["rule_violations"])


def test_schema_rejects_out_of_range_scores(tmp_path):
    with pytest.raises(FlowError):
        analyze_task(workspace(tmp_path), FakeRuntime(profile(security=axis(6, "authentication"))))


def test_soft_checks_flag_suspicious_scores_without_blocking(tmp_path):
    ws = workspace(tmp_path)
    result = analyze_task(ws, FakeRuntime(profile("T4", ("payments",), security=axis(1, "data_sensitivity"),
                                                  architecture_impact=axis(1, "system_structure"))), "T4")
    assert any("security 1 nhưng có từ khóa bảo mật" in note for note in result["checks"])
    assert any("architecture_impact 1 nhưng có dấu hiệu đổi kiến trúc" in note for note in result["checks"])


def test_changed_inputs_make_the_profile_stale(tmp_path):
    ws = workspace(tmp_path)
    analyze_task(ws, FakeRuntime(profile()))
    entry = cli.RaArtifacts(ws).read_json("requirement_profile.json")["profiles"]["T3"]
    sources = Artifacts(ws)
    assert not analyze.stale(sources, entry, "T3")
    path = sources.path("architecture.md")
    path.write_text(path.read_text(encoding="utf-8") + "\n## Mới\nthêm cache\n", encoding="utf-8")
    assert analyze.stale(sources, entry, "T3")


def test_missing_inputs_are_reported(tmp_path):
    ws = workspace(tmp_path)
    (ws / ".raac/pre_executor/tasks.json").unlink()
    with pytest.raises(ValueError, match="Chưa có tasks.json"):
        analyze_task(ws, FakeRuntime(profile()))


# ---------------------------------------------------------------- CLI

def test_cli_analyze_and_show(tmp_path, capsys):
    ws = workspace(tmp_path)
    runtime = FakeRuntime(profile())

    async def open_runtime(workspace, request_text, flow, model=None, **kwargs):
        assert flow == "analyze"
        return runtime

    assert cli.main(["analyze", "--workspace", str(ws), "--task", "T3", "--observe", "none"],
                    open_runtime=open_runtime) == 0
    assert "T3: (C=3, T=2, S=4, A=2)" in capsys.readouterr().out
    assert cli.main(["show", "--workspace", str(ws)]) == 0
    assert "T3: C=3 T=2 S=4 A=2" in capsys.readouterr().out
    assert cli.main(["analyze", "--workspace", str(ws), "--task", "T9", "--observe", "none"],
                    open_runtime=open_runtime) == 2
    assert "Không có task T9" in capsys.readouterr().err


# ---------------------------------------------------------------- hạ tầng MAS thật: agent tự đọc đúng mục kiến trúc

def test_the_agent_reads_only_the_relevant_architecture_lines_with_harness_tools(tmp_path):
    from raac.llm.config import LLMConfig
    from raac.pre_executor.runtime import open_runtime

    ws = workspace(tmp_path)
    outline = architecture_outline((ws / ".raac/pre_executor/architecture.md").read_text(encoding="utf-8"))
    accounts = next(item for item in outline if item["title"] == "accounts")
    seen = []
    replies = [
        {"tool_calls": [{"id": "call-1", "type": "function", "function": {
            "name": "read_file", "arguments": json.dumps({"path": ".raac/pre_executor/architecture.md",
                                                          "start_line": accounts["line"],
                                                          "end_line": accounts["end_line"]})}}]},
        {"content": json.dumps(profile(), ensure_ascii=False)},
    ]

    def respond(http_request):
        seen.append(json.loads(http_request.content))
        return httpx.Response(200, json={"choices": [{"message": replies.pop(0)}],
                                         "usage": {"prompt_tokens": 100, "completion_tokens": 20}})

    async def scenario():
        from raac.ra import contracts

        contracts.register()
        runtime = await open_runtime(ws, "Đổi email", "analyze", stream=False, results_dir=tmp_path / "results",
                                     llm_config=LLMConfig(model="fixture"), transport=httpx.MockTransport(respond))
        try:
            return await analyze.run(Artifacts(ws), cli.RaArtifacts(ws), runtime, CONFIG, "T3",
                                     rubric_path=ROOT / RUBRIC_FILE)
        finally:
            await runtime.aclose()

    result = run(scenario())
    first = json.dumps(seen[0], ensure_ascii=False)
    tool_result = next(m for m in seen[1]["messages"] if m["role"] == "tool")["content"]
    assert "email_change_requests" not in first  # request đầu không chứa nội dung kiến trúc
    assert "email_change_requests" in tool_result and "HMAC-SHA512" not in tool_result  # chỉ đọc đúng mục accounts
    assert "Requirement Analyzer" in seen[0]["messages"][0]["content"] and result["profile"] == [3, 2, 4, 2]
