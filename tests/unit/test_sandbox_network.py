from unittest.mock import Mock

import pytest
import yaml

from raac.benchmark.dataset import Task
from raac.harness.layer3_execution.sandbox.docker_sandbox import (
    DockerSandbox,
    dockerfile,
    load_profile,
)
from raac.harness.layer3_execution.sandbox.network import NetworkFixture


def test_tarpit_is_internal_isolated_and_reserves_target(tmp_path):
    client = Mock()
    client.version.return_value = {"Version": "29.1.2"}
    fixture = NetworkFixture(client, Mock(), "requests_tarpit", tmp_path, "baseline")
    fixture.start()
    options = client.networks.create.call_args.kwargs
    assert options["internal"] is True
    assert (
        options["options"]["com.docker.network.bridge.gateway_mode_ipv4"] == "isolated"
    )
    pool = options["ipam"]["Config"][0]
    assert pool["AuxiliaryAddresses"] == {"tarpit": "10.255.255.1"}
    assert pool["Gateway"] != "10.255.255.1"
    assert fixture.options["dns"] == ["127.0.0.1"]
    client.containers.create.assert_not_called()
    fixture.close()
    client.networks.create.return_value.remove.assert_called_once()


def test_service_cleanup_still_removes_container_and_network_if_logs_fail(tmp_path):
    fixture = NetworkFixture(Mock(), Mock(), "requests_httpbin", tmp_path, "gold")
    fixture.service, fixture.network = Mock(), Mock()
    fixture.service.logs.side_effect = RuntimeError("logs unavailable")
    with pytest.raises(RuntimeError, match="logs unavailable"):
        fixture.close()
    fixture.service.remove.assert_called_once_with(force=True)
    fixture.network.remove.assert_called_once()


def test_network_start_error_cleans_volume_and_fixture(tmp_path, monkeypatch):
    from raac.harness.layer3_execution.sandbox import network

    fixture = Mock()
    fixture.start.side_effect = RuntimeError("isolated mode unavailable")
    monkeypatch.setattr(network, "NetworkFixture", Mock(return_value=fixture))
    runner = DockerSandbox(client=Mock())
    with pytest.raises(RuntimeError, match="isolated mode unavailable"):
        runner.execute(
            Mock(),
            ["python"],
            tmp_path,
            "baseline",
            profile={"network_fixture": "requests_httpbin"},
        )
    fixture.close.assert_called_once()
    runner.client.volumes.create.return_value.remove.assert_called_once_with(force=True)
    runner.client.containers.create.assert_not_called()


def test_only_named_network_fixtures_are_allowed_and_do_not_change_recipe(tmp_path):
    task = Task("fixture", "fixture/repo", "url", "t", "b", "a" * 40)
    profile = {
        "version": 1,
        "repo": task.repo,
        "base_image": "python:3.11",
        "install_commands": [],
        "test_command": ["pytest"],
    }
    original = dockerfile(profile)
    for kind in ["none", "requests_tarpit", "requests_httpbin"]:
        profile["network_fixture"] = kind
        path = tmp_path / "profile.yaml"
        path.write_text(yaml.safe_dump(profile))
        assert load_profile(path, task)["network_fixture"] == kind
        assert dockerfile(profile) == original
    profile["network_fixture"] = "host"
    path.write_text(yaml.safe_dump(profile))
    with pytest.raises(ValueError, match="network fixture"):
        load_profile(path, task)


def test_service_create_failure_removes_its_network_and_checkout(tmp_path):
    client = Mock()
    client.version.return_value = {"Version": "29.1.2"}
    client.containers.create.side_effect = RuntimeError("service create failed")
    runner = DockerSandbox(client=client)
    with pytest.raises(RuntimeError, match="service create failed"):
        runner.execute(
            Mock(id="base-image"),
            ["python"],
            tmp_path,
            "baseline",
            profile={"network_fixture": "requests_httpbin"},
        )
    client.networks.create.return_value.remove.assert_called_once()
    client.volumes.create.return_value.remove.assert_called_once_with(force=True)


@pytest.mark.parametrize("version", ["27.5.1", "", "unknown"])
def test_old_or_unknown_engine_is_rejected_before_allocating_network(tmp_path, version):
    client = Mock()
    client.version.return_value = {"Version": version}
    fixture = NetworkFixture(client, Mock(), "requests_tarpit", tmp_path, "baseline")
    with pytest.raises(RuntimeError, match="Docker Engine 28"):
        fixture.start()
    client.networks.create.assert_not_called()
    client.containers.create.assert_not_called()


def test_candidate_cannot_replace_internal_service_trust_bundle(tmp_path):
    patch = tmp_path / "candidate.patch"
    patch.write_text(
        "--- a/requests/cacert.pem\n+++ b/requests/cacert.pem\n@@ -1 +1 @@\n-old\n+new\n"
    )
    client = Mock()
    runner = DockerSandbox(client=client)
    runner.config["results"] = str(tmp_path / "results")
    record = runner.run("psf__requests-1723", patch_path=patch)
    assert record["status"] == "protected_test_error"
    client.info.assert_not_called()
