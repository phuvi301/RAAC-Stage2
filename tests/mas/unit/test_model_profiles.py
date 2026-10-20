"""Role model resolution, HTTP payloads and shared usage guards."""
import asyncio
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx
import pytest
from pydantic import SecretStr, ValidationError
import yaml

from raac.common.errors import RunGuardError
from raac.llm.client import LLMClient, LLMError
from raac.llm.config import LLMConfig, load_llm_config
from raac.registry.loader import load_registry

ROOT = Path(__file__).resolve().parents[3]


def call_context(agent="analyst"):
    return {"run_id": "profiles-run", "task_id": "task", "level": "L3", "agent_id": agent,
            "registry_version": "test", "prompt_version": "test", "provenance": {}}


def test_profile_inherits_parameters_without_changing_default():
    config = LLMConfig(model="base-id", temperature=0.2, max_tokens=1000,
                       profiles={"coding": {"model": "provider/code-id", "temperature": 0.0}})
    assert config.resolve_model("coding").model_dump() == {
        "model": "provider/code-id", "temperature": 0.0, "max_tokens": 1000}
    assert config.resolve_model().model_dump() == {
        "model": "base-id", "temperature": 0.2, "max_tokens": 1000}


def test_named_profiles_can_run_without_a_default_model():
    config = LLMConfig(profiles={"coding": {"model": "code-id"}})
    assert config.resolve_role_models({"coder": "coding"})["coder"].model == "code-id"
    with pytest.raises(ValueError, match="analyst.*LLM_MODEL.*reviewer"):
        config.resolve_role_models({"analyst": "default", "coder": "coding", "reviewer": "missing"})


@pytest.mark.parametrize("profile", [
    {}, {"model": " "}, {"model": 42}, {"model": "id", "max_tokens": 0},
    {"model": "id", "max_tokens": "100"}, {"model": "id", "temperature": 3.0},
    {"model": "id", "temperature": float("nan")},
    {"model": "id", "api_key": "forbidden"}, {"model": "id", "base_url": "http://other"},
])
def test_invalid_or_secret_profile_fields_are_rejected(profile):
    with pytest.raises(ValidationError):
        LLMConfig(profiles={"coding": profile})


@pytest.mark.parametrize("name", ["default", "Bad", "../coding", ""])
def test_profile_names_are_validated_and_default_is_reserved(name):
    with pytest.raises(ValidationError):
        LLMConfig(profiles={name: {"model": "id"}})


def test_cli_and_environment_override_only_default_model(tmp_path):
    path = tmp_path / "llm.yaml"
    path.write_text(yaml.safe_dump({"model": "yaml-model", "profiles": {
        "coding": {"model": "fixed-code", "max_tokens": 8000}}}))
    with patch.dict(os.environ, {"LLM_MODEL": "env-model", "LLM_API_KEY": "test-private"}, clear=True):
        assert load_llm_config(path).resolve_model().model == "env-model"
        config = load_llm_config(path, model="cli-model")
        assert config.resolve_model().model == "cli-model"
        assert config.resolve_model("coding").model == "fixed-code"
        assert config.resolve_model("coding").max_tokens == 8000
        assert "test-private" not in json.dumps(config.model_dump())
    with patch.dict(os.environ, {}, clear=True):
        assert load_llm_config(path).resolve_model().model == "yaml-model"


def test_loader_keeps_named_refs_for_runtime_resolution(tmp_path):
    import shutil
    directory = tmp_path / "registry"
    shutil.copytree(ROOT / "registry", directory)
    path = directory / "roles/core/analyst.yaml"
    data = yaml.safe_load(path.read_text())
    data["model"] = {"ref": "analysis"}
    path.write_text(yaml.safe_dump(data))
    registry = load_registry(directory)
    assert registry.get("Analyst").spec.model.ref == "analysis"
    assert registry.snapshot()["roles"]["analyst"]["model"] == {"ref": "analysis"}


class ProfileClientTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.log = Path(self.temp.name) / "llm.jsonl"

    async def test_models_are_selected_per_call_without_mutating_shared_config(self):
        payloads = []
        def handler(request):
            data = json.loads(request.content)
            payloads.append(data)
            return httpx.Response(200, json={"model": "served-" + data["model"],
                "choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": "{}"}}],
                "usage": {"prompt_tokens": 2, "completion_tokens": 3}})
        config = LLMConfig(model="base-id", api_key=SecretStr("test-private"), profiles={
            "coding": {"model": "code-id", "temperature": 0.4, "max_tokens": 8192}})
        client = LLMClient(config, log_path=self.log, transport=httpx.MockTransport(handler))
        try:
            results = await asyncio.gather(
                client.complete([], context=call_context()),
                client.complete([], context=call_context("coder"), model_ref="coding"),
            )
            await client.complete([], context=call_context("reviewer"))
        finally:
            await client.aclose()
        assert [item["model"] for item in payloads] == ["base-id", "code-id", "base-id"]
        assert payloads[1]["max_tokens"] == 8192
        assert payloads[1]["temperature"] == 0.4
        assert config.model == "base-id"
        assert results[1].model == "served-code-id"
        assert client.usage.snapshot(run_id="profiles-run")["total_tokens"] == 15
        assert client.usage.snapshot(run_id="profiles-run", agent_id="coder")["total_tokens"] == 5
        events = [json.loads(line) for line in self.log.read_text().splitlines()]
        assert [event["model_ref"] for event in events] == ["default", "coding", "default"]
        assert events[1]["requested_model"] == "code-id"
        assert events[1]["model_settings"]["max_tokens"] == 8192
        assert "test-private" not in self.log.read_text()

    async def test_unknown_ref_is_rejected_before_http_even_with_valid_default(self):
        def handler(request):
            self.fail("Unknown model profile must not send HTTP")
        client = LLMClient(LLMConfig(model="base-id"), log_path=self.log,
                           transport=httpx.MockTransport(handler))
        try:
            with self.assertRaisesRegex(LLMError, "Unknown model profile"):
                await client.complete([], context=call_context(), model_ref="missing")
        finally:
            await client.aclose()
        assert not self.log.exists()

    async def test_unknown_usage_blocks_other_models_in_the_same_run(self):
        count = 0
        def handler(request):
            nonlocal count
            count += 1
            return httpx.Response(200, json={"choices": [{"finish_reason": "stop",
                "message": {"content": "{}"}}]})
        client = LLMClient(LLMConfig(model="base-id", profiles={"coding": {"model": "code-id"}}),
                           log_path=self.log, transport=httpx.MockTransport(handler))
        try:
            await client.complete([], context=call_context())
            with self.assertRaises(RunGuardError):
                await client.complete([], context=call_context("coder"), model_ref="coding")
        finally:
            await client.aclose()
        assert count == 1
