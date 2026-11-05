"""Shared fixtures (KAN-106): deterministic, valid, and replayable."""

from __future__ import annotations

import json

import pytest

from contracts import parse_evidence_id
from fixtures import DATA, ReplayModel, load_sessions
from fixtures.build import write


def test_rebuild_is_byte_identical(tmp_path):
    for p in write(tmp_path):
        committed = DATA / p.name
        assert committed.exists(), f"{p.name} not committed; run python -m fixtures build"
        assert p.read_bytes() == committed.read_bytes(), f"{p.name} is stale; run python -m fixtures build"


def test_fixture_coverage():
    sessions = load_sessions()
    events = [e for _, evs in sessions.values() for e in evs]
    types = {e["type"] for e in events}
    assert {"command", "file_edit", "resize", "annotation", "correction"} <= types
    assert any(e.get("mistake") for e in events)
    assert any(e["type"] == "file_edit" and e["is_config"] for e in events)
    assert any(e["type"] == "command" and e["exit_code"] not in (0, None) for e in events)
    assert "mock-nginx-port" in sessions and "mock-nginx-port-rerun" in sessions  # same task twice (KAN-45)
    # every annotation tree has exactly one root per session
    for sid, (_, evs) in sessions.items():
        roots = [e for e in evs if e["type"] == "annotation" and e["parent"] is None]
        assert len(roots) == 1, sid


def test_replay_hits_and_misses():
    m = ReplayModel()
    sessions = load_sessions()
    _, evs = sessions["mock-docker-build"]
    first = next(e for e in evs if e["type"] == "annotation")
    reply = json.loads(m.complete("annotate", [f"mock-docker-build#{first['target']}"]))
    assert reply["mistake"] is True and reply["result"] == "failure"
    with pytest.raises(KeyError):
        m.complete("annotate", ["mock-docker-build#c999"])
    assert len(m.calls) == 2


def test_replies_cite_existing_events():
    sessions = load_sessions()
    for r in ReplayModel().replies.values():
        for eid in r["evidence_ids"]:
            sid, ev = parse_evidence_id(eid)
            assert ev in {e["id"] for e in sessions[sid][1]}, eid
