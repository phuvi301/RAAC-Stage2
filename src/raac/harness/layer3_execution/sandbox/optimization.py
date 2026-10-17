"""Versioned build/evaluation identities and conservative native rebuild decisions."""

import fnmatch
import hashlib
import json
from pathlib import Path


def signature(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def build_identity(task, source_sha256, recipe):
    # The rendered recipe includes every build-time environment value and command.
    return signature(
        {
            "repo": task.repo,
            "commit": task.base_commit,
            "source": source_sha256,
            "recipe": recipe,
        }
    )


def evaluation_identity(task, profile, row, policy, registry, stage):
    here = Path(__file__).parent
    return signature(
        {
            "schema": 1,
            "task": vars(task),
            "profile": profile,
            "row": row,
            "policy": policy,
            "registry": registry,
            "stage": stage,
            "code": {
                name: hashlib.sha256((here / name).read_bytes()).hexdigest()
                for name in (
                    "docker_sandbox.py",
                    "evaluation.py",
                    "issue_runner.py",
                    "optimization.py",
                    "policy.py",
                    "build.py",
                )
            },
            "template": hashlib.sha256(
                (here.parents[4] / "docker/Dockerfile.sandbox").read_bytes()
            ).hexdigest(),
        }
    )


def needs_native_rebuild(profile, patch):
    if not profile.get("post_patch_commands") or patch is None:
        return False
    from .evaluation import patch_paths

    try:
        paths = patch_paths(patch.decode())
    except (ValueError, UnicodeError):
        return True
    patterns = profile.get("native_rebuild_paths")
    safe = profile.get("native_safe_paths", [])
    if not patterns:
        return True
    # Unknown paths are deliberately rebuilt. Only explicitly reviewed pure-Python
    # paths are eligible for the fast path; generators still override safe patterns.
    return any(
        any(fnmatch.fnmatchcase(p, g) for g in patterns)
        or not any(fnmatch.fnmatchcase(p, g) for g in safe)
        for p in paths
    )


def reusable_record(results, identity):
    for path in sorted(
        results.glob("*/record.json"), key=lambda p: p.stat().st_mtime, reverse=True
    ):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            if (
                record.get("evaluation_identity") != identity
                or record.get("status") != "validated"
                or record.get("stage", "full") != "full"
                or record.get("reused_from")
            ):
                continue
            if not all(
                record.get(p + "_validation", {}).get("valid")
                for p in ("baseline", "gold")
            ):
                continue
            # Require both original reports and logs, not just a summary status.
            if not all(
                (path.parent / (p + suffix)).is_file()
                for p in ("baseline", "gold")
                for suffix in (".log", ".tests.json")
            ):
                continue
            evidence = record.get("evidence_hashes", {})
            expected_names = {
                p + suffix
                for p in ("baseline", "gold")
                for suffix in (".log", ".tests.json")
            }
            if set(evidence) != expected_names or any(
                hashlib.sha256((path.parent / name).read_bytes()).hexdigest()
                != expected
                for name, expected in evidence.items()
            ):
                continue
            return record
        except (OSError, ValueError, TypeError):
            continue
    return None


def compatible_image(client, results, task, source_hash, recipe_hash):
    """Adopt an existing image only with byte-identical source and recipe evidence."""
    import docker

    for path in results.glob("*/record.json"):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            previous = record.get("task", {})
            if (
                previous.get("repo") != task.repo
                or previous.get("base_commit") != task.base_commit
                or record.get("source_sha256") != source_hash
                or record.get("dockerfile_sha256") != recipe_hash
            ):
                continue
            return client.images.get(record["image_id"])
        except (OSError, ValueError, KeyError, docker.errors.ImageNotFound):
            continue
    return None
