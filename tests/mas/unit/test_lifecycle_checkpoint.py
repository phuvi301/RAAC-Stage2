from pathlib import Path
import pytest
from raac.harness.layer1_lifecycle import checkpoint


def test_checkpoint_roundtrip_revision_and_reject_stale_write(tmp_path):
    assert hasattr(checkpoint, "CheckpointStore"), "Durable checkpoints are missing"
    store = checkpoint.CheckpointStore(tmp_path / "lifecycle.sqlite3")
    state = {
        "invocation_id": "a" * 32,
        "run_id": "run-one",
        "revision": 0,
        "status": "PLANNING",
    }
    store.save(state)
    other = checkpoint.CheckpointStore(tmp_path / "lifecycle.sqlite3")
    loaded = other.load("a" * 32)
    assert loaded["revision"] == 1
    store.save(state)
    with pytest.raises(ValueError, match="stale"):
        other.save(loaded)
    assert other.load("a" * 32)["revision"] == 2


def test_checkpoint_bad_ids_and_version_are_rejected(tmp_path):
    assert hasattr(checkpoint, "CheckpointStore")
    store = checkpoint.CheckpointStore(tmp_path / "lifecycle.sqlite3")
    for value in ["../outside", "", "a/../b"]:
        with pytest.raises(ValueError):
            store.load(value)
