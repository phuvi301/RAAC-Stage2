"""Task commands execute only in a run-owned Docker container, never on the host."""

import asyncio
import io
import json
import math
import tarfile
import time
from pathlib import Path, PurePosixPath

from docker.errors import DockerException
from pydantic import BaseModel, ConfigDict, Field
from requests.exceptions import RequestException

from .local_executor import OutputLimitError
from .sandbox.policy import SandboxPolicy
from .tools.paths import repo_path

# Python 2.7 compatible so the task image need not contain the host RAAC runtime.
COMMAND_WRAPPER = """
import json, os, signal, subprocess, sys
root = '/workspace'
cwd = os.path.realpath(sys.argv[1])
if cwd != root and not cwd.startswith(root + '/'):
    raise ValueError('cwd escapes workspace')
environment = os.environ.copy()
environment.update(json.loads(sys.argv[2]))
p = subprocess.Popen(sys.argv[3:], cwd=cwd, env=environment, preexec_fn=os.setsid)
try:
    code = p.wait()
finally:
    try: os.killpg(p.pid, signal.SIGKILL)
    except OSError: pass
sys.exit(code if code >= 0 else 128 - code)
"""


class DockerExecutionPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    timeout_seconds: float = Field(default=200.0, gt=0, le=300)
    run_timeout_seconds: float = Field(default=9000.0, gt=0, allow_inf_nan=False)
    max_output_bytes: int = Field(default=131072, ge=1024, le=1048576)
    max_report_bytes: int = Field(default=16777216, ge=1024, le=67108864)


class DockerRunWorkspace:
    def __init__(self, client, image: str, run_id: str, *, policy=None):
        self.client, self.image, self.run_id = client, image, run_id
        self.policy = policy or DockerExecutionPolicy()
        self.container = None

    @staticmethod
    def options(image, run_id):
        if not run_id or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for c in run_id):
            raise ValueError("Invalid run ID")
        return {**SandboxPolicy().container_options(), "image": image,
                "command": ["sleep", "infinity"], "working_dir": "/workspace",
                "labels": {"raac.mas.run_id": run_id}, "init": True,
                "stdin_open": False, "tty": False}

    def __enter__(self):
        self.container = self.client.containers.create(**self.options(self.image, self.run_id))
        try:
            self.container.start()
        except BaseException:
            try:
                self.container.remove(force=True)
            except Exception:  # noqa: BLE001 -- cleanup failure must preserve the startup error.
                self.cleanup_failed = True
            raise
        return self

    def __exit__(self, kind, error, traceback):
        try:
            if self.container is not None:
                self.container.remove(force=True)
        except Exception:
            if error is None:
                raise
            # Preserve the primary failure; the caller can report cleanup separately.
            self.cleanup_failed = True


