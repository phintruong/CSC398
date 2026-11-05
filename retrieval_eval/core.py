"""Retrieval eval: query format, recall@k / MRR, and a BM25 baseline (KAN-69, KAN-71).

A searcher is any callable ``search(query: str, k: int) -> list[str]`` that
returns record ids, best first. Session records use evidence ids
(``session#event``); document records (Group 6's index) use
``doc:<path>#<chunk>``.
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from pathlib import Path
from typing import Callable, Iterable, Sequence

from contracts import evidence_id, parse_evidence_id
from postmortem.core import apply_corrections

KS = (1, 3, 5, 10)
QUERY_KINDS = frozenset({"how_did_i_fix", "find_command", "file_history", "explain_error", "document"})
Searcher = Callable[[str, int], list]


def validate_query(q: dict) -> list[str]:
    errs = []
    for k, t in (("id", str), ("query", str), ("kind", str), ("expected", list)):
        if not isinstance(q.get(k), t):
            errs.append(f"{q.get('id', '?')}: {k!r} missing or not {t.__name__}")
    if errs:
        return errs
    if q["kind"] not in QUERY_KINDS:
        errs.append(f"{q['id']}: kind {q['kind']!r} not in {sorted(QUERY_KINDS)}")
    if not q["expected"]:
        errs.append(f"{q['id']}: expected is empty")
    for rid in q["expected"] + q.get("also_relevant", []):
        if not (rid.startswith("doc:") or "#" in rid):
            errs.append(f"{q['id']}: {rid!r} is not a record id")
    return errs


def load_queries(path: Path) -> list[dict]:
    return [json.loads(l) for l in Path(path).read_text(encoding="utf-8").splitlines() if l.strip()]


def score(queries: Sequence[dict], search: Searcher, ks: Sequence[int] = KS) -> dict:
    """recall@k: share of expected ids found in the top k (averaged per query).
    MRR: 1 / rank of the first expected-or-also-relevant hit."""
    per_query = []
    for q in queries:
        results = list(search(q["query"], max(ks)))
        expected = set(q["expected"])
        relevant = expected | set(q.get("also_relevant", []))
        row = {"id": q["id"], "kind": q["kind"]}
        for k in ks:
            row[f"recall@{k}"] = round(len(expected & set(results[:k])) / len(expected), 4)
        rank = next((i + 1 for i, r in enumerate(results) if r in relevant), None)
        row["rr"] = round(1 / rank, 4) if rank else 0.0
        per_query.append(row)
    n = len(per_query) or 1
    overall = {f"recall@{k}": round(sum(r[f"recall@{k}"] for r in per_query) / n, 4) for k in ks}
    overall["mrr"] = round(sum(r["rr"] for r in per_query) / n, 4)
    by_kind = {}
    for kind in sorted({r["kind"] for r in per_query}):
        rows = [r for r in per_query if r["kind"] == kind]
        by_kind[kind] = {"n": len(rows), "mrr": round(sum(r["rr"] for r in rows) / len(rows), 4),
                         f"recall@{ks[-1]}": round(sum(r[f"recall@{ks[-1]}"] for r in rows) / len(rows), 4)}
    return {"queries": len(per_query), **overall, "by_kind": by_kind, "per_query": per_query}


# -- baseline searcher ------------------------------------------------------------

_TOK = re.compile(r"[a-z0-9]+")
_STOP = frozenset("a an the i my me did do how what when where which why is was to of in on for and or it with".split())


def _tokens(text: str) -> list[str]:
    return [t for t in _TOK.findall(text.lower()) if t not in _STOP]


def records_from_session(header: dict, events: Iterable[dict]) -> list[tuple[str, str]]:
    """(record id, searchable text) for each command / file edit, with its annotation."""
    evs = apply_corrections(list(events))
    anns = {e["target"]: e for e in evs if e.get("type") == "annotation"}
    out = []
    for e in evs:
        if e.get("type") not in ("command", "file_edit"):
            continue
        a = anns.get(e["id"], {})
        parts = [e.get("command"), e.get("output"), e.get("path"), e.get("diff"), a.get("goal"), a.get("summary")]
        out.append((evidence_id(header["id"], e["id"]), " ".join(p for p in parts if p)))
    return out


class BM25:
    def __init__(self, records: Sequence[tuple[str, str]], k1: float = 1.2, b: float = 0.75) -> None:
        self.ids = [r for r, _ in records]
        self.docs = [Counter(_tokens(t)) for _, t in records]
        self.lens = [sum(d.values()) for d in self.docs]
        self.avg = (sum(self.lens) / len(self.lens)) if self.lens else 0.0
        df = Counter(t for d in self.docs for t in d)
        n = len(self.docs)
        self.idf = {t: math.log(1 + (n - c + 0.5) / (c + 0.5)) for t, c in df.items()}
        self.k1, self.b = k1, b

    def __call__(self, query: str, k: int) -> list[str]:
        q = _tokens(query)
        scored = []
        for i, d in enumerate(self.docs):
            s = 0.0
            for t in q:
                f = d.get(t)
                if f:
                    s += self.idf[t] * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * self.lens[i] / self.avg))
            if s > 0:
                scored.append((s, i))
        scored.sort(key=lambda x: (-x[0], x[1]))
        return [self.ids[i] for _, i in scored[:k]]


def check_ids_exist(queries: Sequence[dict], sessions: dict[str, tuple[dict, list[dict]]]) -> list[str]:
    errs = []
    for q in queries:
        for rid in q["expected"] + q.get("also_relevant", []):
            if rid.startswith("doc:"):
                continue
            sid, eid = parse_evidence_id(rid)
            if sid not in sessions or eid not in {e["id"] for e in sessions[sid][1]}:
                errs.append(f"{q['id']}: {rid} does not exist")
    return errs
