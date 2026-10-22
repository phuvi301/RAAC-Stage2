"""Remote snapshots are untrusted file payloads, not host filesystem paths."""

import base64

import pytest

from raac.harness.layer3_execution.tools.docker_workspace import decode_snapshot


def entry(path="value.py", content=b"VALUE = 1\n", **extra):
    return {"path": path, "content": base64.b64encode(content).decode(), "mode": 420, **extra}


def test_decode_remote_snapshot_retains_source_bytes():
    files, modes = decode_snapshot([entry()], max_files=2, max_file_bytes=20, max_repo_bytes=20)
    assert files == {"value.py": b"VALUE = 1\n"}
    assert modes == {"value.py": 420}


@pytest.mark.parametrize("entries", [[entry("../host")], [entry("/etc/passwd")],
    [entry(".env")], [entry(), entry()], [entry(content=b"a" * 21)],
    [entry(content=b"a" * 11), entry("other.py", b"b" * 11)]])
def test_invalid_snapshot_is_rejected(entries):
    with pytest.raises(ValueError):
        decode_snapshot(entries, max_files=2, max_file_bytes=20, max_repo_bytes=20)
