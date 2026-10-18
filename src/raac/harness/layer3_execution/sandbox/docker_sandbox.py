"""Docker-only repository test pipeline. Repository code never runs on the host."""

import hashlib
import io
import json
import os
import re
import shlex
import subprocess
import tarfile
import tempfile
import time
import uuid
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from dataclasses import asdict, replace
from pathlib import Path

import yaml

import docker
from raac.benchmark.dataset import load_tasks

from .build import BuildTimeout, build_image
from .policy import SandboxPolicy

ROOT = Path(__file__).resolve().parents[5]


def digest(value):
    return hashlib.sha256(value).hexdigest()


def safe_snapshot(value):
    """Redact credentials if an operator accidentally puts one in a profile."""
    if isinstance(value, dict):
        return {
            k: "[REDACTED]"
            if any(
                word in k.lower()
                for word in ("password", "secret", "api_key", "access_token")
            )
            else safe_snapshot(v)
            for k, v in value.items()
        }
    if isinstance(value, list):
        result = []
        redact_next = False
        for item in value:
            result.append("[REDACTED]" if redact_next else safe_snapshot(item))
            redact_next = isinstance(item, str) and item in {
                "--password",
                "--token",
                "--api-key",
            }
        return result
    if isinstance(value, str):
        value = re.sub(r"(https?://)[^/\s@]+@", r"\1[REDACTED]@", value)
        for key, secret in os.environ.items():
            if len(secret) >= 8 and any(
                word in key.upper()
                for word in ("TOKEN", "SECRET", "PASSWORD", "API_KEY")
            ):
                value = value.replace(secret, "[REDACTED]")
    return value


def resolve_path(root, value):
    path = Path(value)
    return path.resolve() if path.is_absolute() else (root / path).resolve()


