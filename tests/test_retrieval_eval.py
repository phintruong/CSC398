"""Retrieval eval set + scorer (KAN-69, KAN-71)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from fixtures import load_sessions
from retrieval_eval import BM25, load_queries, records_from_session, score, validate_query
from retrieval_eval.core import check_ids_exist

REPO = Path(__file__).resolve().parent.parent
QUERIES = load_queries(REPO / "retrieval_eval/queries.jsonl")
SESSIONS = load_sessions()


def test_queries_valid_and_ids_exist():
    assert len(QUERIES) >= 20
    assert len({q["id"] for q in QUERIES}) == len(QUERIES)
    assert [e for q in QUERIES for e in validate_query(q)] == []
    assert check_ids_exist(QUERIES, SESSIONS) == []


def test_validate_query_catches_problems():
    assert validate_query({"id": "x", "query": "q", "kind": "nope", "expected": ["a#b"]})
    assert validate_query({"id": "x", "query": "q", "kind": "document", "expected": []})
    assert validate_query({"id": "x", "query": "q", "kind": "document", "expected": ["no-hash"]})
    assert validate_query({"id": "x", "query": "q", "kind": "document", "expected": ["doc:/a.md#3"]}) == []


def test_score_math():
    qs = [{"id": "1", "kind": "find_command", "query": "a", "expected": ["s#1", "s#2"]},
          {"id": "2", "kind": "find_command", "query": "b", "expected": ["s#9"], "also_relevant": ["s#5"]}]
    results = {"a": ["s#3", "s#1", "s#2"], "b": ["s#5", "s#9"]}
    r = score(qs, lambda q, k: results[q][:k])
    assert r["recall@1"] == 0.0 and r["recall@3"] == 1.0
    assert r["mrr"] == round((1 / 2 + 1) / 2, 4)  # q2: also_relevant at rank 1 counts for MRR


def test_perfect_and_empty_searchers():
    perfect = {q["query"]: q["expected"] for q in QUERIES}
    r = score(QUERIES, lambda q, k: perfect[q][:k])
    assert r["mrr"] == 1.0 and r["recall@10"] == 1.0
    r = score(QUERIES, lambda q, k: [])
    assert r["mrr"] == 0.0 and r["recall@10"] == 0.0


def test_bm25_baseline_is_a_meaningful_bar():
    search = BM25([rec for h, evs in SESSIONS.values() for rec in records_from_session(h, evs)])
    r = score(QUERIES, search)
    assert 0.6 <= r["mrr"] < 1.0  # good enough to beat, not trivially perfect
    assert search("nginx reload", 3)[0].startswith("mock-nginx-port")


def test_cli_gate():
    ok = subprocess.run([sys.executable, "-m", "retrieval_eval", "--min-mrr", "0.5"], capture_output=True, text=True, cwd=REPO)
    assert ok.returncode == 0, ok.stderr
    bar = subprocess.run([sys.executable, "-m", "retrieval_eval", "--min-mrr", "0.99"], capture_output=True, text=True, cwd=REPO)
    assert bar.returncode == 1
