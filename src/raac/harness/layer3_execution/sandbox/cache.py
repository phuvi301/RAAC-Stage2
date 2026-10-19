"""Sandbox-owned cache accounting and bounded, non-force retention."""

import json
import math
import re
import shutil
import subprocess

from .docker_sandbox import ROOT, cache_lock, resolve_path

BUILDER = "raac-sandbox-managed-v1"
GIB = 1024**3


def size_bytes(value):
    if isinstance(value, int):
        return value
    match = re.fullmatch(r"([\d.]+)\s*(B|kB|MB|GB|TB|KiB|MiB|GiB)", str(value))
    if not match:
        raise ValueError("Unknown Buildx size: " + str(value))
    number, unit = match.groups()
    scale = {
        "B": 1,
        "kB": 1000,
        "MB": 1000**2,
        "GB": 1000**3,
        "TB": 1000**4,
        "KiB": 1024,
        "MiB": 1024**2,
        "GiB": 1024**3,
    }[unit]
    precision = len(number.split(".")[1]) if "." in number else 0
    # Buildx emits rounded human sizes, so use one display quantum as headroom.
    return math.ceil((float(number) + 10 ** (-precision)) * scale)


def tree_bytes(path):
    return (
        sum(p.stat().st_size for p in path.rglob("*") if p.is_file())
        if path.exists()
        else 0
    )


def inventory(sandbox, protect_tasks=()):
    sandbox.connect()
    protected = {c["ImageID"] for c in sandbox.client.api.containers(all=True)}
    touched = {}
    results = resolve_path(sandbox.root, sandbox.config["results"])
    for path in results.glob("*/record.json"):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            image = record.get("image_id")
            touched[image] = max(touched.get(image, 0), path.stat().st_mtime)
            if record.get("task_id") in protect_tasks:
                protected.add(image)
        except (OSError, ValueError):
            continue
    disk = sandbox.client.df()
    image_sizes = {i["Id"]: i for i in disk.get("Images", [])}
    images = []
    for image in sandbox.client.api.images():
        tags = image.get("RepoTags") or []
        image_id = image["Id"]
        if not tags or not all(t.startswith("raac-sandbox:") for t in tags):
            continue
        images.append(
            {
                "id": image_id,
                "tags": tags,
                "bytes_upper_bound": image_sizes.get(image_id, {}).get(
                    "Size", image.get("Size", 0)
                ),
                "last_used": touched.get(image_id, 0),
                "protected": image_id in protected,
            }
        )
    # Buildx machine-readable output is version dependent. Never treat an error as zero.
    du = subprocess.run(
        ["docker", "buildx", "du", "--builder", BUILDER, "--format", "json"],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    build_bytes = None
    if du.returncode == 0:
        try:
            entries = [
                json.loads(line) for line in du.stdout.splitlines() if line.strip()
            ]
            build_bytes = sum(size_bytes(e["Size"]) for e in entries)
        except (ValueError, TypeError, AttributeError):
            pass
    git_bytes = tree_bytes(resolve_path(sandbox.root, sandbox.config["repo_cache"]))
    legacy_bytes = tree_bytes(sandbox.root / "data/build-cache")
    image_upper = min(sum(i["bytes_upper_bound"] for i in images), disk["LayersSize"])
    upper = git_bytes + legacy_bytes + image_upper
    return {
        "status": "ready",
        "git_bytes": git_bytes,
        "legacy_build_bytes": legacy_bytes,
        "managed_build_bytes": build_bytes,
        "managed_build_bytes_estimated": True,
        "image_bytes_upper_bound": image_upper,
        "images": images,
        "total_bytes_upper_bound": upper + (build_bytes or 0),
        "accounting_complete": build_bytes is not None,
        "limitations": "Image sizes include shared layers; total is conservative. Git cache and active images are retained. Unknown builder usage is reported, not assumed zero.",
    }


def prune(sandbox, max_gib=30, apply=False, protect_tasks=(), workload_locked=False):
    if max_gib <= 0:
        raise ValueError("max-gib must be positive")
    if apply and not workload_locked:
        with cache_lock(sandbox.root / "data/sandbox-run.lock", timeout=0):
            return prune(sandbox, max_gib, True, protect_tasks, True)
    with cache_lock(ROOT / "data/build-cache.lock", timeout=0):
        before = inventory(sandbox, protect_tasks)
        limit = int(max_gib * GIB)
        estimated = before["total_bytes_upper_bound"]
        actions, errors = [], []
        if estimated > limit and before["legacy_build_bytes"]:
            path = sandbox.root / "data/build-cache"
            actions.append({"kind": "legacy_build_cache", "path": str(path)})
            if apply:
                # Resolve and verify the exact owned directory before recursive removal.
                if (
                    path.resolve() != sandbox.root.resolve() / "data/build-cache"
                    or path.is_symlink()
                ):
                    raise ValueError("Unsafe legacy cache path")
                shutil.rmtree(path)
            estimated -= before["legacy_build_bytes"]
        for image in sorted(before["images"], key=lambda i: i["last_used"]):
            if estimated <= limit:
                break
            if image["protected"]:
                continue
            actions.append({"kind": "image", "id": image["id"]})
            if apply:
                try:
                    # Remove owned tags only; never force removal of an image in use.
                    for tag in image["tags"]:
                        sandbox.client.images.remove(tag, force=False)
                except Exception as exc:  # noqa: BLE001 -- retain failures in cleanup report
                    errors.append(str(exc))
                    continue
            if apply:
                estimated = inventory(sandbox, protect_tasks)["total_bytes_upper_bound"]
            else:
                estimated -= image["bytes_upper_bound"]
        if estimated > limit and before["managed_build_bytes"]:
            keep = max(0, limit - (estimated - before["managed_build_bytes"]))
            command = [
                "docker",
                "buildx",
                "prune",
                "--builder",
                BUILDER,
                "--force",
                "--max-used-space",
                str(keep),
            ]
            actions.append({"kind": "builder_prune", "keep_bytes": keep})
            if apply:
                result = subprocess.run(
                    command, check=False, capture_output=True, text=True, timeout=60
                )
                if result.returncode:
                    errors.append(result.stderr)
        after = inventory(sandbox, protect_tasks) if apply else None
        return {
            "status": "ready",
            "dry_run": not apply,
            "max_gib": max_gib,
            "before": before,
            "actions": actions,
            "errors": errors,
            "after": after,
            "over_budget": (after or before)["total_bytes_upper_bound"] > limit,
        }
