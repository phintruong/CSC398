"""Repeated-run alignment and canonical procedures (KAN-46)."""

from __future__ import annotations

import subprocess
import sys
from itertools import combinations
from pathlib import Path

from fixtures import load_sessions
from procedures import HashEmbedder, align, canonical_procedure, make_run, normalize_command, same_task_score
from procedures.__main__ import SAME_TASK_THRESHOLD

S = load_sessions()
RUNS = {k: make_run(*v) for k, v in S.items()}
REPO = Path(__file__).resolve().parent.parent


def test_normalize_command():
    assert normalize_command("sudo ssh <USER_2>@<HOST_7> -p 2222") == "ssh <USER>@<HOST> -p N"


def test_embedder_basics():
    e = HashEmbedder()
    a, b, c = e.embed(["sudo nginx -t", "nginx -t", "docker build -t api ."])
    dot = lambda x, y: sum(p * q for p, q in zip(x, y))  # noqa: E731
    assert abs(dot(a, a) - 1) < 1e-9 and dot(a, b) > dot(a, c)


def test_same_task_separates_fixtures():
    same = {("mock-nginx-port", "mock-nginx-port-rerun")}
    for x, y in combinations(sorted(RUNS), 2):
        score = same_task_score(RUNS[x], RUNS[y])
        assert (score >= SAME_TASK_THRESHOLD) == ((x, y) in same), (x, y, score)


def test_self_alignment_is_identity():
    r = RUNS["mock-docker-build"]
    assert [(i, j) for i, j, _ in align(r, r)] == [(k, k) for k in range(len(r.steps))]
    assert same_task_score(r, r) == 1.0


def test_canonical_procedure_drops_mistakes_and_cites_both_runs():
    proc = canonical_procedure([RUNS["mock-nginx-port"], RUNS["mock-nginx-port-rerun"]])
    assert [p.get("command") or "edit" for p in proc] == [
        "edit", "sudo nginx -t", "sudo systemctl reload nginx", "curl -sI http://localhost:8080 | head -1"]
    edit = proc[0]
    assert "+\tlisten 8080 default_server;" in edit["diff"]  # the clean edit, with the semicolon
    assert edit["support"] == 1.0
    assert {e.split("#")[0] for e in edit["evidence"]} == {"mock-nginx-port", "mock-nginx-port-rerun"}
    assert all(p["support"] == 1.0 for p in proc)


def test_reference_is_cleanest_run_regardless_of_order():
    a, b = RUNS["mock-nginx-port"], RUNS["mock-nginx-port-rerun"]
    assert canonical_procedure([a, b]) == canonical_procedure([b, a])


def test_single_run_procedure_excludes_failures_and_diagnostics():
    proc = canonical_procedure([RUNS["mock-docker-build"]])
    cmds = [p.get("command") for p in proc]
    assert "ls" not in cmds
    assert cmds.count("docker build -t api:dev .") == 1  # only the successful build
    assert canonical_procedure([]) == []


def test_cli():
    files = [str(REPO / f"fixtures/data/{k}.events.jsonl") for k in ("mock-nginx-port", "mock-nginx-port-rerun")]
    r = subprocess.run([sys.executable, "-m", "procedures", "merge", *files], capture_output=True, text=True,
                       cwd=REPO, encoding="utf-8")
    assert r.returncode == 0, r.stderr
    assert "1. Modified `/etc/nginx/sites-available/default`" in r.stdout and "[mock-nginx-port#f10]" in r.stdout
