"""Public snapshots for tracing the demo; .env and credentials are never read here."""

import hashlib
from pathlib import Path
import subprocess
import shutil
import os

import yaml


def collect_provenance(project: Path, registry, config_paths: list[Path], effective: dict) -> dict:
    configs = {}
    for path in config_paths:
        raw = path.read_bytes()
        parsed = yaml.safe_load(raw.decode("utf-8-sig")) or {}
        configs[str(path.relative_to(project)).replace("\\", "/")] = {
            "version": parsed.get("version"), "sha256": hashlib.sha256(raw).hexdigest(),
        }
    git = shutil.which("git")
    native_git = Path(os.getenv("ProgramFiles", "C:/Program Files")) / "Git/cmd/git.exe"
    if native_git.exists():
        git = str(native_git)
    commit = None
    if git:
        try:
            result = subprocess.run([git, "rev-parse", "HEAD"], cwd=project,
                                    capture_output=True, text=True, timeout=5, check=False)
            if result.returncode == 0:
                commit = result.stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            pass
    source_hash = hashlib.sha256()
    for path in sorted((project / "src").rglob("*.py")):
        source_hash.update(str(path.relative_to(project)).encode())
        source_hash.update(path.read_bytes())
    return {"framework_commit": commit, "framework_source_hash": source_hash.hexdigest(),
            "registry_version": registry.version,
            "registry_snapshot": registry.snapshot(), "configs": configs,
            "effective_config": effective}
