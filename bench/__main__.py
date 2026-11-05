"""python -m bench run --gold DIR --pred DIR [-o report.json] [--rules rules.json]
python -m bench baseline --gold DIR --out DIR      # streaming annotator + replay model as a reference prediction

Exit status 1 when a pass rule fails (so CI can gate on it, KAN-63)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from contracts import dumps_session

from .runner import DEFAULT_RULES, load_dir, run, write_report


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m bench")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--gold", type=Path, required=True)
    r.add_argument("--pred", type=Path, required=True)
    r.add_argument("-o", "--output", type=Path)
    r.add_argument("--rules", type=Path, help="JSON {metric: threshold}; default: bench.runner.DEFAULT_RULES")
    b = sub.add_parser("baseline")
    b.add_argument("--gold", type=Path, required=True)
    b.add_argument("--out", type=Path, required=True)
    a = p.parse_args(argv)

    if a.cmd == "baseline":
        from fixtures import ReplayModel
        from streaming import annotate_session

        a.out.mkdir(parents=True, exist_ok=True)
        for sid, (h, evs) in load_dir(a.gold).items():
            base = [e for e in evs if e["type"] not in ("annotation", "correction")]
            anns, _ = annotate_session(ReplayModel(), h, base)
            (a.out / f"{sid}.events.jsonl").write_text(dumps_session(h, base + anns), encoding="utf-8")
        return 0

    rules = json.loads(a.rules.read_text()) if a.rules else DEFAULT_RULES
    report = run(a.gold, a.pred, rules)
    if a.output:
        write_report(report, a.output)
    summary = {k: report[k] for k in ("sessions", "missing_predictions", "pass")}
    summary["checks"] = [f"{c['metric']}={c['value']} (>= {c['threshold']}) {'ok' if c['pass'] else 'FAIL'}" for c in report["checks"]]
    print(json.dumps(summary, indent=2))
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
