"""Deterministic project requirement detection, gap checks and local install plans.

No LLM is involved and repository code is never executed: manifests are parsed
statically; only toolchain binaries (python/java/node/...) are probed for versions.
Installs stay project-local (a .venv, node_modules, the build tool's own cache).
A missing language runtime is reported with an exact hint, or fetched through
`uv python install` (user-local) when uv exists.
"""

import asyncio
import configparser
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

from packaging.requirements import InvalidRequirement, Requirement
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version

from .tools.paths import excluded_directory

try:
    import tomllib
except ImportError:  # pragma: no cover - MAS itself runs on a recent Python
    tomllib = None

MAX_DEPTH = 3
TEST_EXTRAS = {"test", "tests", "testing", "dev"}
IS_WINDOWS = os.name == "nt"

MARKERS = {
    "python": ("pyproject.toml", "setup.py", "setup.cfg", "tox.ini", "Pipfile"),
    "node": ("package.json",),
    "maven": ("pom.xml",),
    "gradle": ("build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts"),
    "go": ("go.mod",),
    "rust": ("Cargo.toml",),
}
# requirements*.txt is a Python marker only when matched by pattern.
REQUIREMENTS_FILE = re.compile(r"requirements([-_.][\w.-]+)?\.txt", re.I)


def _run(argv, *, timeout=20, cwd=None):
    """Probe a toolchain binary; return (exit_code, stdout+stderr) or (None, error)."""
    try:
        done = subprocess.run(argv, capture_output=True, text=True, errors="replace",
                              timeout=timeout, cwd=cwd, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError) as error:
        return None, type(error).__name__
    return done.returncode, (done.stdout + "\n" + done.stderr).strip()


def is_venv_name(name):
    return bool(re.fullmatch(r"\.?venv[-_]?\d*", name, re.I))


def python_in(venv_dir):
    for sub in ("Scripts/python.exe", "bin/python", "python.exe"):
        candidate = Path(venv_dir) / sub
        if candidate.is_file():
            return candidate
    return None


# --------------------------------------------------------------------------- detect

def detect_projects(root, cwd="."):
    """Find project directories by marker files, at most MAX_DEPTH levels below cwd."""
    root, base = Path(root).resolve(), (Path(root) / cwd).resolve()
    found = []
    for directory, dirs, files in os.walk(base):
        depth = len(Path(directory).relative_to(base).parts)
        dirs[:] = sorted(name for name in dirs if not excluded_directory(name)) if depth < MAX_DEPTH else []
        names = set(files)
        for ecosystem, markers in MARKERS.items():
            hits = [name for name in markers if name in names]
            if ecosystem == "python":
                hits += sorted(name for name in names if REQUIREMENTS_FILE.fullmatch(name))
            if hits:
                relative = Path(directory).relative_to(root).as_posix()
                found.append({"ecosystem": ecosystem, "directory": relative, "markers": hits})
    # Maven/Gradle/Node pom-style roots hide nested modules; keep the shallowest per ecosystem chain.
    return found


# --------------------------------------------------------------------------- python

def _load_toml(path):
    if tomllib is None or not path.is_file():
        return {}
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _requirement_lines(path):
    lines = []
    try:
        for raw in path.read_text(encoding="utf-8-sig").splitlines():
            line = raw.split(" #")[0].strip()
            if line and not line.startswith(("#", "-", "git+", "http://", "https://", ".")):
                lines.append(line)
    except OSError:
        pass
    return lines


def _setup_cfg(path):
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read(path, encoding="utf-8")
    except (OSError, configparser.Error):
        return {}
    def lines(section, key):
        return [item.strip() for item in parser.get(section, key, fallback="").splitlines()
                if item.strip() and not item.strip().startswith("#")]
    extras = {name: lines("options.extras_require", name) for name in parser.options("options.extras_require")} \
        if parser.has_section("options.extras_require") else {}
    return {"name": parser.get("metadata", "name", fallback=None),
            "python_requires": parser.get("options", "python_requires", fallback=None),
            "install_requires": lines("options", "install_requires"),
            "tests_require": lines("options", "tests_require"), "extras": extras,
            "pytest": parser.has_section("tool:pytest")}


