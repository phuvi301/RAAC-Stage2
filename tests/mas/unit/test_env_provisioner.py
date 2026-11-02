"""Deterministic project detection, interpreter choice and gap reporting (no network, no LLM)."""

import asyncio
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from raac.harness.layer3_execution import env_provisioner as provisioner
from raac.harness.layer3_execution.local_executor import LocalExecutionPolicy, LocalExecutor


def write(root, name, text=""):
    path = Path(root) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def candidate(version, source="path"):
    return {"path": f"/py/{version}", "version": version, "source": source}


class DetectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)

    def test_markers_select_ecosystems_and_skip_dependency_directories(self):
        write(self.root, "pyproject.toml", "[project]\nname='a'\n")
        write(self.root, "web/package.json", "{}")
        write(self.root, "svc/pom.xml", "<project/>")
        write(self.root, "go/go.mod", "module x\ngo 1.21\n")
        write(self.root, "node_modules/dep/package.json", "{}")
        write(self.root, ".venv/pyproject.toml", "")
        found = {(p["ecosystem"], p["directory"]) for p in provisioner.detect_projects(self.root)}
        self.assertEqual(found, {("python", "."), ("node", "web"), ("maven", "svc"), ("go", "go")})

    def test_requirements_txt_alone_marks_python(self):
        write(self.root, "requirements-dev.txt", "pytest\n")
        self.assertEqual([p["ecosystem"] for p in provisioner.detect_projects(self.root)], ["python"])

    def test_python_requirements_from_setup_cfg_tox_and_requirements(self):
        write(self.root, "setup.cfg", "[metadata]\nname = legacy\n[options]\npython_requires = >=3.6\n"
              "install_requires =\n    numpy>=1.12\n    pandas>=0.19\n[tool:pytest]\n"
              "[options.extras_require]\ntests =\n    pytest\n")
        write(self.root, "tox.ini", "[tox]\nenvlist = py37,py38\n")
        write(self.root, "requirements.txt", "# comment\nrequests==2.0\n-r other.txt\n")
        info = provisioner.python_requirements(self.root)
        self.assertEqual(info["name"], "legacy")
        self.assertEqual(info["requires_python"], ">=3.6")
        self.assertEqual(info["supported_minors"], [7, 8])
        self.assertEqual(set(info["dependencies"]), {"numpy>=1.12", "pandas>=0.19", "pytest", "requests==2.0"})
        self.assertTrue(info["uses_pytest"] and info["installable"])

    def test_pyproject_dependencies_and_test_extras(self):
        write(self.root, "pyproject.toml", '[project]\nname="x"\nrequires-python=">=3.9"\n'
              'dependencies=["httpx>=0.27"]\n[project.optional-dependencies]\ntest=["pytest"]\ndocs=["sphinx"]\n')
        info = provisioner.python_requirements(self.root)
        self.assertEqual(set(info["dependencies"]), {"httpx>=0.27", "pytest"})


class InterpreterChoiceTests(unittest.TestCase):
    REQUIRES = {"requires_python": ">=3.6", "supported_minors": [6, 7], "pinned": None}

    def test_window_allows_two_newer_minors_but_not_a_much_newer_runtime(self):
        self.assertEqual(provisioner.choose_python(self.REQUIRES, [candidate("3.14.8")]), None)
        chosen = provisioner.choose_python(self.REQUIRES, [candidate("3.14.8"), candidate("3.8.10"), candidate("3.9.13")])
        self.assertEqual(chosen["version"], "3.9.13")

    def test_existing_project_venv_wins_ties(self):
        chosen = provisioner.choose_python(self.REQUIRES, [candidate("3.9.13"), candidate("3.9.1", "project-venv")])
        self.assertEqual(chosen["source"], "project-venv")

    def test_python_version_pin_overrides_classifiers(self):
        requires = {**self.REQUIRES, "pinned": "3.7.9"}
        self.assertEqual(provisioner.choose_python(requires, [candidate("3.8.10"), candidate("3.7.4")])["version"], "3.7.4")

    def test_no_declaration_accepts_any_python3_that_satisfies_requires(self):
        requires = {"requires_python": ">=3.11", "supported_minors": [], "pinned": None}
        self.assertEqual(provisioner.choose_python(requires, [candidate("3.10.1"), candidate("3.14.0")])["version"], "3.14.0")

    def test_wanted_minor_is_fetchable_and_inside_window(self):
        self.assertEqual(provisioner.wanted_minors(self.REQUIRES), ["3.8"])
        modern = {"requires_python": ">=3.10", "supported_minors": [10, 11], "pinned": None}
        self.assertEqual(provisioner.wanted_minors(modern), ["3.10"])


class InspectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)

    def test_missing_runtime_is_blocking_and_offers_a_user_local_fix(self):
        write(self.root, "setup.cfg", "[options]\npython_requires = >=3.6\n[metadata]\nname=legacy\n")
        write(self.root, "setup.py", "from setuptools import setup\nsetup(classifiers=['Programming Language :: Python :: 3.7'])\n")
        fix = {"argv": ["py", "install", "--yes", "3.8"], "purpose": "x"}
        with patch.object(provisioner, "interpreter_candidates", return_value=[candidate("3.14.8")]), \
                patch.object(provisioner, "python_installer", return_value=fix):
            report = provisioner.inspect_project(self.root)
        project = report["projects"][0]
        self.assertFalse(report["ready"])
        self.assertIn("No installed interpreter", project["blocking"][0])
        self.assertEqual(project["toolchain_fix"], fix)
        self.assertIsNone(project["selected_python"])

    def test_no_fix_available_reports_exact_hint(self):
        write(self.root, "setup.cfg", "[options]\npython_requires = >=3.6\n[metadata]\nname=legacy\n")
        write(self.root, "tox.ini", "[tox]\nenvlist = py37\n")
        with patch.object(provisioner, "interpreter_candidates", return_value=[candidate("3.14.8")]), \
                patch.object(provisioner, "python_installer", return_value=None):
            project = provisioner.inspect_project(self.root)["projects"][0]
        self.assertTrue(any("Install Python 3.8" in item for item in project["blocking"]))

    def test_plan_creates_venv_installs_missing_and_the_project(self):
        write(self.root, "pyproject.toml", '[project]\nname="demo-pkg"\ndependencies=["packaging>=20"]\n'
              "[tool.pytest.ini_options]\n")
        write(self.root, "demo/__init__.py", "")
        python = Path(sys.executable)
        with patch.object(provisioner, "interpreter_candidates",
                          return_value=[{"path": str(python), "version": "3.14.0", "source": "mas"}]):
            project = provisioner.inspect_project(self.root)["projects"][0]
        argv = [step["argv"] for step in project["plan"]]
        self.assertEqual(argv[0][1:], ["-m", "venv", ".venv"])
        self.assertIn("pytest", argv[1])
        self.assertIn("packaging>=20", argv[1])
        self.assertEqual(argv[2][-3:], ["install", "-e", "."])
        self.assertEqual(project["test_command"][-2:], ["-m", "pytest"])

    def test_satisfied_environment_needs_nothing(self):
        write(self.root, "requirements.txt", "packaging>=1\n")
        python = Path(sys.executable)
        with patch.object(provisioner, "interpreter_candidates",
                          return_value=[{"path": str(python), "version": "3.14.0", "source": "project-venv"}]):
            report = provisioner.inspect_project(self.root)
        project = report["projects"][0]
        self.assertEqual(project["dependencies"]["missing"], [])
        self.assertEqual(project["plan"], [])
        self.assertTrue(report["ready"])

    def test_version_mismatch_is_reported_for_reinstall(self):
        write(self.root, "requirements.txt", "packaging<1\n")
        python = Path(sys.executable)
        with patch.object(provisioner, "interpreter_candidates",
                          return_value=[{"path": str(python), "version": "3.14.0", "source": "project-venv"}]):
            project = provisioner.inspect_project(self.root)["projects"][0]
        self.assertEqual(project["dependencies"]["mismatched"][0]["requirement"], "packaging<1")
        self.assertIn("packaging<1", project["plan"][0]["argv"])

    def test_node_and_go_report_missing_toolchain_and_dependencies(self):
        write(self.root, "web/package.json", '{"scripts": {"test": "jest"}, "engines": {"node": ">=18"}}')
        write(self.root, "web/package-lock.json", "{}")
        with patch.object(provisioner, "_version_of", return_value="20.1.0"), \
                patch.object(provisioner.shutil, "which", side_effect=lambda name, *a, **k: name):
            node = provisioner.inspect_node(self.root, "web")
        self.assertEqual(node["plan"][0]["argv"], ["npm", "ci"])
        self.assertEqual(node["dependencies"]["missing"], ["node_modules"])
        with patch.object(provisioner, "_version_of", return_value=None):
            self.assertIn("Node.js is not installed", provisioner.inspect_node(self.root, "web")["blocking"][0])
            write(self.root, "go/go.mod", "module x\ngo 1.21\n")
            self.assertIn("go is not installed", provisioner.inspect_simple(self.root, "go", "go")["blocking"][0])

    def test_java_older_than_project_level_blocks(self):
        write(self.root, "pom.xml", "<project><properties><maven.compiler.source>17</maven.compiler.source></properties></project>")
        with patch.object(provisioner, "_run", return_value=(0, 'openjdk version "11.0.2" 2019')), \
                patch.object(provisioner.shutil, "which", return_value="mvn"):
            project = provisioner.inspect_jvm(self.root, ".", "maven")
        self.assertIn("older than the project's Java 17", project["blocking"][0])


