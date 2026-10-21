"""Observe environment readiness and bind one runtime to later development tools."""

from copy import deepcopy
from pathlib import Path
import re


class EnvironmentManager:
    def __init__(self, workspace):
        self.workspace = workspace
        self.configuration = None
        self.verification = []
        self.revision = 0
        self.ready = False
        self.smoke_report = None

    async def configure(self, *, python_executable, cwd, test_command, check_command, env=None):
        self.ready = False
        self.smoke_report = None
        workspace = self.workspace
        if not workspace._path(cwd, allow_root=True).is_dir():
            raise ValueError("Environment cwd must be a repository directory")
        candidate = Path(python_executable)
        if not candidate.is_absolute():
            candidate = workspace.root / cwd / candidate
        candidate = candidate.absolute()  # Preserve venv/bin/python symlink identity.
        if not candidate.is_file():
            raise ValueError("Python executable must exist")
        # Check candidate directly, before replacing the previous runtime binding.
        old = getattr(workspace.executor, "environment", None)
        workspace.executor.environment = None
        verification = []
        try:
            commands = [[str(candidate), "-c", "import sys; print(sys.executable); print(sys.version)"]]
            selected = list(test_command)
            if re.fullmatch(r"python(?:3(?:\.\d+)?)?(?:\.exe)?", Path(selected[0]).name, re.I):
                selected[0] = str(candidate)
            # Always collect the actual selected pytest suite. A successful pip
            # install/version command alone is not evidence tests can be imported.
            if len(selected) >= 3 and selected[1:3] == ["-m", "pytest"]:
                check = [*selected, "--collect-only", "-q"]
            else:
                check = list(check_command)
                if re.fullmatch(r"python(?:3(?:\.\d+)?)?(?:\.exe)?", Path(check[0]).name, re.I):
                    check[0] = str(candidate)
            commands.append(check)
            for command in commands:
                observation = await workspace.executor.run_command(
                    workspace.root, command=command, cwd=cwd, env=env or {}, unlimited=True)
                verification.append(observation)
                if observation.get("status") != "completed" or observation.get("exit_code") != 0:
                    self.verification = verification
                    return {"ready": False, "status": "failed", "verification": verification,
                            "summary": "Environment verification failed; inspect stdout/stderr and repair dependencies."}
            if len(selected) >= 3 and selected[1:3] == ["-m", "pytest"]:
                smoke = await workspace.executor.run_tests(
                    workspace.root, command=selected, cwd=cwd, env=env or {}, unlimited=True)
                failures, seen = [], set()
                for item in smoke.get("results", []):
                    if item.get("status") not in {"failed", "error"}:
                        continue
                    signature = item.get("details", "").split("\n", 1)[0]
                    if signature not in seen:
                        seen.add(signature)
                        failures.append({**item, "details": item.get("details", "")[:1500]})
                self.smoke_report = {key: smoke.get(key) for key in
                    ("status", "tests_run", "failed", "skipped", "expected_failures", "summary")}
                self.smoke_report["failure_samples"] = failures[:8]
                if smoke.get("status") == "error" or smoke.get("tests_run", 0) <= 0:
                    self.verification = verification
                    return {"ready": False, "status": "failed", "verification": verification,
                            "smoke_report": self.smoke_report,
                            "summary": "Environment execution check could not produce a reliable test report."}
        finally:
            workspace.executor.environment = old
        self.configuration = {"python_executable": str(candidate), "cwd": cwd,
                              "test_command": selected, "check_command": check, "env": env or {}}
        self.verification = verification
        self.revision += 1
        self.ready = True
        workspace.executor.environment = deepcopy(self.configuration)
        workspace.test_spec = {"command": selected, "cwd": cwd, "env": env or {}, "report_path": None}
        return {"ready": True, "status": "completed", "configuration": deepcopy(self.configuration),
                "verification": verification, "smoke_report": deepcopy(self.smoke_report), "revision": self.revision}
