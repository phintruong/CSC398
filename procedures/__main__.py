"""python -m procedures same-task A.events.jsonl B.events.jsonl ...   (pairwise scores)
python -m procedures merge A.events.jsonl B.events.jsonl ... [--json]   (canonical procedure)"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from contracts import loads_session

from .core import canonical_procedure, make_run, same_task_score

SAME_TASK_THRESHOLD = 0.4


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m procedures")
    p.add_argument("cmd", choices=["same-task", "merge"])
    p.add_argument("sessions", nargs="+", type=Path)
    p.add_argument("--json", action="store_true")
    p.add_argument("--min-support", type=float, default=0.5)
    a = p.parse_args(argv)
    runs = [make_run(*loads_session(f.read_text(encoding="utf-8"), strict=False)) for f in a.sessions]
    sys.stdout.reconfigure(encoding="utf-8")
    if a.cmd == "same-task":
        rows = [{"a": x.session, "b": y.session, "score": same_task_score(x, y),
                 "same_task": same_task_score(x, y) >= SAME_TASK_THRESHOLD}
                for i, x in enumerate(runs) for y in runs[i + 1 :]]
        print(json.dumps(rows, indent=2))
        return 0
    proc = canonical_procedure(runs, a.min_support)
    if a.json:
        print(json.dumps(proc, indent=2, ensure_ascii=False))
        return 0
    print(f"# Procedure merged from {len(runs)} run(s)\n")
    for st in proc:
        cite = " ".join(f"[{e}]" for e in st["evidence"])
        opt = "" if st["support"] == 1 else f" (in {st['support']:.0%} of runs)"
        print(f"{st['n']}. {st['action']}{opt}" + (f": {st['why']}" if st.get("why") else "") + f" {cite}")
        if st.get("diff"):
            print("\n```diff\n" + st["diff"].rstrip("\n") + "\n```\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
