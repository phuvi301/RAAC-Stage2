"""Benchmark records preserve unknown resource usage and independent verdicts."""

import json

import pytest
from pydantic import ValidationError

from raac.benchmark.run_record import RunRecord, write_record


def record(**values):
    return RunRecord(run_id="benchmark-fixture", task_id="fixture__repo-1", level="L3",
                     base_commit="a" * 40, **values)


def test_pending_record_does_not_claim_zero_usage_or_success(tmp_path):
    output = tmp_path / "nested/record.json"
    original = record()
    write_record(output, original)
    loaded = RunRecord.model_validate_json(output.read_text())
    assert loaded == original
    assert loaded.agent_status == loaded.evaluation_status == "pending"
    assert loaded.usage.usage_complete is False
    assert loaded.usage.total_tokens is None


def test_candidate_verdict_is_independent_of_agent_verdict():
    value = record(agent_status="rejected", evaluation_status="resolved")
    assert value.agent_status == "rejected"
    assert value.evaluation_status == "resolved"


def test_api_usage_cache_is_not_added_to_total():
    value = record(usage={"prompt_tokens": 100, "completion_tokens": 20,
                          "total_tokens": 120, "cached_tokens": 80,
                          "calls": 2, "usage_complete": True, "cache_usage_complete": True})
    assert value.usage.total_tokens == 120
    with pytest.raises(ValidationError):
        record(usage={**value.usage.model_dump(), "total_tokens": 200})


@pytest.mark.parametrize("values", [
    {"timing": {"total_seconds": -1.0}}, {"repetition": 0},
    {"usage": {"prompt_tokens": -1}}, {"level": "L9"},
])
def test_invalid_measurements_are_rejected(values):
    if "level" in values:
        with pytest.raises(ValidationError):
            RunRecord(run_id="test", task_id="task", base_commit="a" * 40, **values)
    else:
        with pytest.raises(ValidationError):
            record(**values)


def test_record_redacts_nested_secrets_without_mutating_original(tmp_path):
    output = tmp_path / "record.json"
    value = record(provenance={"effective_config": {"api_key": "private-key"}})
    write_record(output, value)
    assert json.loads(output.read_text())["provenance"]["effective_config"]["api_key"] == "[REDACTED]"
    assert value.provenance["effective_config"]["api_key"] == "private-key"


def test_failed_serialization_preserves_previous_record(tmp_path):
    output = tmp_path / "record.json"
    write_record(output, record())
    before = output.read_bytes()
    with pytest.raises((ValueError, TypeError)):
        write_record(output, record(provenance={"invalid": object()}))
    assert output.read_bytes() == before
