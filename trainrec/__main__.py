"""python -m trainrec export SESSIONS_DIR -o out.jsonl [--kind sft|pref|both] [--context N]

Sessions that aren't scrubbed, or whose consent/license isn't allowed, are
skipped and listed on stderr (ids and reasons only).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from contracts import loads_session

from .core import CONTEXT_EVENTS, allowed, export_preferences, export_sft, validate_record


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m trainrec")
    sub = p.add_subparsers(dest="cmd", required=True)
    ex = sub.add_parser("export")
    ex.add_argument("sessions", type=Path, help="directory of *.events.jsonl")
    ex.add_argument("-o", "--output", type=Path, required=True)
    ex.add_argument("--kind", choices=["sft", "pref", "both"], default="both")
    ex.add_argument("--context", type=int, default=CONTEXT_EVENTS)
    ds = sub.add_parser("dataset", help="session-level train/dev/test splits in chat SFT and DPO format (KAN-83)")
    ds.add_argument("sessions", type=Path)
    ds.add_argument("-o", "--output", type=Path, required=True)
    ds.add_argument("--dev", type=float, default=0.1)
    ds.add_argument("--test", type=float, default=0.1)
    ds.add_argument("--salt", default="v1", help="change only to reshuffle every split on purpose")
    a = p.parse_args(argv)

    if a.cmd == "dataset":
        from .dataset import build

        m = build(a.sessions.glob("*.events.jsonl"), a.output, a.dev, a.test, a.salt)
        print(json.dumps({k: m[k] for k in ("splits", "skipped", "duplicates_dropped")}), file=sys.stderr)
        return 0

    records, skipped = [], []
    for path in sorted(a.sessions.glob("*.events.jsonl")):
        header, events = loads_session(path.read_text(encoding="utf-8"), strict=False)
        ok, why = allowed(header)
        if not ok:
            skipped.append({"session": header.get("id", path.name), "reason": why})
            continue
        if a.kind in ("sft", "both"):
            records += export_sft(header, events, a.context)
        if a.kind in ("pref", "both"):
            records += export_preferences(header, events, a.context)
    bad = [(r["id"], validate_record(r)) for r in records if validate_record(r)]
    if bad:
        print(json.dumps({"invalid": bad[:5]}), file=sys.stderr)
        return 1
    a.output.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records), encoding="utf-8")
    counts = {k: sum(r["kind"] == k for r in records) for k in ("sft", "preference")}
    print(json.dumps({"output": str(a.output), **counts, "skipped": skipped}), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
