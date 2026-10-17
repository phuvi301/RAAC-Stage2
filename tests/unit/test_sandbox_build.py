from unittest.mock import Mock

import pytest

from raac.harness.layer3_execution.sandbox import build


def test_build_timeout_stops_process_and_private_builder(tmp_path, monkeypatch):
    process = Mock()
    process.poll.return_value = None
    commands = []
    monkeypatch.setattr(build.subprocess, "Popen", lambda *a, **k: process)
    monkeypatch.setattr(
        build.subprocess,
        "run",
        lambda command, **kwargs: commands.append(command) or Mock(returncode=1),
    )
    times = iter([0, 0.1, 0.2, 2])
    monkeypatch.setattr(build.time, "monotonic", lambda: next(times))
    with pytest.raises(build.BuildTimeout):
        build._build_image(tmp_path, "fixture:test", tmp_path / "build.log", 1)
    process.terminate.assert_called_once()
    assert commands[0][:3] == ["docker", "buildx", "inspect"]
    assert commands[1][:3] == ["docker", "buildx", "create"]
    assert commands[-1][:4] == ["docker", "buildx", "rm", "--force"]
    assert commands[0][3] == commands[-1][-1]


def test_build_failure_keeps_log_and_cleans_builder(tmp_path, monkeypatch):
    process = Mock(returncode=1)
    process.poll.return_value = 1
    commands = []
    monkeypatch.setattr(build.subprocess, "Popen", lambda *a, **k: process)
    monkeypatch.setattr(
        build.subprocess,
        "run",
        lambda command, **kwargs: commands.append(command) or Mock(returncode=1),
    )
    with pytest.raises(RuntimeError, match="exited 1"):
        build._build_image(tmp_path, "fixture:test", tmp_path / "build.log", 10)
    assert (tmp_path / "build.log").exists()
    assert commands[-1][2:4] == ["rm", "--force"]


def test_success_retains_builder_and_avoids_oci_export(tmp_path, monkeypatch):
    commands = []
    monkeypatch.setattr(
        build.subprocess,
        "run",
        lambda cmd, **kw: commands.append(cmd) or Mock(returncode=0),
    )

    def start(cmd, **kw):
        commands.append(cmd)
        return Mock(returncode=0, poll=lambda: 0)

    monkeypatch.setattr(build.subprocess, "Popen", start)
    build._build_image(tmp_path, "fixture:test", tmp_path / "build.log", 10)
    assert len(commands) == 2
    assert commands[0][2] == "inspect" and commands[1][2] == "build"
    assert "--cache-to" not in commands[1]
    assert "--cache-from" not in commands[1]
    assert "--load" in commands[1]


def test_build_metrics_do_not_sum_overlapping_steps(tmp_path):
    log = tmp_path / "build.log"
    log.write_text(
        "#4 [5/6] RUN compile\n#4 DONE 12.3s\n#5 exporting to docker image format\n#5 DONE 8.2s\n"
    )
    result = build.build_metrics(log)
    assert [s["seconds"] for s in result["steps"]] == [12.3, 8.2]
