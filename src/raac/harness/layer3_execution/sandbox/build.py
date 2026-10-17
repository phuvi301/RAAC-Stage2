"""Bounded Docker builds in disposable, independently cancellable builders."""

import os
import subprocess
import time
import uuid
from pathlib import Path


class BuildTimeout(TimeoutError):
    """The build exceeded its wall-clock budget."""


def _build_image(context: Path, tag: str, log: Path, timeout: int):
    # A private builder lets cancellation stop daemon-side work as well as the CLI.
    builder = "raac-" + uuid.uuid4().hex
    process = None
    cache = Path(__file__).resolve().parents[5] / "data/build-cache"
    cache.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout
    environment = os.environ.copy()
    # BuildKit must not inject host proxy credentials into build RUN instructions.
    for key in list(environment):
        if "proxy" in key.lower():
            environment.pop(key)
    try:
        with log.open("wb") as output:
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
                    "--cache-from",
                    "type=local,src=" + str(cache),
                    "--cache-to",
                    "type=local,dest=" + str(cache) + ",mode=max",
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
            while process.poll() is None:
                if time.monotonic() >= deadline:
                    raise BuildTimeout(f"Build exceeded {timeout}s; see {log}")
                time.sleep(0.2)
            if process.returncode:
                raise RuntimeError(
                    f"Docker build exited {process.returncode}; see {log}"
                )
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
        subprocess.run(
            ["docker", "buildx", "rm", "--force", builder],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=60,
        )


def build_image(context: Path, tag: str, log: Path, timeout: int):
    from .docker_sandbox import ROOT, cache_lock

    started = time.monotonic()
    with cache_lock(ROOT / "data/build-cache.lock", timeout=timeout):
        remaining = timeout - (time.monotonic() - started)
        if remaining <= 0:
            raise BuildTimeout("Build budget exhausted waiting for build cache lock")
        return _build_image(context, tag, log, remaining)
