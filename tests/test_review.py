"""Human review and correction of tags, including on a live daemon session (KAN-39)."""

from __future__ import annotations

import shutil
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from capture import Session
from capture.daemon import CaptureDaemon, send, serve
from contracts import loads_session, validate_session
from postmortem import postmortems
from review import ReviewError, accept, make_corrections, rows
from runtime import Guarded
from runtime.rules import RuleAnnotator
from streaming import StreamingAnnotator
from trainrec import export_preferences, export_sft

REPO = Path(__file__).resolve().parent.parent
FIX = REPO / "fixtures/data"


@pytest.fixture
def docker_session(tmp_path):
    p = tmp_path / "s.events.jsonl"
    shutil.copy(FIX / "mock-docker-build.events.jsonl", p)
    return p


def _load(p):
    return loads_session(p.read_text(encoding="utf-8"))


def test_list_filters(docker_session):
    _, evs = _load(docker_session)
    allr = rows(evs)
    assert len(allr) == 5 and rows(evs, mistakes=True)[0]["target"] == "c2"
    assert len(rows(evs, unreviewed=True)) == 5


def test_correct_appends_and_everything_downstream_sees_it(docker_session):
    ann = rows(_load(docker_session)[1], mistakes=True)[0]["id"]
    written = make_corrections(docker_session, ann, {"summary": "Typo: requirement.txt instead of requirements.txt.",
                                                    "result": "failure"}, "rev-1", now="2026-11-05T20:00:00+00:00")
    assert [w["field"] for w in written] == ["summary", "result"]
    h, evs = _load(docker_session)
    assert validate_session(h, evs) == []
    r = next(x for x in rows(evs) if x["id"] == ann)
    assert r["summary"].startswith("Typo") and r["reviewed"] and r["changed"] == ["summary"]  # result was already failure
    # post-mortems and training data use the corrected text; a preference pair now exists
    assert "Typo: requirement.txt" in postmortems(h, evs)[0]["root_cause"]["text"]
    pref = export_preferences(h, evs)
    assert len(pref) == 1 and pref[0]["chosen"]["summary"].startswith("Typo")
    assert next(s for s in export_sft(h, evs) if s["provenance"]["annotation"] == ann)["provenance"]["label_source"] == "human-corrected"


def test_accept_marks_reviewed_without_changing_labels(docker_session):
    _, evs = _load(docker_session)
    ann = rows(evs)[1]["id"]
    accept(docker_session, ann, "rev-1")
    h, evs2 = _load(docker_session)
    r = next(x for x in rows(evs2) if x["id"] == ann)
    assert r["reviewed"] and r["changed"] == []
    assert export_preferences(h, evs2) == []  # no preference signal from an accept
    sft = next(s for s in export_sft(h, evs2) if s["provenance"]["annotation"] == ann)
    assert sft["provenance"]["label_source"] == "human-reviewed"
    assert len(rows(evs2, unreviewed=True)) == 4


@pytest.mark.parametrize("changes,msg", [
    ({"result": "great"}, "result must be"),
    ({"mistake": "maybe"}, "true or false"),
    ({"colour": "x"}, "field must be"),
    ({"parent": "a999"}, "not an annotation"),
    ({"summary": "  "}, "can't be empty"),
])
def test_bad_corrections_rejected_and_nothing_written(docker_session, changes, msg):
    before = docker_session.read_text(encoding="utf-8")
    ann = rows(_load(docker_session)[1])[0]["id"]
    with pytest.raises(ReviewError, match=msg):
        make_corrections(docker_session, ann, changes, "rev-1")
    assert docker_session.read_text(encoding="utf-8") == before


def test_author_must_be_a_handle(docker_session):
    ann = rows(_load(docker_session)[1])[0]["id"]
    with pytest.raises(ReviewError, match="handle"):
        make_corrections(docker_session, ann, {"summary": "x y z"}, "Jane Q. Public")


def test_cli(docker_session):
    def run(*args):
        return subprocess.run([sys.executable, "-m", "review", *args], capture_output=True, text=True, cwd=REPO,
                              encoding="utf-8")

    r = run("list", str(docker_session), "--mistakes")
    assert r.returncode == 0 and "MISTAKE" in r.stdout
    ann = rows(_load(docker_session)[1], mistakes=True)[0]["id"]
    r = run("correct", str(docker_session), ann, "--author", "rev-1", "--set", "mistake=false")
    assert r.returncode == 0 and "1 correction" in r.stdout
    r = run("correct", str(docker_session), ann, "--author", "rev-1", "--set", "result=meh")
    assert r.returncode == 2 and "result must be" in r.stderr


def test_review_on_a_live_daemon_session(tmp_path):
    """Tags stream in from live capture; a reviewer corrects one while the session is still running."""
    s = Session.start(tmp_path / "s", source="t", consent="synthetic", license="none", now=0.0, memory=True)
    d = CaptureDaemon(s, lambda h: StreamingAnnotator(Guarded(RuleAnnotator()), h, model_name="rules-v1"))
    ready = threading.Event()
    t = threading.Thread(target=serve, args=(d,), kwargs={"ready": ready}, daemon=True)
    t.start()
    assert ready.wait(5)
    send(s.dir, {"op": "pre", "cmd": "make test", "cwd": str(tmp_path), "t": 1.0})
    send(s.dir, {"op": "post", "exit": 2, "t": 2.0})
    live = s.dir / "live.events.jsonl"
    ann = rows(_load(live)[1], mistakes=True)[0]["id"]
    make_corrections(live, ann, {"summary": "Tests failed because the fixture DB wasn't running."}, "rev-1")
    send(s.dir, {"op": "pre", "cmd": "make test", "cwd": str(tmp_path), "t": 3.0})
    send(s.dir, {"op": "post", "exit": 0, "t": 4.0})  # the daemon keeps appending after the review
    h, evs = _load(live)
    assert validate_session(h, evs) == []
    assert "fixture DB" in next(x for x in rows(evs) if x["id"] == ann)["summary"]
    send(s.dir, {"op": "stop"})
    t.join(5)