def python_requirements(directory):
    """Static requirements: python_requires/supported minors, dependency strings, test runner."""
    directory = Path(directory)
    pyproject = _load_toml(directory / "pyproject.toml")
    project = pyproject.get("project", {})
    cfg = _setup_cfg(directory / "setup.cfg") if (directory / "setup.cfg").is_file() else {}
    requires_python = project.get("requires-python") or cfg.get("python_requires")
    setup_py = directory / "setup.py"
    setup_text = setup_py.read_text(encoding="utf-8", errors="replace") if setup_py.is_file() else ""
    if not requires_python:
        found = re.search(r"python_requires\s*=\s*['\"]([^'\"]+)['\"]", setup_text)
        requires_python = found.group(1) if found else None
    name = project.get("name") or cfg.get("name")
    if not name:
        found = re.search(r"\bname\s*=\s*['\"]([\w.-]+)['\"]", setup_text)
        name = found.group(1) if found else None
    # Supported minors from classifiers, setup.py text and tox env names.
    text = setup_text + "\n" + json.dumps(project.get("classifiers", []))
    cfg_path = directory / "setup.cfg"
    if cfg_path.is_file():
        text += "\n" + cfg_path.read_text(encoding="utf-8", errors="replace")
    minors = {int(m) for m in re.findall(r"Programming Language :: Python :: 3\.(\d+)", text)}
    tox = directory / "tox.ini"
    if tox.is_file():
        tox_text = tox.read_text(encoding="utf-8", errors="replace")
        minors |= {int(m) for m in re.findall(r"\bpy3(\d+)\b", tox_text)}
    version_file = directory / ".python-version"
    pinned = version_file.read_text(encoding="utf-8").strip() if version_file.is_file() else None
    dependencies, unparsed = [], []
    sources = list(project.get("dependencies", [])) + list(cfg.get("install_requires", []))
    for extra in TEST_EXTRAS:
        sources += project.get("optional-dependencies", {}).get(extra, [])
        sources += cfg.get("extras", {}).get(extra, [])
    sources += cfg.get("tests_require", [])
    for path in sorted(directory.glob("requirements*.txt")):
        if REQUIREMENTS_FILE.fullmatch(path.name):
            sources += _requirement_lines(path)
    for text_line in sources:
        try:
            dependencies.append(str(Requirement(text_line)))
        except InvalidRequirement:
            unparsed.append(text_line)
    uses_pytest = (bool(cfg.get("pytest")) or (directory / "pytest.ini").is_file()
                   or (directory / "conftest.py").is_file() or "pytest" in pyproject.get("tool", {})
                   or any(canonicalize_name(Requirement(d).name) in {"pytest", "pytest-cov"} for d in dependencies))
    installable = bool(project.get("name") or cfg.get("name") or setup_py.is_file())
    build_requires = []
    for text_line in pyproject.get("build-system", {}).get("requires", []):
        try:
            build_requires.append(str(Requirement(text_line)))
        except InvalidRequirement:
            pass
    return {"name": name, "build_requires": build_requires, "legacy_setup_py": setup_py.is_file(),
            "requires_python": requires_python, "supported_minors": sorted(minors),
            "pinned": pinned, "dependencies": sorted(set(dependencies)), "unparsed": unparsed,
            "uses_pytest": uses_pytest, "installable": installable}


def probe_python(path):
    """Verify an interpreter actually starts; returns its version tuple or None."""
    code, output = _run([str(path), "-I", "-c", "import sys;print('.'.join(map(str,sys.version_info[:3])))"])
    if code != 0:
        return None
    try:
        return tuple(int(x) for x in output.splitlines()[-1].split("."))
    except ValueError:
        return None


