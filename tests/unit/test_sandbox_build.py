from unittest.mock import Mock

import pytest

from raac.harness.layer3_execution.sandbox import build


def test_build_timeout_stops_process_and_private_builder(tmp_path, monkeypatch):
    process = Mock()
    process.poll.return_value = None
    commands = []
    monkeypatch.setattr(build.subprocess, "Popen", lambda *a, **k: process)
    monkeypatch.setattr(
        build.subprocess, "run", lambda command, **kwargs: commands.append(command)
    )
    times = iter([0, 0.1, 2])
    monkeypatch.setattr(build.time, "monotonic", lambda: next(times))
    with pytest.raises(build.BuildTimeout):
        build._build_image(tmp_path, "fixture:test", tmp_path / "build.log", 1)
    process.terminate.assert_called_once()
    assert commands[0][:3] == ["docker", "buildx", "create"]
    assert commands[-1][:4] == ["docker", "buildx", "rm", "--force"]
    assert commands[0][4] == commands[-1][-1]


def test_build_failure_keeps_log_and_cleans_builder(tmp_path, monkeypatch):
    process = Mock(returncode=1)
    process.poll.return_value = 1
    commands = []
    monkeypatch.setattr(build.subprocess, "Popen", lambda *a, **k: process)
    monkeypatch.setattr(
        build.subprocess, "run", lambda command, **kwargs: commands.append(command)
    )
    with pytest.raises(RuntimeError, match="exited 1"):
        build._build_image(tmp_path, "fixture:test", tmp_path / "build.log", 10)
    assert (tmp_path / "build.log").exists()
    assert commands[-1][2:4] == ["rm", "--force"]
