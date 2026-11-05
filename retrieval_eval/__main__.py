"""python -m retrieval_eval [--queries FILE] [--sessions DIR] [--searcher module:function] [--min-mrr X]

Default searcher: the BM25 baseline over the sessions directory. A real
searcher (e.g. Group 6's search_events) is passed as ``module:function`` and
must accept (query, k) and return record ids. Exits 1 if MRR < --min-mrr.
"""

from __future__ import annotations

import argparse
import importlib
import json
import sys
from pathlib import Path

from .core import BM25, check_ids_exist, load_queries, records_from_session, score, validate_query

HERE = Path(__file__).resolve().parent


def main(argv: list[str] | None = None) -> int:
    from fixtures import load_sessions

    p = argparse.ArgumentParser(prog="python -m retrieval_eval")
    p.add_argument("--queries", type=Path, default=HERE / "queries.jsonl")
    p.add_argument("--sessions", type=Path, default=HERE.parent / "fixtures" / "data")
    p.add_argument("--searcher", help="module:function taking (query, k)")
    p.add_argument("--min-mrr", type=float, default=0.0)
    p.add_argument("--per-query", action="store_true")
    a = p.parse_args(argv)

    queries = load_queries(a.queries)
    sessions = load_sessions(a.sessions)
    errs = [e for q in queries for e in validate_query(q)] + check_ids_exist(queries, sessions)
    if errs:
        print("\n".join(errs), file=sys.stderr)
        return 2
    if a.searcher:
        mod, fn = a.searcher.split(":")
        search = getattr(importlib.import_module(mod), fn)
    else:
        search = BM25([r for h, evs in sessions.values() for r in records_from_session(h, evs)])
    report = score(queries, search)
    if not a.per_query:
        report.pop("per_query")
    print(json.dumps(report, indent=2))
    return 0 if report["mrr"] >= a.min_mrr else 1


if __name__ == "__main__":
    sys.exit(main())
