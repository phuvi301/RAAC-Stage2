"""Check reproducible snapshots and the public Make commands without Docker or LLMs."""

import hashlib
import json
import runpy
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def project(tmp_path):
    (tmp_path / "configs").mkdir()
    (tmp_path / "configs/llm.yaml").write_text(
        "version: test-v1\nmodel: example\napi_key: private-config-key\n"
    )
    (tmp_path / ".env").write_text("LLM_API_KEY=private-env-key\n")
    shutil.copytree(ROOT / "registry", tmp_path / "registry")
    (tmp_path / "requirements.lock").write_text("pytest==9.1.1\n")
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "fixture"\n')
    return tmp_path


def freeze(project, output):
    namespace = runpy.run_path(str(ROOT / "scripts/freeze_config.py"))
    return namespace["freeze"](project, output)


def test_freeze_preserves_inputs_hashes_and_redacts_secrets(project):
    output = project / "snapshot.json"
    freeze(project, output)
    text = output.read_text(encoding="utf-8")
    data = json.loads(text)
    config = data["files"]["configs/llm.yaml"]
    assert config["sha256"] == hashlib.sha256(
        (project / "configs/llm.yaml").read_bytes()
    ).hexdigest()
    assert config["snapshot"]["model"] == "example"
    assert config["snapshot"]["api_key"] == "[REDACTED]"
    assert data["registry_version"] == (project / "registry/VERSION").read_text().strip()
    assert "registry/prompts/coder.md" in data["files"]
    assert "requirements.lock" in data["files"]
    assert "private-config-key" not in text
    assert "private-env-key" not in text
    assert ".env" not in data["files"]
    assert "private-config-key" in (project / "configs/llm.yaml").read_text()


def test_freeze_refuses_to_overwrite_existing_snapshot(project):
    output = project / "snapshot.json"
    output.write_text("keep this")
    with pytest.raises(FileExistsError):
        freeze(project, output)
    assert output.read_text() == "keep this"


def test_freeze_invalid_yaml_does_not_create_snapshot(project):
    (project / "configs/llm.yaml").write_text("model: [broken")
    import yaml

    output = project / "snapshot.json"
    with pytest.raises(yaml.YAMLError):
        freeze(project, output)
    assert not output.exists()


@pytest.mark.skipif(not shutil.which("make"), reason="GNU Make is not installed")
def test_make_test_propagates_test_failure(tmp_path):
    test = tmp_path / "test_failure.py"
    test.write_text("def test_failure():\n    assert False\n")
    result = subprocess.run(
        ["make", "test", f"PYTHON={sys.executable}", f'TEST_ARGS="{test.as_posix()}" -q'],
        cwd=ROOT, capture_output=True, text=True, timeout=30, check=False,
    )
    assert result.returncode != 0
    assert "1 failed" in result.stdout


@pytest.mark.skipif(not shutil.which("make"), reason="GNU Make is not installed")
def test_make_pilot_requires_explicit_task():
    result = subprocess.run(
        ["make", "pilot", "TASK="], cwd=ROOT, capture_output=True, text=True, timeout=10,
        check=False,
    )
    assert result.returncode != 0
    assert "TASK" in result.stderr
