"""Snapshot public configuration and Registry; never load .env or overwrite a freeze."""

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import yaml

from raac.common.tracing import JSONLTrace
from raac.harness.layer3_execution.sandbox.docker_sandbox import safe_snapshot
from raac.registry import load_registry


def freeze(root: Path, output: Path) -> None:
    root = root.resolve()
    registry = load_registry(root / "registry")
    paths = [root / "pyproject.toml", root / "requirements.lock", root / "registry/VERSION"]
    for directory, suffixes in (("configs", {".yaml", ".yml"}),
                                ("registry", {".yaml", ".yml", ".md"})):
        paths.extend(sorted(p for p in (root / directory).rglob("*") if p.suffix in suffixes))
    files = {}
    for path in paths:
        if not path.resolve().is_relative_to(root):
            raise ValueError("Snapshot input is outside the project")
        raw = path.read_bytes()
        text = raw.decode("utf-8-sig")
        files[path.relative_to(root).as_posix()] = {
            "sha256": hashlib.sha256(raw).hexdigest(),
            "snapshot": yaml.safe_load(text) if path.suffix in {".yaml", ".yml"} else text,
        }
    data = {"format_version": 1, "created_at": datetime.now(UTC).isoformat(),
            "registry_version": registry.version, "files": files}
    data = JSONLTrace(output).redact(safe_snapshot(data))
    payload = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as stream:
        stream.write(payload)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    output = args.output or root / "results/freezes" / (
        datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8] + ".json"
    )
    try:
        freeze(root, output)
    except (OSError, ValueError, yaml.YAMLError):
        print("Freeze failed: check inputs and choose an output that does not exist.", file=sys.stderr)
        return 1
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
