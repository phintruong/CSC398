"""Training-record export acceptance tests (docs/design/training-records.md section 4)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from contracts import parse_evidence_id
from fixtures import load_sessions
from trainrec import NotScrubbed, allowed, export_preferences, export_sft, validate_record

SESSIONS = load_sessions()
REPO = Path(__file__).resolve().parent.parent
ALL = [(sid, h, evs) for sid, (h, evs) in sorted(SESSIONS.items())]


def all_records():
    for sid, h, evs in ALL:
        for r in export_sft(h, evs) + export_preferences(h, evs):
            yield sid, evs, r


def test_t1_records_validate():
    recs = [r for _, _, r in all_records()]
    assert len(recs) > 20
    for r in recs:
        assert validate_record(r) == [], r["id"]


def test_t2_no_future_leakage():
    for sid, evs, r in all_records():
        steps = [e for e in evs if e["type"] in ("command", "file_edit")]
        pos = {e["id"]: i for i, e in enumerate(steps)}
        tgt = r["input"]["target"]["id"]
        assert all(pos[c["id"]] < pos[tgt] for c in r["input"]["context"])
        later = steps[pos[tgt] + 1 :]
        blob = json.dumps(r["input"])
        for e in later:
            # text unique to a later event must not appear in the input
            for key in ("output", "diff"):
                val = e.get(key)
                if val and len(val) > 30 and all(val not in json.dumps(s) for s in steps[: pos[tgt] + 1]):
                    assert val not in blob


def test_t3_corrections():
    h, evs = SESSIONS["mock-nginx-port"]
    sft = {r["provenance"]["annotation"]: r for r in export_sft(h, evs)}
    (pref,) = export_preferences(h, evs)
    ann = pref["provenance"]["annotation"]
    assert sft[ann]["output"]["summary"].startswith("Changed the port to 8080")
    assert sft[ann]["provenance"]["label_source"] == "human-corrected"
    assert pref["chosen"]["summary"] != pref["rejected"]["summary"]
    assert pref["rejected"]["summary"] == "Changed the port but dropped the semicolon."
    assert pref["provenance"]["correction_authors"] == ["reviewer-1"]
    # a no-op correction gives no preference signal
    noop = {"type": "correction", "id": "x99", "target": ann, "field": "summary", "author": "r",
            "new": pref["chosen"]["summary"], "t_created": "2026-12-01T00:00:00Z"}
    assert len(export_preferences(h, evs + [noop])) == 1  # re-confirming the same value: still one pair
    # a correction that sets the model's own value back: corrected == original, no pair
    reverted = [e for e in evs if e["type"] != "correction"] + [
        {**noop, "new": "Changed the port but dropped the semicolon."}]
    assert export_preferences(h, reverted) == []


def test_t4_policy():
    h, evs = SESSIONS["mock-docker-build"]
    with pytest.raises(NotScrubbed):
        export_sft({**h, "redaction": {}}, evs)
    assert allowed(h) == (True, "ok")
    assert allowed({**h, "provenance": {**h["provenance"], "consent": "unknown"}})[0] is False
    assert allowed({**h, "provenance": {**h["provenance"], "license": "All-Rights-Reserved"}})[0] is False


def test_t4_cli_skips_disallowed(tmp_path):
    d = tmp_path / "s"
    d.mkdir()
    for sid in ("mock-docker-build", "mock-git-conflict"):
        src = REPO / "fixtures/data" / f"{sid}.events.jsonl"
        text = src.read_text(encoding="utf-8")
        if sid == "mock-git-conflict":
            text = text.replace('"license": "CC0-1.0"', '"license": "none"', 1)
        (d / src.name).write_text(text, encoding="utf-8")
    out = tmp_path / "r.jsonl"
    r = subprocess.run([sys.executable, "-m", "trainrec", "export", str(d), "-o", str(out)],
                       capture_output=True, text=True, cwd=REPO)
    assert r.returncode == 0, r.stderr
    summary = json.loads(r.stderr)
    assert summary["skipped"] == [{"session": "mock-git-conflict", "reason": "license 'none' not allowed"}]
    sessions = {json.loads(l)["provenance"]["session"] for l in out.read_text(encoding="utf-8").splitlines()}
    assert sessions == {"mock-docker-build"}


def test_t5_deterministic():
    first = [r for _, _, r in all_records()]
    assert first == [r for _, _, r in all_records()]
    assert len({r["id"] for r in first}) == len(first)


def test_t6_evidence_exists():
    for sid, evs, r in all_records():
        ids = {e["id"] for e in evs}
        for eid in r["provenance"]["evidence"]:
            s, e = parse_evidence_id(eid)
            assert s == sid and e in ids


def test_context_limit_and_trimming():
    h, evs = SESSIONS["mock-nginx-port"]
    assert max(len(r["input"]["context"]) for r in export_sft(h, evs, n_context=2)) == 2
    long = [dict(e) for e in evs]
    for e in long:
        if e["type"] == "command":
            e["output"] = "x" * 5000
    r = export_sft(h, long)[-1]
    assert all(len(c.get("output") or "") < 1100 for c in r["input"]["context"] if c["type"] == "command")


def test_validate_record_catches_problems():
    assert validate_record([]) == ["record: not an object"]
    assert "missing 'input'" in validate_record({"schema": "trainrec-v1", "kind": "sft", "task": "a", "id": "x", "provenance": {}})
    base = next(r for _, _, r in all_records() if r["kind"] == "preference")
    assert "chosen == rejected" in validate_record({**base, "rejected": base["chosen"]})