class ProvisionApplyTests(unittest.IsolatedAsyncioTestCase):
    async def test_apply_runs_plan_once_then_reinspects(self):
        class Workspace:
            def __init__(self, root):
                self.root = root
                self.executor = LocalExecutor(LocalExecutionPolicy())
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            write(root, "requirements.txt", "# no third-party dependencies\n")

            def candidates(folder, extra=()):
                found = [{"path": sys.executable, "version": "3.14.0", "source": "mas"}]
                venv = provisioner.python_in(Path(folder) / ".venv")
                if venv:  # the real scan reports a project venv once it exists
                    found.append({"path": str(venv), "version": "3.14.0", "source": "project-venv"})
                return found

            with patch.object(provisioner, "interpreter_candidates", side_effect=candidates):
                dry = await provisioner.provision(Workspace(root), apply=False)
                self.assertFalse(dry["applied"])
                self.assertFalse((root / ".venv").exists())
                self.assertEqual(dry["projects"][0]["plan"][0]["argv"][1:], ["-m", "venv", ".venv"])
                result = await provisioner.provision(Workspace(root), apply=True)
            self.assertTrue((root / ".venv").is_dir())
            self.assertTrue(all(step["exit_code"] == 0 for step in result["executed"]), result["executed"])
            self.assertEqual([step["purpose"] for step in result["executed"]], ["Create project-local virtualenv .venv"])
            self.assertTrue(result["ready"], result["summary"])

class LegacyBuildRecoveryTests(unittest.IsolatedAsyncioTestCase):
    def test_build_requires_are_read_from_pyproject(self):
        with tempfile.TemporaryDirectory() as temp:
            write(temp, "pyproject.toml", '[build-system]\nrequires=["setuptools","cython==0.29.22","not valid !!"]\n')
            write(temp, "setup.py", "from setuptools import setup\nsetup(name='old')\n")
            info = provisioner.python_requirements(temp)
        self.assertEqual(info["build_requires"], ["setuptools", "cython==0.29.22"])
        self.assertTrue(info["legacy_setup_py"] and info["installable"])

    def test_recovery_installs_build_requires_then_builds_without_isolation(self):
        steps = provisioner.legacy_build_recovery(
            "py", "pkg", {"build_requires": ["setuptools>=42", "wheel", "cython==0.29.22"]})
        self.assertEqual(steps[0]["argv"], ["py", "-m", "pip", "install", "setuptools<60", "wheel", "cython==0.29.22"])
        self.assertEqual(steps[1]["argv"], ["py", "-m", "pip", "install", "--no-build-isolation", "--no-deps", "-e", "."])

    def test_recovery_caps_setuptools_scm_for_the_old_toolchain(self):
        steps = provisioner.legacy_build_recovery("py", ".", {"build_requires": ["setuptools_scm>=6.2", "extension-helpers"]})
        self.assertIn("setuptools_scm<7", steps[0]["argv"])
        self.assertNotIn("setuptools_scm>=6.2", steps[0]["argv"])

    def test_error_lines_drop_pip_upgrade_noise(self):
        lines = provisioner.error_lines("Getting requirements to build wheel: finished with status 'error'\nok",
                                        "ModuleNotFoundError: No module named 'extension_helpers'\n"
                                        "You should consider upgrading via pip (error)")
        self.assertEqual(len(lines), 2)
        self.assertTrue(any("extension_helpers" in line for line in lines))

    async def test_failed_editable_install_triggers_recovery_steps(self):
        calls = []

        class Executor:
            async def run_command(self, root, *, command, cwd, unlimited):
                calls.append(command)
                bad = command[-2:] == ["-e", "."] and "--no-build-isolation" not in command
                return {"status": "completed", "exit_code": 1 if bad else 0, "stdout": "", "stderr": "error: boom" if bad else ""}

        class Workspace:
            root = Path(".")
            executor = Executor()

        plan = [{"argv": ["py", "-m", "pip", "install", "-e", "."], "cwd": ".", "purpose": "Install the project itself",
                 "on_failure": provisioner.legacy_build_recovery("py", ".", {"build_requires": []})}]
        report = {"projects": [{"plan": plan, "blocking": []}], "ready": False}
        with patch.object(provisioner, "inspect_project", return_value=report):
            result = await provisioner.provision(Workspace(), apply=True, max_rounds=1)
        self.assertEqual(len(calls), 3)
        self.assertEqual([bool(e.get("recovery")) for e in result["executed"]], [False, True, True])
        self.assertEqual(result["executed"][0]["error_lines"], ["error: boom"])


if __name__ == "__main__":
    unittest.main()