@contextmanager
def cache_lock(path, timeout=120, on_wait=None):
    """Cross-process advisory lock; OS releases the lock after a process crash."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")
    if path.stat().st_size == 0:
        handle.write(b"0")
        handle.flush()
    deadline = time.monotonic() + timeout
    notified = 0
    while True:
        try:
            handle.seek(0)
            if __import__("os").name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except OSError:
            if on_wait and time.monotonic() - notified >= 10:
                try:
                    on_wait()
                except BaseException:
                    handle.close()
                    raise
                notified = time.monotonic()
            if time.monotonic() >= deadline:
                handle.close()
                raise TimeoutError(f"Cache lock timed out: {path}")
            time.sleep(0.1)
    try:
        yield
    finally:
        handle.seek(0)
        if __import__("os").name == "nt":
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(handle, fcntl.LOCK_UN)
        handle.close()


def git(*args, timeout=300):
    return subprocess.run(
        ["git", "-c", "core.hooksPath=/dev/null", *map(str, args)],
        check=True,
        capture_output=True,
        timeout=timeout,
    )


def archive_repo(task, cache_root, destination, versioned=False, deadline=None):
    def remaining():
        value = deadline - time.monotonic() if deadline is not None else 1800
        if value <= 0:
            raise BuildTimeout("Preparation budget exhausted while fetching source")
        return value

    def invoke(*args, timeout=300):
        return git(*args, timeout=min(timeout, remaining()))

    cache = cache_root / (task.repo + ".git")
    with cache_lock(cache.with_suffix(".lock"), timeout=min(120, remaining())):
        if not cache.exists():
            # Clone into a temporary sibling so interrupted clones cannot poison the cache.
            with tempfile.TemporaryDirectory(dir=cache.parent) as tmp:
                staged = Path(tmp) / "repo.git"
                invoke("init", "--bare", staged)
                invoke("--git-dir", staged, "remote", "add", "origin", task.repo_url)
                invoke(
                    "--git-dir",
                    staged,
                    "fetch",
                    "--depth=1",
                    "origin",
                    task.base_commit,
                    timeout=1800,
                )
                staged.rename(cache)
        try:
            invoke("--git-dir", cache, "cat-file", "-e", task.base_commit + "^{commit}")
        except subprocess.CalledProcessError:
            invoke(
                "--git-dir",
                cache,
                "fetch",
                "--depth=1",
                "origin",
                task.base_commit,
                timeout=1800,
            )
        tag = None
        if versioned:
            # Full commit ancestry is necessary for exact SCM distances across
            # merges; merely finding a nearby tag in a shallow graph undercounts.
            if (cache / "shallow").exists():
                invoke(
                    "--git-dir",
                    cache,
                    "fetch",
                    "--unshallow",
                    "--tags",
                    "origin",
                    task.base_commit,
                    timeout=1800,
                )
            elif not (cache / "raac-tags-fetched").exists():
                invoke(
                    "--git-dir",
                    cache,
                    "fetch",
                    "--tags",
                    "origin",
                    task.base_commit,
                    timeout=1800,
                )
            (cache / "raac-tags-fetched").write_text("1")
            tag = (
                invoke(
                    "--git-dir",
                    cache,
                    "describe",
                    "--tags",
                    "--abbrev=0",
                    task.base_commit,
                )
                .stdout.decode()
                .strip()
            )
        with destination.open("wb") as output:
            subprocess.run(
                [
                    "git",
                    "-c",
                    "core.autocrlf=false",
                    "-c",
                    "core.eol=lf",
                    "--git-dir",
                    str(cache),
                    "archive",
                    "--format=tar",
                    task.base_commit,
                ],
                stdout=output,
                stderr=subprocess.PIPE,
                check=True,
                timeout=min(300, remaining()),
            )
        # Preserve the real HEAD for versioneer/setuptools-scm without copying history,
        # hooks, host config, credentials, or cache paths into the build context.
        with tempfile.TemporaryDirectory() as packed:
            tree = invoke(
                "--git-dir",
                cache,
                "rev-list",
                "--objects",
                "--no-object-names",
                task.base_commit + "^{tree}",
            ).stdout
            commits = (
                invoke(
                    "--git-dir",
                    cache,
                    "rev-list",
                    "--objects",
                    "--filter=blob:none",
                    "--no-object-names",
                    task.base_commit,
                ).stdout
                if versioned
                else task.base_commit.encode() + b"\n"
            )
            objects = tree + commits
            tags = []
            if tag:
                refs = (
                    invoke(
                        "--git-dir",
                        cache,
                        "for-each-ref",
                        "--merged=" + task.base_commit,
                        "--format=%(refname:strip=2) %(objectname)",
                        "refs/tags",
                    )
                    .stdout.decode()
                    .splitlines()
                )
                tags = [line.split(" ", 1) for line in refs]
                objects += b"".join(oid.encode() + b"\n" for _, oid in tags)
            subprocess.run(
                [
                    "git",
                    "-c",
                    "pack.writeReverseIndex=false",
                    "--git-dir",
                    str(cache),
                    "pack-objects",
                    # Stable source bytes across thread scheduling, cache repacks,
                    # and host Git compression/index defaults. Otherwise image keys
                    # can change between preflight and full for the same commit.
                    "--threads=1",
                    "--no-reuse-delta",
                    "--no-reuse-object",
                    "--compression=6",
                    "--window=10",
                    "--depth=50",
                    "--index-version=2",
                    str(Path(packed) / "pack"),
                ],
                input=objects,
                check=True,
                capture_output=True,
                timeout=min(300, remaining()),
            )
            with tarfile.open(destination, "a") as tar:

                def add(name, value):
                    item = tarfile.TarInfo(name)
                    item.size = len(value)
                    item.mode = 0o644
                    tar.addfile(item, io.BytesIO(value))

                refs = tarfile.TarInfo(".git/refs")
                refs.type = tarfile.DIRTYPE
                refs.mode = 0o755
                tar.addfile(refs)
                add(".git/HEAD", task.base_commit.encode() + b"\n")
                if versioned:
                    if (cache / "shallow").exists():
                        roots = set(
                            (cache / "shallow").read_bytes().splitlines()
                        ) & set(commits.splitlines())
                        if roots:
                            add(".git/shallow", b"\n".join(sorted(roots)) + b"\n")
                    for name, oid in tags:
                        add(".git/refs/tags/" + name, oid.encode() + b"\n")
                else:
                    add(".git/shallow", task.base_commit.encode() + b"\n")
                add(
                    ".git/config",
                    b'[core]\nrepositoryformatversion = 0\nbare = false\nautocrlf = false\n[remote "origin"]\nurl = https://github.com/'
                    + task.repo.encode()
                    + b"\n",
                )
                for file in sorted(Path(packed).iterdir()):
                    add(".git/objects/pack/" + file.name, file.read_bytes())
    return cache


def load_profile(path, task):
    profile = yaml.safe_load(path.read_text(encoding="utf-8"))
    if profile.get("repo") != task.repo:
        raise ValueError("Profile repo does not match task")
    overrides = profile.pop("task_overrides", {})
    profile.update(overrides.get(task.id, {}))
    allowed = {
        "version",
        "repo",
        "base_image",
        "system_packages",
        "install_commands",
        "dependency_commands",
        "post_patch_commands",
        "native_rebuild_paths",
        "native_safe_paths",
        "preflight_imports",
        "source_history",
        "test_command",
        "environment",
        "test_framework",
        "report_format",
        "report_path",
        "build_timeout",
        "test_timeout",
    }
    if set(profile) - allowed:
        raise ValueError("Unknown profile fields")
    if not isinstance(profile.get("version"), (int, str)):
        raise TypeError("Profile version required")
    if not re.fullmatch(
        r"[a-zA-Z0-9][a-zA-Z0-9/_.:@-]*", profile.get("base_image", "")
    ):
        raise ValueError("Invalid base image")
    packages = profile.get("system_packages", [])
    if not isinstance(packages, list) or any(
        not isinstance(x, str) or not re.fullmatch(r"[a-z0-9][a-z0-9+.-]*", x)
        for x in packages
    ):
        raise ValueError("Invalid system packages")
    commands = (
        profile.get("dependency_commands", [])
        + profile.get("install_commands", [])
        + profile.get("post_patch_commands", [])
    )
    if not isinstance(commands, list):
        raise TypeError("install_commands must be a list")
    for command in [*commands, profile.get("test_command")]:
        if (
            not isinstance(command, list)
            or not command
            or any(not isinstance(x, str) or not x or "\x00" in x for x in command)
        ):
            raise ValueError("Commands must be nonempty argument lists")
    if profile["repo"] != task.repo:
        raise ValueError("Overrides cannot change the repo")
    if profile.get("source_history", "snapshot") not in {"snapshot", "versioned"}:
        raise ValueError("Unknown source_history")
    environment = profile.get("environment", {})
    if not isinstance(environment, dict) or any(
        not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", str(k)) or not isinstance(v, str)
        for k, v in environment.items()
    ):
        raise ValueError("environment must map valid variable names to strings")
    if safe_snapshot(environment) != environment or any(
        any(word in k.upper() for word in ("SECRET", "TOKEN", "PASSWORD", "API_KEY"))
        for k in environment
    ):
        raise ValueError("Secrets are not allowed in profile environment")
    for key in ("build_timeout", "test_timeout"):
        if key in profile and (type(profile[key]) is not int or profile[key] <= 0):
            raise ValueError(f"{key} must be a positive integer")
    if profile.get("test_framework", "pytest") not in {"pytest", "django", "command"}:
        raise ValueError("Unknown test_framework")
    if profile.get("report_format", "junit") not in {"junit", "none"}:
        raise ValueError("Unknown report_format")
    report_path = profile.get("report_path", "/workspace/pytest.xml")
    if not report_path.startswith("/workspace/") or ".." in report_path.split("/"):
        raise ValueError("report_path must be inside /workspace")
    for field in ("native_rebuild_paths", "native_safe_paths", "preflight_imports"):
        if not isinstance(profile.get(field, []), list) or any(
            not isinstance(x, str) or not x for x in profile.get(field, [])
        ):
            raise ValueError(f"{field} must be a list of nonempty strings")
    return profile


def dockerfile(profile):
    packages = profile.get("system_packages", [])
    system_setup = ""
    if packages:
        system_setup = (
            "RUN apt-get update && apt-get install -y --no-install-recommends "
            + " ".join(packages)
            + " && rm -rf /var/lib/apt/lists/*"
        )
    template = (ROOT / "docker/Dockerfile.sandbox").read_text(encoding="utf-8")
    return template.format(
        base_image=profile["base_image"],
        system_setup=system_setup,
        environment="\n".join(
            "ENV " + k + "=" + json.dumps(v)
            for k, v in sorted(profile.get("environment", {}).items())
        ),
        dependency_steps="\n".join(
            "RUN " + json.dumps(c) for c in profile.get("dependency_commands", [])
        ),
        install_steps="\n".join(
            "RUN " + json.dumps(c) for c in profile.get("install_commands", [])
        ),
    )


def junit_stats(raw):
    root = ET.fromstring(raw)
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    return {
        key: sum(int(s.get(key, 0)) for s in suites)
        for key in ("tests", "failures", "errors", "skipped")
    }


class DockerSandbox:
    def __init__(self, root=ROOT, config=None, policy=None, client=None):
        self.root = Path(root).resolve()
        self.config_path = resolve_path(self.root, config or "configs/benchmark.yaml")
        self.config = yaml.safe_load(self.config_path.read_text(encoding="utf-8"))
        self.policy = policy or SandboxPolicy()
        self.default_policy = self.policy
        self.client = client

    def connect(self):
        if self.client is None:
            self.client = docker.from_env(timeout=30)
        info = self.client.info()
        if info.get("OSType") != "linux":
            raise RuntimeError("Docker Engine must use Linux containers")
        return info

    def check(self):
        try:
            info = self.connect()
            git("--version")
            return {
                "status": "ready",
                "docker_os": info["OSType"],
                "server_version": info.get("ServerVersion"),
            }
        except Exception as exc:  # noqa: BLE001 -- public boundary must report Docker and OS failures
            return {
                "status": "unavailable",
                "error": str(exc),
                "hint": "Start Docker Desktop and select Linux containers; no host test fallback is supported.",
            }

    def execute(
        self,
        image,
        command,
        directory,
        label,
        patch=None,
        profile=None,
        evaluation=None,
    ):
        profile = profile or {}
        volume = self.client.volumes.create(labels={"raac": directory.name})
        container = None
        started = time.monotonic()
        log = directory / f"{label}.log"
        try:
            # Docker populates a new empty volume from the image's /workspace.
            script = (
                "set -eu; rm -f "
                + shlex.quote(profile.get("report_path", "/workspace/pytest.xml"))
                + "; "
            )
            if patch is not None:
                script += "cd /workspace; git apply --check /tmp/candidate.patch || exit 86; git apply /tmp/candidate.patch || exit 86; "
            if evaluation is not None:
                script += "git apply --check /tmp/test.patch || exit 88; git apply /tmp/test.patch || exit 88; "
            from .optimization import needs_native_rebuild

            effective_patch = (patch or b"") + (
                evaluation["test_patch"].encode() if evaluation else b""
            )
            rebuilt = needs_native_rebuild(profile, effective_patch or None)
            if rebuilt:
                for rebuild in profile.get("post_patch_commands", []):
                    script += shlex.join(rebuild) + " || exit 87; "
            script += "exec " + shlex.join(command)
            container = self.client.containers.create(
                image.id,
                ["/bin/sh", "-c", script],
                working_dir="/workspace",
                volumes={volume.name: {"bind": "/workspace", "mode": "rw"}},
                labels={"raac": directory.name},
                use_config_proxy=False,
                environment=profile.get("environment", {}),
                **self.policy.container_options(),
            )
            files = {"candidate.patch": patch} if patch is not None else {}
            if evaluation is not None:
                files.update(
                    {
                        "test.patch": evaluation["test_patch"].encode(),
                        "raac-issue-runner.py": evaluation.get("runner_source")
                        or Path(__file__).with_name("issue_runner.py").read_bytes(),
                        "raac-selectors.json": json.dumps(
                            evaluation["selectors"]
                        ).encode(),
                    }
                )
            if files:
                stream = io.BytesIO()
                with tarfile.open(fileobj=stream, mode="w") as tar:
                    for name, content in files.items():
                        item = tarfile.TarInfo(name)
                        item.size = len(content)
                        item.mode = 0o444
                        tar.addfile(item, io.BytesIO(content))
                container.put_archive("/tmp", stream.getvalue())
            container.start()
            # The Docker HTTP timeout is shorter than the run timeout; poll state instead of wait().
            deadline = started + self.policy.timeout
            timed_out = False
            log.write_bytes(b"")
            while True:
                container.reload()
                log.write_bytes(container.logs(stdout=True, stderr=True))
                if getattr(self, "phase_progress", None):
                    self.phase_progress(label, log)
                if container.status in ("exited", "dead"):
                    break
                if time.monotonic() >= deadline:
                    timed_out = True
                    container.kill()
                    container.reload()
                    break
                time.sleep(1)
            code = container.attrs["State"].get("ExitCode")
            log.write_bytes(container.logs(stdout=True, stderr=True))
            stats = None
            try:
                if profile.get("report_format", "junit") == "none":
                    raise ValueError("Runner does not produce JUnit")
                chunks, _ = container.get_archive(
                    profile.get("report_path", "/workspace/pytest.xml")
                )
                with tarfile.open(fileobj=io.BytesIO(b"".join(chunks))) as tar:
                    raw = tar.extractfile(tar.getmembers()[0]).read()
                (directory / f"{label}.xml").write_bytes(raw)
                stats = junit_stats(raw)
            except (docker.errors.NotFound, ET.ParseError, ValueError, KeyError):
                pass
            issue_report = None
            issue_report_available = False
            if evaluation is not None:
                try:
                    chunks, _ = container.get_archive("/workspace/raac-tests.json")
                    with tarfile.open(fileobj=io.BytesIO(b"".join(chunks))) as tar:
                        raw = tar.extractfile(tar.getmembers()[0]).read()
                    (directory / f"{label}.tests.json").write_bytes(raw)
                    issue_report = json.loads(raw)
                    issue_report_available = True
                except (docker.errors.NotFound, ValueError, KeyError):
                    issue_report = {"tests": {}, "errors": ["Missing per-test report"]}
            if issue_report_available:
                outcomes = [
                    v.get("outcome") for v in issue_report.get("tests", {}).values()
                ]
                stats = {
                    "tests": len(outcomes),
                    "failures": outcomes.count("failed"),
                    "errors": sum(x in {"runner_error", "mixed"} for x in outcomes),
                    "skipped": sum(
                        x in {"skipped", "xfail", "xpass"} for x in outcomes
                    ),
                }
            native_count = None
            if profile.get("test_framework") == "django" and evaluation is None:
                text = log.read_text(encoding="utf-8", errors="replace")
                summaries = list(
                    re.finditer(r"^Ran (\d+) tests? in[^\r\n]*$", text, re.MULTILINE)
                )
                if summaries:
                    matched = summaries[-1]
                    native_count = int(matched[1])
                    footer = re.search(
                        r"^(?:FAILED|OK)(?: \(([^\r\n]*)\))?\s*$",
                        text[matched.end() :],
                        re.MULTILINE,
                    )
                    details = footer[1] or "" if footer else ""
                    counters = dict(
                        re.findall(
                            r"(?:^|,\s*)(failures|errors|skipped|expected failures|unexpected successes)=(\d+)",
                            details,
                        )
                    )
                    stats = {"tests": native_count}
                    for counter_name in (
                        "failures",
                        "errors",
                        "skipped",
                        "expected failures",
                        "unexpected successes",
                    ):
                        stats[counter_name.replace(" ", "_")] = int(
                            counters.get(counter_name, 0)
                        )
            if timed_out:
                status = "timeout"
            elif patch is not None and code == 86:
                status = "patch_error"
            elif evaluation is not None and code == 88:
                status = "test_patch_error"
            elif code == 87:
                status = "patch_setup_error"
            elif issue_report is not None:
                status = (
                    "collection_error"
                    if issue_report.get("errors")
                    else "passed"
                    if code == 0
                    else "test_failed"
                    if code == 1
                    else "execution_error"
                )
            elif profile.get("test_framework") == "django" and native_count is None:
                status = "collection_error" if code == 1 else "execution_error"
            elif profile.get("test_framework") == "django" and native_count == 0:
                status = "no_tests"
            elif profile.get("test_framework", "pytest") != "pytest":
                status = (
                    "passed"
                    if code == 0
                    else "test_failed"
                    if code == 1
                    else "execution_error"
                )
            else:
                status = {
                    0: "passed",
                    1: "test_failed",
                    2: "collection_error",
                    3: "pytest_error",
                    4: "test_command_error",
                    5: "no_tests",
                }.get(code, "execution_error")
            return {
                "status": status,
                "native_rebuilt": rebuilt,
                "found_selectors": issue_report.get("found", [])
                if issue_report
                else [],
                "test_results": issue_report.get("tests", {})
                if issue_report is not None
                else None,
                "selector_errors": issue_report.get("errors", [])
                if issue_report is not None
                else None,
                "exit_code": code,
                "timeout": timed_out,
                "duration_seconds": round(time.monotonic() - started, 3),
                "pytest": stats
                if profile.get("test_framework", "pytest") == "pytest"
                else None,
                "test_statistics": stats,
                "report_format": "per-test-json"
                if issue_report is not None
                else profile.get("report_format", "junit"),
                "statistics_available": stats is not None,
                "runner": profile.get("test_framework", "pytest"),
                "log": str(log),
            }
        finally:
            try:
                if container is not None:
                    container.remove(force=True)
            finally:
                volume.remove(force=True)
                if getattr(self, "phase_progress", None):
                    self.phase_progress("cleanup", log)

    def run(self, *args, **kwargs):
        # All entrypoints share one workload slot, including separate CLI processes.
        with cache_lock(self.root / "data/sandbox-run.lock", timeout=1800):
            return self._run(*args, **kwargs)

    def _run(
        self,
        task_id,
        dataset=None,
        patch_path=None,
        prepare=False,
        build_timeout=None,
        test_timeout=None,
        run_id=None,
        test_mode="issue",
        validate=False,
        stage="full",
        preflight=False,
        reuse_validated=False,
        progress=None,
    ):
        run_id = run_id or uuid.uuid4().hex
        directory = resolve_path(self.root, self.config["results"]) / run_id
        directory.mkdir(parents=True)
        record = {
            "run_id": run_id,
            "task_id": task_id,
            "status": "preparation_error",
            "baseline": None,
            "candidate": None,
        }
        started = time.monotonic()
        last_event = [None, 0]

        def phase(name, log=None):
            if getattr(self, "stop_file", None) and self.stop_file.exists():
                raise KeyboardInterrupt
            if last_event[0] == name and time.monotonic() - last_event[1] < 10:
                return
            last_event[:] = [name, time.monotonic()]
            event = {
                "event": "phase",
                "task_id": task_id,
                "run_id": run_id,
                "phase": name,
                "elapsed_seconds": round(time.monotonic() - started, 1),
                "log": str(log) if log else None,
            }
            (directory / "progress.json").write_text(
                json.dumps(event), encoding="utf-8"
            )
            if progress:
                progress(event)
            if getattr(self, "stop_file", None) and self.stop_file.exists():
                raise KeyboardInterrupt

        self.phase_progress = phase
        try:
            if preflight and test_mode != "issue":
                raise ValueError("Preflight requires issue mode")
            if stage not in {"pilot", "full"}:
                raise ValueError("Unknown validation stage")
            if stage != "full" and not validate:
                raise ValueError("Pilot requires validation")
            record["stage"] = stage
            record["operation"] = (
                "preflight" if preflight else "validate" if validate else "run"
            )
            dataset_path = resolve_path(self.root, dataset or self.config["dataset"])
            task = load_tasks(dataset_path)[task_id]
            record["task"] = asdict(task)
            if test_mode not in {"suite", "issue"}:
                raise ValueError("Unknown test mode")
            record["test_mode"] = test_mode
            if validate and test_mode != "issue":
                raise ValueError("Validation requires issue mode")
            profile_name = self.config.get("profiles", {}).get(task.repo)
            if profile_name is None:
                record["status"] = "profile_missing"
                return record
            profile_path = resolve_path(self.root, profile_name)
            profile = load_profile(profile_path, task)
            record["profile_path"] = str(profile_path)
            self.policy = replace(
                self.default_policy,
                timeout=min(test_timeout, profile.get("test_timeout", test_timeout))
                if test_timeout
                else profile.get("test_timeout", self.default_policy.timeout),
            )
            effective_build_timeout = (
                min(build_timeout, profile.get("build_timeout", build_timeout))
                if build_timeout
                else profile.get("build_timeout", 1800)
            )
            record["build_timeout"] = effective_build_timeout
            preparation_deadline = time.monotonic() + effective_build_timeout
            # Only this explicit schema is accepted: arbitrary credential-bearing config is not logged.
            snapshot = {
                "profile": profile,
                "policy": self.policy.snapshot(),
                "dataset": str(dataset_path),
                "repo_cache": self.config["repo_cache"],
                "results": self.config["results"],
                "version": self.config["version"],
            }
            record["provenance"] = {
                "registry_version": (self.root / "registry/VERSION")
                .read_text()
                .strip(),
                "config_hashes": {
                    str(p.relative_to(self.root))
                    if p.is_relative_to(self.root)
                    else str(p): digest(p.read_bytes())
                    for p in (self.config_path, profile_path)
                },
                "dataset_sha256": digest(dataset_path.read_bytes()),
                "sandbox_code_hashes": {
                    name: digest(Path(__file__).with_name(name).read_bytes())
                    for name in (
                        "docker_sandbox.py",
                        "build.py",
                        "policy.py",
                        "evaluation.py",
                        "issue_runner.py",
                        "optimization.py",
                        "cache.py",
                    )
                },
                "effective_config": safe_snapshot(snapshot),
            }
            from .evaluation import (
                DEFAULT_ARTIFACTS,
                grade,
                load_evaluation,
                patch_paths,
                payload,
            )

            evaluation = None
            row = None
            command = profile["test_command"]
            patch = Path(patch_path).read_bytes() if patch_path else None
            if test_mode == "issue" and not prepare:
                artifact_path = resolve_path(
                    self.root, self.config.get("evaluation", DEFAULT_ARTIFACTS)
                )
                row = load_evaluation(artifact_path, task)
                record["evaluation"] = {
                    k: row[k]
                    for k in (
                        "instance_id",
                        "pr_url",
                        "version",
                        "environment_setup_commit",
                    )
                }
                record["evaluation"].update(
                    {
                        "artifact_sha256": digest(artifact_path.read_bytes()),
                        "source_sha256": json.loads(
                            artifact_path.read_text(encoding="utf-8")
                        )["source_sha256"],
                        "test_patch_sha256": digest(row["test_patch"].encode()),
                        "gold_patch_sha256": digest(row["gold_patch"].encode())
                        if validate
                        else None,
                    }
                )
                if patch is not None and patch_paths(patch.decode()) & patch_paths(
                    row["test_patch"]
                ):
                    record["status"] = "protected_test_error"
                    return record
                if stage == "pilot":
                    row = dict(row, PASS_TO_PASS=[])
                evaluation = {
                    "test_patch": row["test_patch"],
                    "selectors": payload(row, profile),
                    "runner_source": Path(__file__)
                    .with_name("issue_runner.py")
                    .read_bytes(),
                }
                evaluation["selectors"]["collect_only"] = preflight
                evaluation["selectors"]["imports"] = (
                    profile.get("preflight_imports", []) if preflight else []
                )
                command = [
                    "python",
                    "/tmp/raac-issue-runner.py",
                    "/tmp/raac-selectors.json",
                ]
            record["patch_sha256"] = digest(patch) if patch is not None else None
            if patch is not None:
                (directory / "candidate.patch").write_bytes(patch)
            from .optimization import evaluation_identity, reusable_record

            record["evaluation_identity"] = evaluation_identity(
                task,
                profile,
                row,
                self.policy.snapshot(),
                record["provenance"]["registry_version"],
                stage,
            )
            if reuse_validated and validate and stage == "full":
                old = reusable_record(
                    resolve_path(self.root, self.config["results"]),
                    record["evaluation_identity"],
                )
                if old:
                    self.connect()
                    try:
                        self.client.images.get(old["image_id"])
                    except docker.errors.ImageNotFound:
                        # A rebuild may resolve unpinned dependencies differently.
                        # Historical evidence remains, but cannot validate that image.
                        old = None
                if old:
                    record.update(
                        {
                            k: old[k]
                            for k in (
                                "baseline",
                                "gold",
                                "baseline_validation",
                                "gold_validation",
                                "image_id",
                                "source_sha256",
                                "image_tag",
                                "dockerfile_sha256",
                            )
                        }
                    )
                    record["reused_from"] = old["result_path"]
                    record["status"] = "validated"
                    record["preparation_seconds"] = 0
                    return record
            phase("source")
            self.connect()
            if getattr(self, "auto_prune", False):
                from .cache import prune

                phase("cache")
                record["cache_retention"] = prune(
                    self, protect_tasks=[task_id], apply=True, workload_locked=True
                )
            with tempfile.TemporaryDirectory() as tmp:
                context = Path(tmp)
                archive_repo(
                    task,
                    resolve_path(self.root, self.config["repo_cache"]),
                    context / "source.tar",
                    versioned=profile.get("source_history") == "versioned",
                    deadline=preparation_deadline,
                )
                source_sha256 = digest((context / "source.tar").read_bytes())
                record["source_sha256"] = source_sha256
                recipe = dockerfile(profile)
                (context / "Dockerfile").write_text(recipe, encoding="utf-8")
                from .optimization import build_identity

                key = build_identity(task, source_sha256, recipe)
                record["build_identity"] = key
                tag = "raac-sandbox:" + key
                try:
                    image = self.client.images.get(tag)
                    record["image_cache_hit"] = True
                except docker.errors.ImageNotFound:
                    from .optimization import compatible_image

                    image = compatible_image(
                        self.client,
                        resolve_path(self.root, self.config["results"]),
                        task,
                        source_sha256,
                        digest(recipe.encode()),
                    )
                    if image is not None:
                        image.tag("raac-sandbox", key)
                        record["image_cache_hit"] = True
                        record["image_adopted"] = True
                    else:
                        remaining_build_time = preparation_deadline - time.monotonic()
                        if remaining_build_time <= 0:
                            raise BuildTimeout(
                                "Preparation budget exhausted before build"
                            )
                        phase("build", directory / "build.log")
                        self.build_cancel = lambda: phase(
                            "build", directory / "build.log"
                        )
                        build_started = time.monotonic()
                        build_image(
                            context,
                            tag,
                            directory / "build.log",
                            remaining_build_time,
                            **(
                                {"progress": self.build_cancel}
                                if progress or getattr(self, "stop_file", None)
                                else {}
                            ),
                        )
                        record["build_seconds"] = round(
                            time.monotonic() - build_started, 3
                        )
                        from .build import build_metrics

                        record["build_metrics"] = build_metrics(directory / "build.log")
                        record["image_cache_hit"] = False
                        image = self.client.images.get(tag)
                record["image_id"] = image.id
                record["dockerfile_sha256"] = digest(recipe.encode())
                record["image_tag"] = tag
            record["preparation_seconds"] = round(time.monotonic() - started, 3)
            if prepare:
                record["status"] = "prepared"
            elif preflight:
                phase("preflight", directory / "preflight.log")
                record["preflight"] = self.execute(
                    image,
                    command,
                    directory,
                    "preflight",
                    profile=profile,
                    evaluation=evaluation,
                )
                found = record["preflight"].get("found_selectors", [])
                record["preflight"]["executed_selectors"] = 0
                record["preflight"]["found_count"] = len(found)
                record["preflight"]["required_selectors"] = len(
                    evaluation["selectors"]["ids"]
                )
                record["status"] = (
                    "preflight_passed"
                    if record["preflight"]["exit_code"] == 0
                    and set(found) == set(evaluation["selectors"]["ids"])
                    else record["preflight"]["status"]
                )
                if record["status"] == "passed":
                    record["status"] = "preflight_failed"
            else:
                phase("baseline", directory / "baseline.log")
                record["status"] = "execution_error"
                record["baseline"] = self.execute(
                    image,
                    command,
                    directory,
                    "baseline",
                    profile=profile,
                    evaluation=evaluation,
                )
                if evaluation is not None:
                    record["baseline_validation"] = grade(
                        row, record["baseline"], baseline=True
                    )
                if validate:
                    phase("gold", directory / "gold.log")
                    record["gold"] = self.execute(
                        image,
                        command,
                        directory,
                        "gold",
                        row["gold_patch"].encode(),
                        profile=profile,
                        evaluation=evaluation,
                    )
                    record["gold_validation"] = grade(row, record["gold"])
                    record["status"] = (
                        ("pilot_passed" if stage == "pilot" else "validated")
                        if record["baseline_validation"]["valid"]
                        and record["gold_validation"]["valid"]
                        else "validation_failed"
                    )
                else:
                    if patch is not None:
                        phase("candidate", directory / "candidate.log")
                        record["candidate"] = self.execute(
                            image,
                            command,
                            directory,
                            "candidate",
                            patch,
                            profile=profile,
                            evaluation=evaluation,
                        )
                        if evaluation is not None:
                            record["candidate_validation"] = grade(
                                row, record["candidate"]
                            )
                    record["status"] = (record["candidate"] or record["baseline"])[
                        "status"
                    ]
                    if evaluation is not None and record["candidate"] is not None:
                        record["status"] = (
                            "resolved"
                            if record["candidate_validation"]["valid"]
                            and record["baseline_validation"]["valid"]
                            else "unresolved"
                        )
            if evaluation is not None:
                for phase in (
                    record.get("baseline"),
                    record.get("gold"),
                    record.get("candidate"),
                ):
                    if phase and phase["status"] not in {"passed", "test_failed"}:
                        record["status"] = phase["status"]
                        break
            return record
        except subprocess.TimeoutExpired as exc:
            record["status"] = (
                "build_timeout"
                if time.monotonic() >= preparation_deadline
                else "preparation_error"
            )
            record["error"] = str(exc)
            return record
        except BuildTimeout as exc:
            record["status"] = "build_timeout"
            record["error"] = str(exc)
            return record
        except KeyboardInterrupt:
            record["status"] = "interrupted"
            raise
        except Exception as exc:  # noqa: BLE001 -- public boundary must report Docker and OS failures
            record["error"] = f"{type(exc).__name__}: {exc}"
            return record
        finally:
            if record.get("status") == "validated" and not record.get("reused_from"):
                record["evidence_hashes"] = {
                    p.name: digest(p.read_bytes())
                    for p in directory.iterdir()
                    if p.name
                    in {
                        "baseline.log",
                        "gold.log",
                        "baseline.tests.json",
                        "gold.tests.json",
                    }
                }
            record["duration_seconds"] = round(time.monotonic() - started, 3)
            record["result_path"] = str(directory / "record.json")
            (directory / "record.json").write_text(
                json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8"
            )
