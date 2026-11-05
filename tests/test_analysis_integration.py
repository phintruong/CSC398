"""Integration checks for post-mortems (KAN-80) and the training-data factory (KAN-84).

Both run on what the rest of the pipeline really produces: a live daemon
session, every imported planted-secret recording (annotated by the rule
model), and damaged session files.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from capture import Session, import_cast
from capture.daemon import CaptureDaemon, send, serve
from contracts import dumps_session, loads_session, parse_evidence_id
from postmortem import check_wording, postmortems, render_markdown
from runtime import Guarded
from runtime.rules import RuleAnnotator
from streaming import StreamingAnnotator, annotate_session
from trainrec import export_preferences, export_sft, validate_record
from trainrec.dataset import build

from planted_set import build_cases

REPO = Path(__file__).resolve().parent.parent
CASES = build_cases()


def _annotated_import(case):
    h, evs, _, _ = import_cast(case.cast, source="t", consent="synthetic", license="CC0-1.0",
                               extra_terms=case.extra_terms)
    anns, _ = annotate_session(Guarded(RuleAnnotator()), h, evs)
    return h, evs + anns


@pytest.mark.parametrize("case", CASES, ids=[c.name for c in CASES])
def test_on_every_imported_recording(case):
    h, evs = _annotated_import(case)
    ids = {e["id"] for e in evs}
    for r in postmortems(h, evs):
        assert all(parse_evidence_id(e)[1] in ids for e in r["evidence"])
        assert check_wording(render_markdown(r), r) is None
    records = export_sft(h, evs) + export_preferences(h, evs)
    assert all(validate_record(r) == [] for r in records)
    blob = json.dumps(records) + json.dumps(postmortems(h, evs))
    for p in case.planted:
        assert p.value not in blob, f"{case.name}: planted {p.kind} reached post-mortem or training data"


def test_on_a_live_daemon_session(tmp_path):
    s = Session.start(tmp_path / "s", source="t", consent="synthetic", license="CC0-1.0", now=0.0, memory=True)
    d = CaptureDaemon(s, lambda h: StreamingAnnotator(Guarded(RuleAnnotator()), h, model_name="rules-v1"))
    ready = threading.Event()
    threading.Thread(target=serve, args=(d,), kwargs={"ready": ready}, daemon=True).start()
    assert ready.wait(5)
    work = tmp_path / "w"
    work.mkdir()
    (work / "app.cfg").write_text("port = 80\n")
    for i, (cmd, code, edit) in enumerate([("make deploy", 2, None), ("cat app.cfg", 0, None),
                                           ("vim app.cfg", 0, "port = 8080\n"), ("make deploy", 0, None)]):
        send(s.dir, {"op": "pre", "cmd": cmd, "cwd": str(work), "t": i * 2.0 + 1})
        if edit:
            (work / "app.cfg").write_text(edit)
        send(s.dir, {"op": "post", "exit": code, "t": i * 2.0 + 2})
    out = Path(send(s.dir, {"op": "stop"})["events"])
    final_text = out.read_text(encoding="utf-8")
    # the streamed annotations are in live.events.jsonl; join them with the final session for analysis
    h, final = loads_session(final_text)
    _, live = loads_session((s.dir / "live.events.jsonl").read_text(encoding="utf-8"))
    evs = final + [e for e in live if e["type"] == "annotation"]
    (r,) = postmortems(h, evs)
    assert r["status"] == "resolved" and any("app.cfg" in f["text"] for f in r["fix"])
    store = tmp_path / "store"
    store.mkdir()
    (store / "s.events.jsonl").write_text(dumps_session(h, evs), encoding="utf-8")
    m = build(store.glob("*.events.jsonl"), tmp_path / "ds", dev=0.0, test=0.0)
    assert m["splits"]["train"]["sft"] == 4


def test_clis_survive_damaged_files(tmp_path):
    h, evs = _annotated_import(CASES[1])
    text = dumps_session(h, evs)
    lines = text.splitlines()
    damaged = "\n".join(lines[:3] + ["{not json", "[1, 2, 3]"] + lines[3:]) + '\n{"type": "command", "id": "half'
    d = tmp_path / "s"
    d.mkdir()
    (d / "x.events.jsonl").write_text(damaged, encoding="utf-8")
    pm = subprocess.run([sys.executable, "-m", "postmortem", str(d / "x.events.jsonl")], capture_output=True, text=True,
                        cwd=REPO, encoding="utf-8")
    assert pm.returncode == 0, pm.stderr
    tr = subprocess.run([sys.executable, "-m", "trainrec", "export", str(d), "-o", str(tmp_path / "r.jsonl")],
                        capture_output=True, text=True, cwd=REPO)
    assert tr.returncode == 0, tr.stderr
    assert json.loads(tr.stderr)["sft"] > 0
