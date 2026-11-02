import json
from urllib.request import urlopen

from test_trace_web import running_server

from raac.collaboration.levels.l1_fixed import L1_LINEAR
from raac.collaboration.levels.l2_routing import L2_ROUTED_LINEAR
from raac.common.trace_web import TraceFeed, present_event


def test_level_topologies_come_from_actual_builders(tmp_path):
    path = tmp_path / "llm.jsonl"
    path.write_text('{"event":"benchmark_phase_started","level":"L2","phase":"setup"}\n')
    with running_server(path) as url:
        for spec in (L1_LINEAR, L2_ROUTED_LINEAR):
            with urlopen(url + "/api/topology?name=" + spec.topology_id, timeout=3) as response:
                payload = json.load(response)
            assert payload["entry_node"] == spec.entry_node
            assert payload["steps"] == [{"node_id": s.node_id, "role": s.role} for s in spec.steps]
            assert payload["edges"] == [list(edge) for edge in spec.edges]
        with urlopen(url + "/api/events", timeout=3) as response:
            assert json.load(response)["events"][0]["level"] == "L2"


def test_historical_record_metadata_and_skip_reason_are_read_only(tmp_path):
    path = tmp_path / "llm.jsonl"
    path.write_text("")
    record = tmp_path / "record.json"
    record.write_text(json.dumps({"level": "L2", "provenance": {"routing": {
        "selected_roles": ["Coder", "Tester"], "skipped": [{"role": "Planner", "reason": "Localized"}],
        "authorization": "must not be returned"}}}))
    before = record.read_bytes()
    feed = TraceFeed(path)
    metadata = feed.metadata()
    assert metadata["level"] == "L2"
    assert metadata["routing"]["skipped"][0]["reason"] == "Localized"
    assert "authorization" not in metadata["routing"]
    assert record.read_bytes() == before
    row = present_event({"event": "agent_skipped", "level": "L2", "agent_id": "Planner", "reason": "Localized"}, 1)
    assert row["agent"] == "planner" and row["summary"] == "Localized"
    row = present_event({"event": "routing_decided", "level": "L2", "selected_roles": ["Coder", "Tester"],
                         "skipped": [{"role": "Planner", "reason": "Localized"}]}, 2)
    assert row["selected_roles"] == ["Coder", "Tester"]
    assert row["skipped"][0]["role"] == "Planner"
    old_router = present_event({"event": "agent_completed", "agent_id": "router", "output": {
        "selected_roles": ["Coder", "Tester"], "skipped": [{"role": "Reviewer", "reason": "Direct test"}]}}, 3)
    assert old_router["skipped"][0]["role"] == "Reviewer"
    record.write_text("partially-written-json")
    assert feed.metadata() == {}