def interpreter_candidates(root, *, extra=()):
    """Interpreters that really start. Dead launcher entries (e.g. removed 3.8) are dropped."""
    paths = [(Path(sys.executable), "mas")]
    paths += [(Path(p), "extra") for p in extra]
    for minor in range(6, 16):
        found = shutil.which(f"python3.{minor}")
        if found:
            paths.append((Path(found), "path"))
    if IS_WINDOWS:
        _, listing = _run(["py", "-0p"])
        for line in listing.splitlines():
            found = re.search(r"([A-Za-z]:\\.*python[\w.]*\.exe)\s*$", line.strip(), re.I)
            if found:
                paths.append((Path(found.group(1)), "py-launcher"))
    if shutil.which("uv"):
        code, listing = _run(["uv", "python", "list", "--only-installed", "--output-format", "json"])
        if code == 0:
            try:
                paths += [(Path(item["path"]), "uv") for item in json.loads(listing) if item.get("path")]
            except (ValueError, TypeError, KeyError):
                pass
    for directory, dirs, _ in os.walk(root):
        depth = len(Path(directory).relative_to(root).parts)
        keep = []
        for name in dirs:
            if is_venv_name(name):
                executable = python_in(Path(directory) / name)
                if executable:
                    paths.append((executable, "project-venv"))
            elif not excluded_directory(name) and depth < MAX_DEPTH:
                keep.append(name)
        dirs[:] = keep
    seen, result = set(), []
    for path, source in paths:
        if not path.is_file():
            continue
        key = os.path.normcase(os.path.realpath(path))
        if key in seen:
            continue
        seen.add(key)
        version = probe_python(path)
        if version and any(r["version"] == ".".join(map(str, version)) and Path(r["path"]).parent == path.absolute().parent
                           for r in result):
            continue
        if version:
            result.append({"path": str(path.absolute()), "version": ".".join(map(str, version)), "source": source})
    return result


def choose_python(requirements, candidates):
    """Pick the highest interpreter inside python_requires and the declared support window."""
    spec = None
    if requirements["requires_python"]:
        try:
            spec = SpecifierSet(requirements["requires_python"])
        except InvalidSpecifier:
            spec = None
    allowed = allowed_minors(requirements)
    usable = []
    for item in candidates:
        version = tuple(int(x) for x in item["version"].split("."))
        if version[0] != 3:
            continue
        if spec is not None and not spec.contains(Version(item["version"]), prereleases=True):
            continue
        if allowed and version[1] not in allowed:
            continue
        usable.append((version, item))
    # Highest minor wins; an existing project venv beats a base interpreter of the same minor.
    usable.sort(key=lambda pair: (pair[0][:2], pair[1]["source"] == "project-venv", pair[0]), reverse=True)
    return usable[0][1] if usable else None


def allowed_minors(requirements):
    """Declared minors plus two newer ones: classifiers lag behind what old code still runs on."""
    pinned = re.match(r"3\.(\d+)", requirements["pinned"] or "")
    if pinned:
        return [int(pinned.group(1))]
    declared = requirements["supported_minors"]
    return list(range(min(declared), max(declared) + 3)) if declared else []


def wanted_minors(requirements):
    """Minor to install when nothing fits: oldest allowed minor uv can fetch (3.8+), else the oldest allowed."""
    allowed = allowed_minors(requirements)
    spec = SpecifierSet(requirements["requires_python"]) if requirements["requires_python"] else None
    fits = [m for m in allowed if spec is None or spec.contains(Version(f"3.{m}.0"), prereleases=True)]
    fetchable = [m for m in fits if m >= 8] or fits
    return [f"3.{fetchable[0]}"] if fetchable else []


_INSTALLED_PROBE = r"""
import json, sys, platform
try:
    from importlib import metadata
except ImportError:
    metadata = None
names = json.loads(sys.argv[1])
found = {}
for name in names:
    try:
        found[name] = metadata.version(name) if metadata else None
    except Exception:
        found[name] = None
env = {"python_version": ".".join(map(str, sys.version_info[:2])),
       "python_full_version": platform.python_version(), "os_name": __import__("os").name,
       "sys_platform": sys.platform, "platform_machine": platform.machine(),
       "platform_system": platform.system(), "platform_python_implementation": platform.python_implementation(),
       "implementation_name": sys.implementation.name}
print(json.dumps({"installed": found, "env": env}))
"""


