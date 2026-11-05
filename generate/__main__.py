"""python -m generate script|dockerfile SESSION.events.jsonl [MORE...] [-o FILE] [--base IMAGE]

Merges the sessions into a canonical procedure (procedures.canonical_procedure)
and renders it. Refuses sessions that weren't scrubbed.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from contracts import loads_session
from procedures import canonical_procedure, make_run

from .core import to_dockerfile, to_script


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m generate")
    p.add_argument("kind", choices=["script", "dockerfile"])
    p.add_argument("sessions", nargs="+", type=Path)
    p.add_argument("-o", "--output", type=Path)
    p.add_argument("--base", default="ubuntu:24.04")
    p.add_argument("--min-support", type=float, default=0.5)
    a = p.parse_args(argv)
    runs = []
    for f in a.sessions:
        h, evs = loads_session(f.read_text(encoding="utf-8"), strict=False)
        if "version" not in (h.get("redaction") or {}):
            print(f"refusing {f.name}: not scrubbed", file=sys.stderr)
            return 1
        runs.append(make_run(h, evs))
    proc = canonical_procedure(runs, a.min_support)
    title = "Procedure from " + ", ".join(r.session for r in runs)
    text = to_script(proc, title) if a.kind == "script" else to_dockerfile(proc, a.base, title)
    if a.output:
        a.output.write_text(text, encoding="utf-8", newline="\n")
    else:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
