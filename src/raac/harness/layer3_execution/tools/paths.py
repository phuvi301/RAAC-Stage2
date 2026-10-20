"""Repository-relative paths shared by file tools, patches and local commands."""

from pathlib import PurePosixPath, PureWindowsPath


EXCLUDED_DIRS = {".git", ".hg", ".svn", ".venv", "venv", "node_modules", "__pycache__",
                 ".pytest_cache", ".mypy_cache", ".ruff_cache", ".idea"}
SECRET_FILES = {".npmrc", ".pypirc", ".netrc", "id_rsa", "id_ed25519"}
WINDOWS_DEVICES = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)),
                   *(f"lpt{i}" for i in range(1, 10))}


def repo_path(name: str, *, allow_root=False) -> str:
    if not isinstance(name, str) or not name or any(ord(char) < 32 for char in name):
        raise ValueError("Invalid repository path")
    name = name.replace("\\", "/")
    if allow_root and name == ".":
        return name
    parts = name.split("/")
    if (PurePosixPath(name).is_absolute() or PureWindowsPath(name).drive
            or any(part in {"", ".", ".."} or ":" in part or part.rstrip(". ") != part
                   or part.split(".")[0].lower() in WINDOWS_DEVICES for part in parts)):
        raise ValueError("Use a relative path inside repo")
    if any(part.lower() in EXCLUDED_DIRS | SECRET_FILES or part.lower() == ".env" or part.lower().startswith(".env.")
           or part.lower().endswith((".pem", ".key")) for part in parts):
        raise ValueError("Credential or excluded path")
    return "/".join(parts)


def test_path(name: str) -> bool:
    path = PurePosixPath(name)
    return (any(part in {"test", "tests", "__tests__", "spec", "specs"} for part in path.parts[:-1])
            or path.name.startswith("test_") or path.stem.endswith("_test")
            or ".test." in path.name or ".spec." in path.name)
