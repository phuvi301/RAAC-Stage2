import asyncio
import json

from swflow import cli, machine, phases
from swflow.store import Store
from test_phases import ARCH, SPEC, TASKS, FakeModel, make_store

INVENTORY = {
    "os": "Windows 11", "shell": "PowerShell",
    "python": [{"version": "3.9", "path": r"C:\gone\python.exe", "exists": False},
               {"version": "3.14", "path": r"C:\Py\python.exe", "exists": True}],
    "tools": {"node": {"version": "24.21.0", "path": r"C:\node\node.exe"},
              "npm": {"version": "12.2.0", "path": r"C:\node\npm.cmd"}},
    "installers": ["winget", "py"],
}
USES_INSTALLED = {
    "runtimes": [{"name": "Node.js", "version": "24", "source": "installed", "path": r"C:\node\node.exe",
                  "reason": "Next.js 15 hỗ trợ Node 24"},
                 {"name": "npm", "version": "12.2.0", "source": "installed", "reason": "đi kèm Node"}],
    "package_manager": "npm", "dependency_install": "npm install", "test_command": "npx vitest run",
    "run_command": "npm run dev", "check_commands": ["node --version", "npm --version"],
    "notes": ["Kiến trúc ghi Node 20 LTS; dùng Node 24 đã có trên máy"]}


def prepared(tmp_path):
    store = make_store(tmp_path)
    store.save_spec(phases.Spec.model_validate(SPEC))
    store.save_architecture(phases.Architecture.model_validate(ARCH))
    return store


def test_installed_versions_must_really_exist_and_broken_registrations_do_not_count():
    plan = phases.EnvironmentPlan.model_validate(USES_INSTALLED)
    assert phases.environment_issues(plan, INVENTORY) == []
    wrong = json.loads(json.dumps(USES_INSTALLED))
    wrong["runtimes"][0]["version"] = "20"
    wrong["runtimes"].append({"name": "python", "version": "3.9", "source": "installed", "reason": "r"})
    wrong["runtimes"].append({"name": "postgresql", "version": "16", "source": "install", "reason": "r"})
    issues = phases.environment_issues(phases.EnvironmentPlan.model_validate(wrong), INVENTORY)
    assert "Node.js 20 is marked installed but the machine has 24.21.0" in issues
    assert "python 3.9 is marked installed but the machine has 3.14" in issues  # the 3.9 entry points nowhere
    assert "postgresql 16 must be installed but has no install_command" in issues


def test_environment_phase_records_the_machine_and_retries_until_it_matches(tmp_path):
    store = prepared(tmp_path)
    wants_node_20 = json.loads(json.dumps(USES_INSTALLED))
    wants_node_20["runtimes"][0]["version"] = "20"
    model = FakeModel({"environment": [wants_node_20, USES_INSTALLED]})
    plan = asyncio.run(phases.environment(store, model, inventory=INVENTORY))
    assert plan.runtimes[0].version == "24"
    assert '"node"' in model.calls[0][2]  # the inventory was sent to the model
    assert "machine has 24.21.0" in model.calls[1][2]
    assert json.loads(store.read_text("machine.json"))["tools"]["node"]["version"] == "24.21.0"
    text = store.read_text("environment.md")
    assert "đã có trên máy" in text and "`npx vitest run`" in text


def test_setup_lists_install_commands_and_installs_nothing_without_apply(tmp_path, capsys):
    store = prepared(tmp_path)
    needs = json.loads(json.dumps(USES_INSTALLED))
    needs["runtimes"].append({"name": "postgresql", "version": "16", "source": "install",
                              "install_command": "winget install PostgreSQL.PostgreSQL.16", "reason": "lưu đơn hàng"})
    store.save_environment(phases.EnvironmentPlan.model_validate(needs))
    assert cli.main(["--runs-dir", str(tmp_path), "setup", "todo"]) == 0
    out = capsys.readouterr().out
    assert "winget install PostgreSQL.PostgreSQL.16" in out and "Chưa cài gì" in out


def test_plan_waits_for_the_environment_and_export_hands_it_to_the_mas(tmp_path, monkeypatch, capsys):
    store = prepared(tmp_path)
    model = FakeModel({"environment": [USES_INSTALLED], "plan": [TASKS]})
    monkeypatch.setattr("swflow.llm_raac.RaacModel", lambda **kwargs: model)
    monkeypatch.setattr(machine, "probe", lambda: INVENTORY)
    run = lambda *args: cli.main(["--runs-dir", str(tmp_path), *args])  # noqa: E731
    assert run("approve", "todo", "spec") == 0 and run("approve", "todo", "architecture") == 0
    assert run("env", "todo") == 0
    assert run("plan", "todo") == 2  # environment not approved yet
    assert run("approve", "todo", "environment") == 0
    assert run("plan", "todo") == 0
    assert run("export", "todo", "--mas-project", str(tmp_path / "shop")) == 0
    t1 = (Store(tmp_path / "todo").path("mas") / "T1.user_chat.txt").read_text(encoding="utf-8")
    assert "## Môi trường đã chốt" in t1
    assert r"- Node.js 24 tại `C:\node\node.exe`" in t1
    assert "Lệnh test (chạy ở thư mục gốc project): `npx vitest run`" in t1
    assert "không dựng thử project ngoài repo" in t1


def test_probe_reads_versions_and_flags_missing_python_paths(tmp_path):
    real = tmp_path / "python.exe"
    real.write_text("")
    listing = f" -V:3.14[-64] *   {real}\n -V:3.9           {tmp_path / 'gone' / 'python.exe'}\n"
    outputs = {"py": listing, "node": "v24.21.0\n", "git": "git version 2.56.0.windows.2\n"}
    found = machine.probe(run=lambda command, timeout=15.0: outputs.get(command[0]))
    assert [(p["version"], p["exists"]) for p in found["python"]][:2] == [("3.14", True), ("3.9", False)]
    assert found["tools"]["node"]["version"] == "24.21.0" and found["tools"]["git"]["version"] == "2.56.0"