class DockerExecutor:
    def __init__(self, container, policy: DockerExecutionPolicy, *, deadline=None, trace=None):
        self.container, self.policy = container, policy
        self.deadline = deadline if deadline is not None else time.monotonic() + policy.run_timeout_seconds
        self.environment = None
        self.trace = trace

    def bind_trace(self, trace, context, *, content_enabled):
        def emit(event, data):
            if event == "command_output":
                data = {**data, "bytes": len(data.get("content", "").encode("utf-8"))}
                if not content_enabled:
                    data.pop("content", None)
            trace.emit(context, event=event, **data)

        self.trace = emit

    def _kill(self):
        try:
            self.container.kill()
        except (DockerException, RequestException, OSError) as error:
            self.stop_error = type(error).__name__

    async def _collect(self, request, outputs):
        loop = asyncio.get_running_loop()

        def collect():
            api = self.container.client.api
            identifier = api.exec_create(**request)["Id"]
            stream = api.exec_start(identifier, stream=True, demux=True)
            try:
                for chunk in stream:
                    for name, data in zip(("stdout", "stderr"), chunk, strict=True):
                        if not data:
                            continue
                        remaining = self.policy.max_output_bytes - sum(map(len, outputs.values()))
                        accepted = data[:max(0, remaining)]
                        outputs[name].extend(accepted)
                        if self.trace and accepted:
                            loop.call_soon_threadsafe(self.trace, "command_output", {
                                "stream": name, "content": accepted.decode("utf-8", errors="replace")})
                        if len(data) > remaining:
                            raise OutputLimitError
                return api.exec_inspect(identifier)["ExitCode"]
            finally:
                close = getattr(stream, "close", None)
                if close:
                    close()

        return await asyncio.to_thread(collect)

    async def run_command(self, root, *, command, cwd=".", env=None, timeout_seconds=None, unlimited=False):
        if str(root) != "/workspace":
            raise ValueError("Docker executor root must be /workspace")
        relative = repo_path(cwd, allow_root=True)
        if not isinstance(command, list) or not 1 <= len(command) <= 64 or any(
            not isinstance(arg, str) or "\0" in arg or len(arg) > 4096 for arg in command
        ) or not command[0]:
            raise ValueError("Command must be a nonempty argv list")
        environment = {**(self.environment or {}).get("env", {}), **(env or {})}
        if any(not isinstance(k, str) or not k or "=" in k or "\0" in k
               or not isinstance(v, str) or "\0" in v for k, v in environment.items()):
            raise ValueError("Invalid command environment")
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Benchmark run deadline exceeded")
        timeout = remaining if unlimited else min(remaining, self.policy.timeout_seconds)
        if timeout_seconds is not None:
            if type(timeout_seconds) not in (float, int) or not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= self.policy.timeout_seconds:
                raise ValueError("Command timeout exceeds execution policy")
            timeout = min(timeout, timeout_seconds)
        selected = list(command)
        if selected[0] in {"python", "python3"} and self.environment:
            selected[0] = self.environment["python_executable"]
        directory = str(PurePosixPath("/workspace") / relative)
        request = {"container": self.container.id, "cmd": ["python", "-E", "-s", "-S", "-c", COMMAND_WRAPPER, directory, json.dumps(environment), *selected],
                   "user": "1000:1000", "workdir": "/",
                   "stdout": True, "stderr": True, "stdin": False, "tty": False}
        result = {"mode": "docker", "command": selected, "cwd": directory, "status": "error",
                  "exit_code": None, "summary": "Docker command failed"}
        outputs = {"stdout": bytearray(), "stderr": bytearray()}
        started = time.monotonic()
        task = asyncio.create_task(self._collect(request, outputs))
        try:
            code = await asyncio.wait_for(asyncio.shield(task), timeout)
            result.update(status="completed", exit_code=code, summary="Docker command completed")
        except asyncio.CancelledError:
            self._kill()
            await asyncio.gather(task, return_exceptions=True)
            raise
        except (TimeoutError, OutputLimitError, DockerException, RequestException, OSError) as error:
            self._kill()
            await asyncio.gather(task, return_exceptions=True)
            result["error_kind"] = type(error).__name__
        result.update({name: data.decode("utf-8", errors="replace") for name, data in outputs.items()})
        result["duration_ms"] = round((time.monotonic() - started) * 1000, 3)
        return result

    def put_file(self, path: str, content: bytes):
        target = PurePosixPath(path)
        if not path.startswith("/tmp/") or target.parent != PurePosixPath("/tmp"):
            raise ValueError("Only direct temporary helper files may be uploaded")
        archive = io.BytesIO()
        with tarfile.open(fileobj=archive, mode="w") as stream:
            info = tarfile.TarInfo(target.name)
            info.size, info.mode, info.uid, info.gid = len(content), 0o600, 1000, 1000
            stream.addfile(info, io.BytesIO(content))
        self.container.put_archive("/tmp", archive.getvalue())

    def read_file(self, path: str, limit: int):
        chunks, stat = self.container.get_archive(path)
        if stat.get("linkTarget"):
            raise ValueError("Report must not be a link")
        raw = bytearray()
        for chunk in chunks:
            raw.extend(chunk)
            if len(raw) > limit + 65536:
                raise OutputLimitError
        with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
            members = archive.getmembers()
            if len(members) != 1 or not members[0].isfile() or members[0].size > limit:
                raise ValueError("Expected one bounded regular report file")
            return archive.extractfile(members[0]).read()

    async def run_tests(self, root, *, command, cwd=".", env=None, report_path=None, unlimited=False):
        from uuid import uuid4

        identifier = uuid4().hex
        helper = "/tmp/raac-test-" + identifier + ".py"
        report = "/tmp/raac-report-" + identifier + ".json"
        selected = list(command)
        python = (self.environment or {}).get("python_executable", "python")
        if selected[0] in {"python", "python3"}:
            selected[0] = python
        specification = {"observed_report_path": report, "workspace_root": "/workspace",
                         "command": selected, "cwd": cwd, "env": env or {},
                         "report_path": report_path, "default_report_path": "/tmp/raac-junit-" + identifier + ".xml"}
        if report_path is not None:
            repo_path(report_path)
        self.put_file(helper, Path(__file__).with_name("local_test_runner.py").read_bytes())
        observed = await self.run_command(root, command=[python, "-I", "-B", helper, json.dumps(specification)],
                                          cwd=cwd, env=env, unlimited=unlimited)
        try:
            data = json.loads(self.read_file(report, self.policy.max_report_bytes))
            if observed["status"] != "completed" or type(data.get("tests_run")) is not int or data["tests_run"] <= 0:
                raise ValueError("No reliable test report")
            if type(data.get("failed")) is not int or not 0 <= data["failed"] <= data["tests_run"]:
                raise ValueError("Invalid test counts")
            if observed["exit_code"] != 0 or data["failed"] or data.get("status") != "passed":
                data["status"] = "failed"
            return {**data, "execution": observed}
        except (ValueError, TypeError, KeyError, tarfile.TarError, OutputLimitError, DockerException, OSError):
            return {"status": "error", "tests_run": 0, "failed": 0, "test_cases": [],
                    "summary": "No reliable container test report; runtime may be unsupported", "execution": observed}
