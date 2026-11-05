"""Human review of tags (KAN-39).

  python -m review list    SESSION.events.jsonl [--unreviewed] [--mistakes] [--json]
  python -m review correct SESSION.events.jsonl ANNOTATION --author HANDLE --set summary="..." --set mistake=true
  python -m review accept  SESSION.events.jsonl ANNOTATION [ANNOTATION ...] --author HANDLE
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .core import ReviewError, accept, load, make_corrections, rows


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m review")
    sub = p.add_subparsers(dest="cmd", required=True)
    ls = sub.add_parser("list")
    ls.add_argument("session", type=Path)
    ls.add_argument("--unreviewed", action="store_true")
    ls.add_argument("--mistakes", action="store_true")
    ls.add_argument("--json", action="store_true")
    co = sub.add_parser("correct")
    co.add_argument("session", type=Path)
    co.add_argument("annotation")
    co.add_argument("--author", required=True)
    co.add_argument("--set", action="append", default=[], metavar="FIELD=VALUE", required=True)
    ac = sub.add_parser("accept")
    ac.add_argument("session", type=Path)
    ac.add_argument("annotations", nargs="+")
    ac.add_argument("--author", required=True)
    a = p.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")

    try:
        if a.cmd == "list":
            _, events = load(a.session)
            items = rows(events, unreviewed=a.unreviewed, mistakes=a.mistakes)
            if a.json:
                print(json.dumps(items, indent=2, ensure_ascii=False))
            else:
                for r in items:
                    flag = "✓" if r["reviewed"] else " "
                    mark = "MISTAKE " if r["mistake"] else ""
                    print(f"[{flag}] {r['id']:>5} on {r['target']:<5} {mark}{r['result'] or '?':<8} {r['event'] or ''}")
                    print(f"        goal: {r['goal']}  ·  {r['summary']}")
                print(f"{len(items)} annotation(s)")
            return 0
        if a.cmd == "correct":
            changes = {}
            for item in a.set:
                if "=" not in item:
                    raise ReviewError(f"--set needs FIELD=VALUE, got {item!r}")
                k, v = item.split("=", 1)
                changes[k.strip()] = v
            written = make_corrections(a.session, a.annotation, changes, a.author)
        else:
            written = [ev for ann in a.annotations for ev in accept(a.session, ann, a.author)]
    except ReviewError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    print(f"appended {len(written)} correction(s): " + ", ".join(f"{w['id']} {w['field']}" for w in written))
    return 0


if __name__ == "__main__":
    sys.exit(main())
