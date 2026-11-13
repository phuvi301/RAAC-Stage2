"""What is already installed on this machine. Plain code, read-only: it only runs `--version` style commands."""

from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
from pathlib import Path

# name -> version command; the first number found in the output is the version.
TOOLS = {
    "node": ["node", "--version"],
    "npm": ["npm", "--version"],
    "pnpm": ["pnpm", "--version"],
    "yarn": ["yarn", "--version"],
    "git": ["git", "--version"],
    "java": ["java", "-version"],
    "go": ["go", "version"],
    "dotnet": ["dotnet", "--version"],
    "docker": ["docker", "--version"],
    "rustc": ["rustc", "--version"],
    "php": ["php", "--version"],
}
VERSION = re.compile(r"(\d+(?:\.\d+){0,3})")


def _run(command: list[str], timeout: float = 15.0) -> str | None:
    executable = shutil.which(command[0])
    if executable is None:
        return None
    try:
        completed = subprocess.run([executable, *command[1:]], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   text=True, errors="replace", timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    return completed.stdout if completed.returncode == 0 or completed.stdout else None


def pythons(run=_run) -> list[dict]:
    """Every Python interpreter the launcher or PATH knows, with a flag for registrations that point nowhere."""
    found: dict[str, dict] = {}
    listing = run(["py", "-0p"]) or ""
    for line in listing.splitlines():
        match = re.match(r"\s*-V:(\S+?)(?:\[-\d+\])?\s+\*?\s*(\S.*\.exe)\s*$", line)
        if match:
            tag, path = match.group(1), match.group(2).strip()
            found[path.lower()] = {"version": tag, "path": path, "exists": Path(path).is_file()}
    for name in ("python", "python3"):
        executable = shutil.which(name)
        if executable and executable.lower() not in found and "windowsapps" not in executable.lower():
            text = run([executable, "--version"]) or ""
            match = VERSION.search(text)
            if match:
                found[executable.lower()] = {"version": match.group(1), "path": executable, "exists": True}
    return sorted(found.values(), key=lambda item: (item["exists"], version_key(item["version"])), reverse=True)


def version_key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in re.findall(r"\d+", version))


def probe(run=_run) -> dict:
    """A snapshot of the runtimes and tools on this machine."""
    tools = {}
    for name, command in TOOLS.items():
        text = run(command)
        match = VERSION.search(text or "")
        if text is not None and match:
            tools[name] = {"version": match.group(1), "path": shutil.which(command[0])}
    return {
        "os": f"{platform.system()} {platform.release()}",
        "shell": "PowerShell" if os.name == "nt" else os.environ.get("SHELL", "sh"),
        "python": pythons(run),
        "tools": tools,
        "installers": [name for name in ("winget", "py", "nvm", "choco", "scoop", "brew", "apt-get", "uv")
                       if shutil.which(name)],
    }
