"""Existing workspace operations backed by bounded Docker file RPC, without host mounts."""

import base64
import hashlib
import json
import time
from pathlib import PurePosixPath
from types import SimpleNamespace
from uuid import uuid4

from ..environment import EnvironmentManager
from .paths import EXCLUDED_DIRS, SECRET_FILES, repo_path, test_path
from .workspace import WorkspaceTools

# Trusted Python 2.7-compatible helper; no framework or credentials in the image.
FILE_RPC = r'''
import base64, json, os, re, signal, stat, sys
signal.alarm(30)
request = json.load(open(sys.argv[1]))
os.unlink(sys.argv[1])
root = '/workspace'
def checked(name):
    path = os.path.join(root, name)
    real = os.path.realpath(path)
    if real != root and not real.startswith(root + '/'):
        raise ValueError('path escape')
    current = path
    while current != root:
        if os.path.islink(current): raise ValueError('symlink')
        parent = os.path.dirname(current)
        if parent == current: raise ValueError('invalid root')
        current = parent
    if os.path.isfile(path) and os.stat(path).st_nlink != 1:
        raise ValueError('hardlink')
    return path
def excluded(name):
    lower = name.lower()
    return (lower in request['excluded'] or lower in request['secrets']
        or lower == '.env' or lower.startswith('.env.') or lower.endswith(('.pem','.key'))
        or bool(re.match(r'^\.(?:venv|python)(?:[-_]?\d+(?:[._-]\d+)*)?$',lower))
        or lower == 'build' or lower == 'dist' or lower.endswith(('.egg-info','.pyc','.pyo')))
operation = request['operation']
if operation == 'snapshot':
    result, total = [], 0
    for directory, dirs, names in os.walk(root, followlinks=False):
        dirs[:] = sorted(n for n in dirs if not excluded(n))
        for name in dirs: checked(os.path.relpath(os.path.join(directory,name),root))
        for name in sorted(names):
            if excluded(name): continue
            relative = os.path.relpath(os.path.join(directory,name),root)
            path = checked(relative)
            info = os.stat(path)
            if not stat.S_ISREG(info.st_mode): raise ValueError('nonregular source')
            if info.st_size > request['max_file_bytes']: raise ValueError('file too large')
            with open(path,'rb') as stream: content = stream.read(request['max_file_bytes']+1)
            total += len(content)
            if len(content) > request['max_file_bytes'] or total > request['max_repo_bytes'] or len(result) >= request['max_files']:
                raise ValueError('snapshot limit')
            result.append({'path':relative.replace(os.sep,'/'),'content':base64.b64encode(content).decode('ascii'),'mode':stat.S_IMODE(info.st_mode)})
elif operation == 'python': result = sys.executable
else:
    path = checked(request['path'])
    if operation == 'stat':
        result = {'exists':os.path.exists(path),'file':os.path.isfile(path),'directory':os.path.isdir(path),'size':os.path.getsize(path) if os.path.isfile(path) else 0}
    elif operation == 'read':
        if not stat.S_ISREG(os.stat(path).st_mode): raise ValueError('not regular')
        with open(path,'rb') as stream: content = stream.read(request['max_file_bytes']+1)
        if len(content) > request['max_file_bytes']: raise ValueError('file too large')
        result = base64.b64encode(content).decode('ascii')
    elif operation == 'write':
        content = base64.b64decode(request['content'])
        if len(content) > request['max_file_bytes']: raise ValueError('file too large')
        with open(path,'wb') as stream: stream.write(content)
        result = True
    elif operation == 'mkdir':
        if not os.path.isdir(path): os.makedirs(path)
        result = True
    elif operation == 'delete': os.unlink(path); result = True
    else: raise ValueError('unknown operation')
print(json.dumps(result))
'''


def decode_snapshot(entries, *, max_files, max_file_bytes, max_repo_bytes):
    if not isinstance(entries, list) or len(entries) > max_files:
        raise ValueError("Invalid remote snapshot")
    files, modes, size = {}, {}, 0
    for entry in entries:
        name = repo_path(entry["path"])
        data = base64.b64decode(entry["content"], validate=True)
        size += len(data)
        if name in files or len(data) > max_file_bytes or size > max_repo_bytes:
            raise ValueError("Invalid or oversized remote snapshot")
        if type(entry["mode"]) is not int or not 0 <= entry["mode"] <= 0o777:
            raise ValueError("Invalid source mode")
        files[name], modes[name] = data, entry["mode"]
    return files, modes