def missing_packages(python, requirements, *, project_name=None):
    """Compare declared dependencies with what the interpreter can import metadata for."""
    parsed = [Requirement(item) for item in requirements["dependencies"]]
    names = sorted({canonicalize_name(r.name) for r in parsed})
    if project_name:
        names.append(canonicalize_name(project_name))
    code, output = _run([str(python), "-I", "-c", _INSTALLED_PROBE, json.dumps(names)], timeout=60)
    if code != 0:
        return {"checked": False, "missing": [str(r) for r in parsed], "mismatched": [], "project_installed": False}
    data = json.loads(output.splitlines()[-1])
    installed, marker_env = data["installed"], data["env"]
    missing, mismatched = [], []
    for requirement in parsed:
        if requirement.marker is not None and not requirement.marker.evaluate({**marker_env, "extra": ""}):
            continue
        version = installed.get(canonicalize_name(requirement.name))
        if version is None:
            missing.append(str(requirement))
            continue
        try:
            ok = requirement.specifier.contains(Version(version), prereleases=True)
        except InvalidVersion:
            ok = True
        if not ok:
            mismatched.append({"requirement": str(requirement), "installed": version})
    return {"checked": True, "missing": missing, "mismatched": mismatched,
            "project_installed": bool(project_name and installed.get(canonicalize_name(project_name)))}


def inspect_python(root, directory, *, python_executable=None):
    root, folder = Path(root).resolve(), (Path(root) / directory).resolve()
    requirements = python_requirements(folder)
    candidates = interpreter_candidates(folder, extra=[python_executable] if python_executable else [])
    if python_executable:
        wanted_path = os.path.normcase(str(Path(python_executable).absolute()))
        chosen = next((c for c in candidates if os.path.normcase(c["path"]) == wanted_path), None)
    else:
        chosen = choose_python(requirements, candidates)
    blocking, warnings, steps = [], [], []
    if chosen is None:
        wanted = wanted_minors(requirements)
        declared = ", ".join("3." + str(m) for m in requirements["supported_minors"])
        blocking.append(f"No installed interpreter satisfies the project (requires-python {requirements['requires_python'] or 'unspecified'}"
                        f"{'; declared support ' + declared if declared else ''}). "
                        f"Found: {', '.join(c['version'] for c in candidates) or 'none'}.")
        fix = python_installer(wanted[0]) if wanted else None
        if wanted and fix is None:
            blocking.append(f"Install Python {wanted[0]} (for example `uv python install {wanted[0]}`) and run again.")
        return {"ecosystem": "python", "directory": directory, "requirements": requirements,
                "interpreters": candidates, "selected_python": None, "blocking": blocking, "warnings": warnings,
                "dependencies": {"checked": False, "missing": [], "mismatched": []},
                "plan": [], "toolchain_fix": fix, "test_command": None}
    if chosen["source"] == "project-venv":
        py, venv_name = chosen["path"], None
    else:
        # Keep a working .venv of another version; a versioned name (.venv38) is the repo's convention.
        major_minor = "".join(chosen["version"].split(".")[:2])
        venv_name = ".venv" if not (folder / ".venv").exists() else f".venv{major_minor}"
        existing = python_in(folder / venv_name)
        py = str(existing) if existing else str(folder / venv_name / ("Scripts/python.exe" if IS_WINDOWS else "bin/python"))
        if existing is None:
            steps.append({"argv": [chosen["path"], "-m", "venv", venv_name], "cwd": directory,
                          "purpose": f"Create project-local virtualenv {venv_name}"})
    usable = venv_name is None or python_in(folder / venv_name) is not None
    if usable:
        report = missing_packages(py, requirements, project_name=requirements["name"])
    else:
        report = {"checked": False, "missing": list(requirements["dependencies"]), "mismatched": [], "project_installed": False}
    needed = list(report["missing"]) + [m["requirement"] for m in report["mismatched"]]
    if requirements["uses_pytest"] and "pytest" not in {canonicalize_name(Requirement(r).name) for r in needed}:
        if not usable or not _has(py, "pytest"):
            needed.append("pytest")
    if needed:
        steps.append({"argv": [py, "-m", "pip", "install", *sorted(set(needed))], "cwd": directory,
                      "purpose": "Install declared dependencies that are missing"})
    if requirements["installable"] and not report["project_installed"]:
        steps.append({"argv": [py, "-m", "pip", "install", "-e", "."], "cwd": directory,
                      "purpose": "Install the project itself in editable mode",
                      "on_failure": legacy_build_recovery(py, directory, requirements)})
    warnings += [f"Skipped unparsable requirement: {item}" for item in requirements["unparsed"]]
    return {"ecosystem": "python", "directory": directory, "requirements": requirements,
            "interpreters": candidates, "selected_python": chosen, "venv_python": py,
            "dependencies": report, "blocking": blocking, "warnings": warnings, "plan": steps,
            "toolchain_fix": None,
            "test_command": [py, "-m", "pytest"] if requirements["uses_pytest"] else [py, "-m", "unittest", "discover"]}


