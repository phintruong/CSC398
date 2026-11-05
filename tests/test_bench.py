"""Benchmark harness (KAN-62): metrics behave, and the runner gates on pass rules."""

from __future__ import annotations

import copy
import random
import subprocess
import sys
from pathlib import Path

from bench import descriptions, rubric, run, segmentation, tagging
from bench.metrics import prf
from contracts import dumps_session
from fixtures import load_sessions

S = load_sessions()
REPO = Path(__file__).resolve().parent.parent
GOLD = REPO / "fixtures/data"


def test_prf_edge_cases():
    assert prf(0, 0, 0)["f1"] == 1.0
    assert prf(0, 3, 0)["precision"] == 0.0
    assert prf(1, 1, 1)["f1"] == 0.5


def test_segmentation_tolerance():
    gold = [{"type": "command", "t": t} for t in (1.0, 5.0, 9.0)]
    assert segmentation(gold, gold)["f1"] == 1.0
    shifted = [{"type": "command", "t": t + 0.8} for t in (1.0, 5.0, 9.0)]
    assert segmentation(gold, shifted)["f1"] == 1.0
    assert segmentation(gold, shifted, tol=0.5)["f1"] == 0.0
    merged = [{"type": "command", "t": 1.0}, {"type": "command", "t": 9.0}]  # two events merged into one
    r = segmentation(gold, merged)
    assert (r["tp"], r["fp"], r["fn"]) == (2, 0, 1)


def test_tagging_perfect_and_degraded():
    _, evs = S["mock-nginx-port"]
    assert tagging(evs, evs)["result_macro_f1"] == 1.0
    bad = copy.deepcopy(evs)
    for e in bad:
        if e["type"] == "annotation":
            e["mistake"] = False
            e["parent"] = None
    t = tagging(evs, bad)
    assert t["mistake"]["recall"] == 0.0 and t["tree_attachment"] < 1.0


def test_tagging_uses_corrected_gold():
    _, evs = S["mock-nginx-port"]
    raw_pred = [e for e in evs if e["type"] != "correction"]  # the model's uncorrected output
    t = tagging(evs, raw_pred)
    assert t["result_macro_f1"] == 1.0  # the correction only changed a summary


def test_rubric():
    ev = {"command": "sudo nginx -t", "output": "nginx: [emerg] unexpected \";\" in /etc/nginx/nginx.conf:12", "exit_code": 1}
    ann = {"tool": "nginx", "result": "failure", "mistake": True}
    good = rubric("nginx -t fails: stray semicolon in nginx.conf line 12.", ev, ann)
    assert all(good.values()), good
    assert not rubric("nginx -t fails because of /etc/ssl/cert.pem.", ev, ann)["faithful"]  # invented file
    assert not rubric("Ran a command.", ev, ann)["specific"]
    assert not rubric("nginx checked its configuration file thoroughly today.", ev, ann)["explains_outcome"]
    assert not rubric("ok", ev, {"result": "success"})["concise"]


def test_descriptions_score_drops_with_bad_summaries():
    _, evs = S["mock-docker-build"]
    good = descriptions(evs, evs)["overall"]
    bad = copy.deepcopy(evs)
    for e in bad:
        if e["type"] == "annotation":
            e["summary"] = "Did something."
    assert descriptions(evs, bad)["overall"] < good


def _write(d: Path, sessions):
    d.mkdir(parents=True, exist_ok=True)
    for sid, (h, evs) in sessions.items():
        (d / f"{sid}.events.jsonl").write_text(dumps_session(h, evs), encoding="utf-8")


def test_runner_passes_on_baseline_and_fails_on_noise(tmp_path):
    base = tmp_path / "baseline"
    r = subprocess.run([sys.executable, "-m", "bench", "baseline", "--gold", str(GOLD), "--out", str(base)],
                       capture_output=True, text=True, cwd=REPO)
    assert r.returncode == 0, r.stderr
    report = run(GOLD, base)
    assert report["pass"] and report["sessions"] == len(S) and report["missing_predictions"] == []

    rng = random.Random(1)
    noisy = {}
    for sid, (h, evs) in S.items():
        evs = copy.deepcopy(evs)
        for e in evs:
            if e["type"] == "annotation":
                e["result"] = rng.choice(["success", "failure", "partial"])
                e["mistake"] = rng.random() < 0.5
            if e["type"] == "command":
                e["t"] += rng.choice([0, 3.0])
                e["t_end"] = max(e["t_end"], e["t"])
        noisy[sid] = (h, evs)
    _write(tmp_path / "noisy", noisy)
    report = run(GOLD, tmp_path / "noisy")
    assert not report["pass"]
    failed = {c["metric"] for c in report["checks"] if not c["pass"]}
    assert "tagging.result_macro_f1" in failed or "segmentation.f1" in failed

    cli = subprocess.run([sys.executable, "-m", "bench", "run", "--gold", str(GOLD), "--pred", str(tmp_path / "noisy")],
                         capture_output=True, text=True, cwd=REPO)
    assert cli.returncode == 1  # CI gate


def test_runner_reports_missing_predictions(tmp_path):
    _write(tmp_path / "partial", {k: v for k, v in list(S.items())[:2]})
    report = run(GOLD, tmp_path / "partial")
    assert len(report["missing_predictions"]) == len(S) - 2 and not report["pass"]