class DockerPath:
    """Only the bounded IO methods consumed by WorkspaceTools; never a host Path."""
    def __init__(self, workspace, name="."):
        self.workspace, self.name = workspace, name

    def __str__(self):
        return str(PurePosixPath("/workspace") / self.name)

    def __truediv__(self, name):
        path = str(PurePosixPath(self.name) / name)
        return self.workspace._path(path, allow_root=True)

    @property
    def parent(self):
        return self.workspace._path(str(PurePosixPath(self.name).parent), allow_root=True)

    def is_dir(self):
        return self.workspace._rpc("stat", path=self.name)["directory"]

    def is_file(self):
        return self.workspace._rpc("stat", path=self.name)["file"]

    def exists(self):
        return self.workspace._rpc("stat", path=self.name)["exists"]

    def stat(self):
        return SimpleNamespace(st_size=self.workspace._rpc("stat", path=self.name)["size"])

    def read_text(self, encoding="utf-8-sig"):
        return base64.b64decode(self.workspace._rpc("read", path=self.name), validate=True).decode(encoding)

    def mkdir(self, **kwargs):
        self.workspace._rpc("mkdir", path=self.name)

    def write_text(self, content, encoding="utf-8", newline="\n"):
        self.workspace._rpc("write", path=self.name, content=base64.b64encode(content.encode(encoding)).decode())

    def unlink(self):
        self.workspace._rpc("delete", path=self.name)


