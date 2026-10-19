"""Bounded Docker builds with an exclusively owned persistent builder."""

import os
import re
import subprocess
import time
from pathlib import Path


class BuildTimeout(TimeoutError):
    """The build exceeded its wall-clock budget."""


def build_metrics(log):
    """Parse BuildKit's measured steps; overlapping steps are not wall-clock totals."""
    labels, steps = {}, []
    for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
        match = re.match(r"#(\d+) (.*)", line)
        if not match:
            continue
        number, text = match.groups()
        if text.startswith(("[", "exporting", "importing", "sending tarball")):
            labels.setdefault(number, text)
        done = re.match(r"DONE ([\d.]+)s", text)
        if done:
            steps.append(
                {
                    "step": number,
                    "label": labels.get(number, "unknown"),
                    "seconds": float(done[1]),
                }
            )
    return {
        "steps": steps,
        "note": "BuildKit steps may overlap; do not sum as total build time.",
    }


def _build_image(context: Path, tag: str, log: Path, timeout: int, progress=None):
    # A private builder lets cancellation stop daemon-side work as well as the CLI.
    builder = "raac-sandbox-managed-v1"
    process = None
    completed = False
    deadline = time.monotonic() + timeout
    environment = os.environ.copy()
    # BuildKit must not inject host proxy credentials into build RUN instructions.
    for key in list(environment):
        if "proxy" in key.lower():
            environment.pop(key)
    try:
        with log.open("wb") as output:
            output.write((builder + "\n").encode())
            output.flush()
            exists = subprocess.run(
                ["docker", "buildx", "inspect", builder],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=max(1, deadline - time.monotonic()),
                env=environment,
            )
            if exists.returncode != 0:
                subprocess.run(
                    [
                        "docker",
                        "buildx",
                        "create",
                        "--name",
                        builder,
                        "--driver",
                        "docker-container",
                    ],
                    stdout=output,
                    stderr=subprocess.STDOUT,
                    check=True,
                    timeout=max(1, deadline - time.monotonic()),
                    env=environment,
                )
            process = subprocess.Popen(
                [
                    "docker",
                    "buildx",
                    "build",
                    "--builder",
                    builder,
                    "--load",
                    "--progress=plain",
                    "--provenance=false",
                    "--build-arg",
                    "HTTP_PROXY=",
                    "--build-arg",
                    "HTTPS_PROXY=",
                    "--build-arg",
                    "ALL_PROXY=",
                    "--build-arg",
                    "http_proxy=",
                    "--build-arg",
                    "https_proxy=",
                    "--build-arg",
                    "all_proxy=",
                    "--tag",
                    tag,
                    str(context),
                ],
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=subprocess.STDOUT,
                env=environment,
            )
            last_progress = 0
            while process.poll() is None:
                if progress and time.monotonic() - last_progress >= 10:
                    progress()
                    last_progress = time.monotonic()
                if time.monotonic() >= deadline:
                    raise BuildTimeout(f"Build exceeded {timeout}s; see {log}")
                time.sleep(0.2)
            if process.returncode:
                raise RuntimeError(
                    f"Docker build exited {process.returncode}; see {log}"
                )
            completed = True
    except subprocess.TimeoutExpired as exc:
        raise BuildTimeout(f"Build exceeded {timeout}s; see {log}") from exc
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        # rm stops/removes this builder's container and state; other builds are untouched.
        if not completed:
            subprocess.run(
                ["docker", "buildx", "rm", "--force", builder],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=60,
            )


def build_image(context: Path, tag: str, log: Path, timeout: int, progress=None):
    from .docker_sandbox import ROOT, cache_lock

    started = time.monotonic()
    try:
        with cache_lock(
            ROOT / "data/build-cache.lock", timeout=timeout, on_wait=progress
        ):
            remaining = timeout - (time.monotonic() - started)
            if remaining <= 0:
                raise BuildTimeout(
                    "Build budget exhausted waiting for build cache lock"
                )
            return _build_image(context, tag, log, remaining, progress=progress)
    except TimeoutError as exc:
        if isinstance(exc, BuildTimeout):
            raise
        raise BuildTimeout(
            "Build budget exhausted waiting for build cache lock"
        ) from exc