def legacy_build_recovery(py, directory, requirements):
    """Fallback for setup.py-era projects whose isolated build breaks on today's setuptools/pip.

    Install the project's own declared build requirements next to a pre-60 setuptools, then build
    against that environment instead of an isolated one.
    """
    build = []
    for item in requirements.get("build_requires", []):
        name = canonicalize_name(Requirement(item).name)
        if name == "setuptools_scm" or name == "setuptools-scm":
            item = f"{Requirement(item).name}<7"  # newer setuptools_scm breaks the pre-60 setuptools toolchain
        if name not in {"setuptools", "wheel"}:
            build.append(item)
    return [{"argv": [py, "-m", "pip", "install", "setuptools<60", "wheel", *build], "cwd": directory,
             "purpose": "Recovery: install declared build requirements with setuptools<60"},
            {"argv": [py, "-m", "pip", "install", "--no-build-isolation", "--no-deps", "-e", "."], "cwd": directory,
             "purpose": "Recovery: editable install without build isolation"}]


_ERROR_LINE = re.compile(r"error|no module named|not found|failed|cannot|unable|traceback|exception", re.I)


def error_lines(stdout, stderr, limit=12):
    """The informative lines of a failed install; pip's own tail only says 'check the logs'."""
    lines = [line.strip() for line in (stdout + "\n" + stderr).splitlines() if _ERROR_LINE.search(line)]
    lines = [line for line in lines if "You should consider upgrading" not in line]
    return lines[-limit:]


def python_installer(version):
    """A user-local way to fetch a Python runtime: uv, or the Windows Python install manager."""
    if shutil.which("uv"):
        return {"argv": ["uv", "python", "install", version], "purpose": "Install a compatible Python (user-local, managed by uv)"}
    if IS_WINDOWS and shutil.which("py"):
        code, output = _run(["py", "help", "install"], timeout=60)
        if code == 0 and "--yes" in output:
            return {"argv": ["py", "install", "--yes", version],
                    "purpose": "Install a compatible Python with the Windows Python install manager"}
    return None


def _has(python, module):
    code, _ = _run([python, "-I", "-c", f"import {module}"], timeout=30)
    return code == 0


# ------------------------------------------------------------------- other ecosystems

def _version_of(argv, pattern=r"(\d+(?:\.\d+)*)"):
    code, output = _run(argv)
    if code is None or (code != 0 and not output):
        return None
    found = re.search(pattern, output)
    return found.group(1) if found else None


def _tool(name, root=None, wrappers=()):
    for wrapper in wrappers:
        path = Path(root) / wrapper
        if path.is_file():
            return str(path)
    return shutil.which(name)