class DockerWorkspaceTools(WorkspaceTools):
    def __init__(self, requirement: str, executor):
        if not requirement.strip():
            raise ValueError("Empty requirement")
        self.executor, self.requirement = executor, requirement
        self.root = self.project = DockerPath(self)
        self.requirement_files = {}
        self.baseline_files = self._snapshot()
        self.baseline_modes = dict(self.current_modes)
        self.coder_paths, self.tester_paths = set(), set()
        self.code_edit_count, self.test_spec = 0, None
        self.active_role = "Environment"
        self.environment = EnvironmentManager(self)
        manifest = [(name, hashlib.sha256(data).hexdigest()) for name, data in sorted(self.baseline_files.items())]
        self.baseline_ref = hashlib.sha256((json.dumps(manifest) + "\0" + requirement).encode()).hexdigest()

    def _rpc(self, operation, **values):
        if time.monotonic() >= self.executor.deadline:
            raise TimeoutError("Run deadline exceeded")
        request = {"operation": operation, "excluded": sorted(EXCLUDED_DIRS), "secrets": sorted(SECRET_FILES),
                   "max_files": self.max_files, "max_file_bytes": self.max_file_bytes,
                   "max_repo_bytes": self.max_repo_bytes, **values}
        path = "/tmp/raac-rpc-" + uuid4().hex + ".json"
        self.executor.put_file(path, json.dumps(request).encode())
        result = self.executor.container.exec_run(["python", "-c", FILE_RPC, path],
                                                 user="1000:1000", workdir="/workspace")
        if result.exit_code != 0 or len(result.output) > self.max_repo_bytes * 2 + 1048576:
            raise ValueError("Container file operation failed or exceeded its limit")
        return json.loads(result.output)

    def _snapshot(self):
        files, self.current_modes = decode_snapshot(self._rpc("snapshot"), max_files=self.max_files,
            max_file_bytes=self.max_file_bytes, max_repo_bytes=self.max_repo_bytes)
        return files

    def _path(self, name, *, allow_root=False):
        return DockerPath(self, repo_path(name, allow_root=allow_root))

    def python_candidates(self, limit=20):
        return [self._rpc("python")][:limit]

    def resolve_runtime(self, python_executable, cwd):
        path = PurePosixPath(python_executable)
        if not path.is_absolute():
            if len(path.parts) == 1:
                if python_executable not in {"python", "python3"}:
                    raise ValueError("Use an absolute container Python path")
                return self._rpc("python")
            path = PurePosixPath("/workspace") / repo_path(cwd, allow_root=True) / path
        return str(path)

    async def provision_environment(self, **kwargs):
        return {"status": "blocked", "summary": "Benchmark image is prebuilt and offline; use configure_environment to verify its runtime.",
                "python_candidates": self.python_candidates()}

    def select_coder_patch(self, patch, *, git_cwds=()):
        # Container root is the repo root; no nested host Git path normalization.
        return super().select_coder_patch(patch, git_cwds=())

    async def _mutating_command(self, role, call):
        before = self._snapshot()
        before_modes = dict(self.current_modes)
        result = await call
        if result.get("status") != "completed":
            return result
        after = self._snapshot()
        changed = {name for name in before.keys() | after.keys() if before.get(name) != after.get(name)
                   or before_modes.get(name) != self.current_modes.get(name)}
        for name in changed:
            if role == "Tester" and (name in self.coder_paths or not test_path(name)):
                self.integrity_error = "Tester modified implementation or Coder source"
                raise PermissionError("Tester shell modified implementation or Coder files")
            if role not in {"Coder", "Tester", "Environment"}:
                self.integrity_error = "Read-only role modified source"
                raise PermissionError("Read-only role modified source")
            if role == "Environment":
                self.integrity_error = "Environment modified source"
                raise PermissionError("Environment modified tracked source; rebuild its profile")
        if role == "Coder":
            self.coder_paths.update(changed)
            self.code_edit_count += len(changed)
        elif role == "Tester":
            self.tester_paths.update(changed)
        if changed and self.executor.trace:
            self.executor.trace("workspace_changed", {"role": role, "paths": sorted(changed)})
        return result

    async def run_command(self, *, command, cwd=".", env=None, timeout_seconds=None, unlimited=False):
        return await self._mutating_command(self.active_role, self.executor.run_command(
            self.root, command=command, cwd=cwd, env=env, timeout_seconds=timeout_seconds, unlimited=unlimited))

    async def run_tests(self, **kwargs):
        return await self._mutating_command(self.active_role, super().run_tests(**kwargs))

    def export_candidate(self, output):
        current = self._snapshot()
        if getattr(self, "integrity_error", None):
            raise ValueError("Candidate attribution failed: " + self.integrity_error)
        changed = {name for name in self.baseline_files.keys() | current.keys()
                   if self.baseline_files.get(name) != current.get(name)
                   or self.baseline_modes.get(name) != self.current_modes.get(name)}
        if changed - self.coder_paths - self.tester_paths:
            raise ValueError("Candidate has unattributed source changes")
        for name in changed & self.coder_paths:
            before, after = self.baseline_files.get(name), current.get(name)
            old_mode, mode = self.baseline_modes.get(name, 0o644), self.current_modes.get(name, 0o644)
            if (old_mode ^ mode) & 0o111 or (before is None and mode & 0o111):
                raise ValueError("unsupported executable mode change")
            for data in (before, after):
                if data is not None:
                    if b"\0" in data:
                        raise ValueError("unsupported binary change")
                    try:
                        data.decode("utf-8-sig")
                    except UnicodeError:
                        raise ValueError("unsupported binary encoding") from None
            if (before is None and after == b"") or (before == b"" and after is None):
                raise ValueError("unsupported empty file creation/deletion")
        patch = self.current_patch()
        if current != self._snapshot():
            raise ValueError("Source changed during candidate export")
        output.parent.mkdir(parents=True, exist_ok=True)
        raw = patch.encode("utf-8")
        if len(raw) > 4194304:
            raise ValueError("Candidate patch exceeds size limit")
        with output.open("xb") as stream:
            stream.write(raw)
        if self.executor.trace:
            self.executor.trace("candidate_exported", {"paths": sorted(changed & self.coder_paths),
                                                      "patch_sha256": hashlib.sha256(raw).hexdigest()})
        return {"path": str(output), "sha256": hashlib.sha256(raw).hexdigest(),
                "baseline_ref": self.baseline_ref, "changed_paths": sorted(changed & self.coder_paths)}
