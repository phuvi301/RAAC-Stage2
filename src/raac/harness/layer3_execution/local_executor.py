"""Execute MAS development commands directly on the laptop in the real repo."""

import asyncio
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .tools.paths import repo_path


class LocalExecutionPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    mode: Literal["local"] = "local"
    timeout_seconds: float = Field(default=60.0, gt=0, le=300)
    max_output_bytes: int = Field(default=131072, ge=1024, le=1048576)


class OutputLimitError(RuntimeError):
    pass


class LocalExecutor:
    def __init__(self, policy: LocalExecutionPolicy):
        self.policy = policy

    def resolve_command(self, command, root, environment):
        if (not isinstance(command, list) or not 1 <= len(command) <= 64
                or any(not isinstance(arg, str) or "\0" in arg or len(arg) > 4096 for arg in command)
                or not command[0]):
            raise ValueError("Command must be a nonempty argv list")
        command = list(command)
        # Prefer this project's virtualenv, then the interpreter running MAS.
        # This also avoids the Windows Store alias for bare 'python'.
        if command[0].lower() in {"python", "python3", "python.exe"}:
            candidates = [root / name / subpath for name in (".venv", "venv")
                          for subpath in ("Scripts/python.exe", "bin/python")]
            command[0] = str(next((path for path in candidates if path.is_file()), Path(sys.executable)))
        elif not Path(command[0]).is_absolute():
            local = root / command[0]
            if local.is_file():
                command[0] = str(local)
            else:
                command[0] = shutil.which(command[0], path=environment.get("PATH")) or command[0]
        return command

    @staticmethod
    async def _stop(process):
        # Stop the process tree as well as the parent when timeout/cancellation happens.
        if os.name == "nt":
            if process.returncode is None:
                killer = await asyncio.create_subprocess_exec(
                    "taskkill", "/PID", str(process.pid), "/T", "/F",
                    stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                await killer.wait()
                if process.returncode is None:
                    try:
                        process.kill()
                    except ProcessLookupError:
                        pass
        else:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        await process.wait()

    async def run_command(self, root: Path, *, command, cwd=".", env=None, timeout_seconds=None):
        root = Path(root).resolve()
        relative = repo_path(cwd, allow_root=True)
        directory = (root / relative).resolve()
        if not directory.is_relative_to(root) or not directory.is_dir():
            raise ValueError("Command cwd must be an existing directory inside repo")
        environment = os.environ.copy()
        for key, value in (env or {}).items():
            if not key or "=" in key or "\0" in key or not isinstance(value, str) or "\0" in value:
                raise ValueError("Invalid command environment")
            environment[key] = value
        environment.setdefault("PYTHONIOENCODING", "utf-8")
        resolved = self.resolve_command(command, root, environment)
        timeout = self.policy.timeout_seconds if timeout_seconds is None else timeout_seconds
        if not isinstance(timeout, (int, float)) or not 0 < timeout <= self.policy.timeout_seconds:
            raise ValueError("Command timeout exceeds execution policy")
        started = time.perf_counter()
        result = {"mode": "local", "command": resolved, "cwd": str(directory),
                  "exit_code": None, "stdout": "", "stderr": "", "status": "error"}
        options = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {"start_new_session": True}
        try:
            process = await asyncio.create_subprocess_exec(
                *resolved, cwd=directory, env=environment, stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, **options)
        except OSError as error:
            return {**result, "error_kind": type(error).__name__,
                    "summary": "Cannot start command on laptop: " + str(error)}
        outputs = {"stdout": bytearray(), "stderr": bytearray()}

        async def read(stream, name):
            while chunk := await stream.read(8192):
                remaining = self.policy.max_output_bytes - sum(map(len, outputs.values()))
                outputs[name].extend(chunk[:max(0, remaining)])
                if len(chunk) > remaining:
                    raise OutputLimitError

        tasks = [asyncio.create_task(read(process.stdout, "stdout")),
                 asyncio.create_task(read(process.stderr, "stderr")), asyncio.create_task(process.wait())]
        try:
            await asyncio.wait_for(asyncio.gather(*tasks), timeout)
            result.update(exit_code=process.returncode, status="completed",
                          summary="Local command completed")
        except (TimeoutError, OutputLimitError) as error:
            result.update(error_kind=type(error).__name__,
                          summary="Local command timeout" if isinstance(error, TimeoutError) else "Local command output limit exceeded")
        finally:
            if process.returncode is None or any(not task.done() for task in tasks):
                await self._stop(process)
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        result.update({name: data.decode("utf-8", errors="replace") for name, data in outputs.items()})
        result["duration_ms"] = round((time.perf_counter() - started) * 1000, 3)
        return result

    async def run_tests(self, root: Path, *, command, cwd=".", env=None, report_path=None):
        # Run the trusted report helper in a separate local process so imports,
        # sys.path and test code cannot corrupt the MAS Python process itself.
        environment = {**os.environ, **(env or {})}
        command = self.resolve_command(command, Path(root), environment)
        if report_path is not None:
            repo_path(report_path)
        with tempfile.TemporaryDirectory(prefix="raac-local-report-") as temporary:
            specification = {"workspace_root": str(Path(root).resolve()), "command": command,
                             "cwd": cwd, "env": env or {}, "report_path": report_path,
                             "default_report_path": str(Path(temporary) / "junit.xml")}
            helper_python = command[0] if re.fullmatch(r"python(?:3(?:\.\d+)?)?(?:\.exe)?", Path(command[0]).name, re.I) else sys.executable
            observed = await self.run_command(root, command=[helper_python, "-I", "-B",
                str(Path(__file__).with_name("local_test_runner.py")), json.dumps(specification)], cwd=cwd)
        failure = {"status": "error", "tests_run": 0, "failed": 0, "test_cases": [],
                   "summary": observed["summary"], "execution": observed}
        if observed["status"] != "completed":
            return failure
        reports = [line.removeprefix("RAAC_TEST_REPORT=") for line in observed["stdout"].splitlines()
                   if line.startswith("RAAC_TEST_REPORT=")]
        try:
            if len(reports) != 1:
                raise ValueError("Missing or ambiguous test execution report")
            report = json.loads(reports[0])
            if (type(report.get("tests_run")) is not int or report["tests_run"] < 0
                    or type(report.get("failed")) is not int or not 0 <= report["failed"] <= report["tests_run"]
                    or not isinstance(report.get("test_cases"), list)):
                raise ValueError("Invalid test execution report")
        except (ValueError, TypeError):
            return {**failure, "summary": "No reliable local test report; inspect stdout/stderr"}
        if report["tests_run"] == 0:
            report["status"] = "error"
        elif observed["exit_code"] != 0 or report["failed"] or report.get("status") != "passed":
            report["status"] = "failed"
        return {**report, "execution": observed}