def inspect_node(root, directory):
    folder = Path(root) / directory
    package = {}
    try:
        package = json.loads((folder / "package.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    manager = "pnpm" if (folder / "pnpm-lock.yaml").is_file() else "yarn" if (folder / "yarn.lock").is_file() else "npm"
    if str(package.get("packageManager", "")).startswith(("pnpm", "yarn")):
        manager = str(package["packageManager"]).split("@")[0]
    blocking, warnings, steps = [], [], []
    node, tool = _version_of(["node", "--version"]), shutil.which(manager)
    required = (package.get("engines") or {}).get("node")
    if node is None:
        blocking.append("Node.js is not installed" + (f" (project wants {required})" if required else "") + "; install it from nodejs.org or a version manager.")
    elif required:
        try:
            if not SpecifierSet(re.sub(r"(?<=\d)\s+(?=[<>=])", ",", required.replace("^", ">=").replace("~", ">="))).contains(Version(node)):
                warnings.append(f"Installed Node {node} may not satisfy engines.node {required}")
        except (InvalidSpecifier, InvalidVersion):
            pass
    if tool is None:
        blocking.append(f"{manager} is not installed")
    has_modules = (folder / "node_modules").is_dir()
    if not has_modules and not blocking:
        locked = (folder / {"npm": "package-lock.json", "yarn": "yarn.lock", "pnpm": "pnpm-lock.yaml"}[manager]).is_file()
        argv = {"npm": ["npm", "ci"] if locked else ["npm", "install"],
                "yarn": ["yarn", "install", "--frozen-lockfile"] if locked else ["yarn", "install"],
                "pnpm": ["pnpm", "install", "--frozen-lockfile"] if locked else ["pnpm", "install"]}[manager]
        steps.append({"argv": [shutil.which(argv[0]) or argv[0], *argv[1:]], "cwd": directory,
                      "purpose": "Install dependencies into node_modules"})
    script = (package.get("scripts") or {}).get("test")
    return {"ecosystem": "node", "directory": directory, "toolchain": {"node": node, "manager": manager, "required": required},
            "dependencies": {"checked": True, "missing": [] if has_modules else ["node_modules"]},
            "blocking": blocking, "warnings": warnings, "plan": steps, "toolchain_fix": None,
            "test_command": [shutil.which(manager) or manager, "test"] if script else None}


def _pom_java_level(folder):
    try:
        text = (folder / "pom.xml").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    found = re.search(r"<(?:maven\.compiler\.(?:release|source|target)|java\.version)>\s*(\d+(?:\.\d+)?)\s*<", text)
    return found.group(1) if found else None


def _java_major(text):
    found = re.search(r'version "(\d+)(?:\.(\d+))?', text or "")
    if not found:
        return None
    major = int(found.group(1))
    return int(found.group(2)) if major == 1 and found.group(2) else major


def inspect_jvm(root, directory, ecosystem):
    folder = Path(root) / directory
    blocking, warnings, steps = [], [], []
    code, output = _run(["java", "-version"])
    installed = _java_major(output) if code == 0 else None
    required = _pom_java_level(folder) if ecosystem == "maven" else None
    if installed is None:
        blocking.append("JDK is not installed" + (f" (project targets Java {required})" if required else "") + "; install a JDK and set JAVA_HOME.")
    elif required:
        level = int(required.split(".")[-1] if required.startswith("1.") else required.split(".")[0])
        if installed < level:
            blocking.append(f"Installed JDK {installed} is older than the project's Java {level}.")
    if ecosystem == "maven":
        tool = _tool("mvn", folder, ("mvnw.cmd" if IS_WINDOWS else "mvnw",))
        argv, test = ([tool, "-B", "-q", "dependency:resolve"], [tool, "-B", "test"]) if tool else (None, None)
    else:
        tool = _tool("gradle", folder, ("gradlew.bat" if IS_WINDOWS else "gradlew",))
        argv, test = ([tool, "--no-daemon", "testClasses"], [tool, "--no-daemon", "test"]) if tool else (None, None)
    if tool is None:
        blocking.append(f"No {ecosystem} executable or wrapper found")
    elif not blocking:
        steps.append({"argv": argv, "cwd": directory, "purpose": "Resolve and compile dependencies"})
    return {"ecosystem": ecosystem, "directory": directory,
            "toolchain": {"java": installed, "required_java": required, "build_tool": tool},
            "dependencies": {"checked": False, "missing": []}, "blocking": blocking, "warnings": warnings,
            "plan": steps, "toolchain_fix": None, "test_command": test}


def inspect_simple(root, directory, ecosystem):
    folder = Path(root) / directory
    binary, versions = ("go", ["go", "version"]) if ecosystem == "go" else ("cargo", ["cargo", "--version"])
    version = _version_of(versions)
    blocking, steps = [], []
    if version is None:
        blocking.append(f"{binary} is not installed")
    else:
        fetch = [binary, "mod", "download"] if ecosystem == "go" else [binary, "fetch"]
        steps.append({"argv": fetch, "cwd": directory, "purpose": "Download dependencies"})
    wanted = None
    if ecosystem == "go":
        found = re.search(r"^go\s+(\d+(?:\.\d+)*)", (folder / "go.mod").read_text(encoding="utf-8", errors="replace"), re.M)
        wanted = found.group(1) if found else None
    return {"ecosystem": ecosystem, "directory": directory, "toolchain": {binary: version, "required": wanted},
            "dependencies": {"checked": False, "missing": []}, "blocking": blocking, "warnings": [],
            "plan": steps, "toolchain_fix": None,
            "test_command": [binary, "test", "./..."] if ecosystem == "go" else [binary, "test"]}


# ------------------------------------------------------------------------- entry points

def inspect_project(root, cwd=".", *, python_executable=None):
    """Detect every project under cwd and report toolchain, missing dependencies and the install plan."""
    projects = []
    for found in detect_projects(root, cwd):
        ecosystem, directory = found["ecosystem"], found["directory"] or "."
        if ecosystem == "python":
            item = inspect_python(root, directory, python_executable=python_executable)
        elif ecosystem == "node":
            item = inspect_node(root, directory)
        elif ecosystem in {"maven", "gradle"}:
            item = inspect_jvm(root, directory, ecosystem)
        else:
            item = inspect_simple(root, directory, ecosystem)
        item["markers"] = found["markers"]
        item["ready"] = not item["blocking"] and not item["plan"] and not item.get("toolchain_fix")
        projects.append(item)
    return {"projects": projects, "detected": bool(projects),
            "ready": bool(projects) and all(p["ready"] for p in projects),
            "summary": ("No known project manifest found under " + cwd) if not projects else
            "; ".join(f"{p['ecosystem']}@{p['directory']}: " + ("ready" if p["ready"] else
                      "blocked" if p["blocking"] and not p.get("toolchain_fix") else "needs install") for p in projects)}


async def _run_step(workspace, root, step, executed, *, recovery=False):
    observation = await workspace.executor.run_command(root, command=step["argv"], cwd=step["cwd"], unlimited=True)
    stdout, stderr = observation.get("stdout", ""), observation.get("stderr", "")
    entry = {"argv": step["argv"], "cwd": step["cwd"], "purpose": step["purpose"],
             "status": observation.get("status"), "exit_code": observation.get("exit_code"),
             "stdout": stdout[-1500:], "stderr": stderr[-1500:]}
    if observation.get("exit_code") not in (0, None) or observation.get("status") != "completed":
        entry["error_lines"] = error_lines(stdout, stderr)
    if recovery:
        entry["recovery"] = True
    executed.append(entry)
    return observation


async def provision(workspace, *, cwd=".", apply=False, python_executable=None, max_rounds=3):
    """Inspect, then (apply=True) run the install plan project by project until nothing is missing."""
    root = workspace.root
    report = await asyncio.to_thread(inspect_project, root, cwd, python_executable=python_executable)
    if not apply:
        return {**report, "applied": False}
    executed = []
    for _ in range(max_rounds):
        pending = [p for p in report["projects"] if p.get("toolchain_fix") or (p["plan"] and not p["blocking"])]
        if not pending:
            break
        failed = False
        for project in pending:
            steps = ([project["toolchain_fix"] | {"cwd": "."}] if project.get("toolchain_fix") else []) + \
                    ([] if project.get("toolchain_fix") else project["plan"])
            for step in steps:
                observation = await _run_step(workspace, root, step, executed)
                if observation.get("status") != "completed" or observation.get("exit_code") != 0:
                    recovered = bool(step.get("on_failure"))
                    for fallback in step.get("on_failure") or []:
                        observation = await _run_step(workspace, root, fallback, executed, recovery=True)
                        if observation.get("status") != "completed" or observation.get("exit_code") != 0:
                            recovered = False
                            break
                    if not recovered:
                        failed = True
                        break
            if failed:
                break
        report = await asyncio.to_thread(inspect_project, root, cwd, python_executable=python_executable)
        if failed:
            break
    return {**report, "applied": True, "executed": executed}
