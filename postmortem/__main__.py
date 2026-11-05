"""python -m postmortem SESSION.events.jsonl [-o out.md] [--json]"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from contracts import loads_session

from .core import postmortems, render


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m postmortem")
    p.add_argument("session", type=Path)
    p.add_argument("-o", "--output", type=Path)
    p.add_argument("--json", action="store_true", help="structured reports instead of Markdown")
    a = p.parse_args(argv)
    header, events = loads_session(a.session.read_text(encoding="utf-8"), strict=False)
    if "version" not in header.get("redaction", {}):
        print("refusing: session was not scrubbed (no redaction block)", file=sys.stderr)
        return 1
    reports = postmortems(header, events)
    if a.json:
        text = json.dumps(reports, indent=2, ensure_ascii=False) + "\n"
    elif reports:
        text = "\n".join(render(r)[0] for r in reports)
    else:
        text = f"No incidents found in {header['id']}.\n"
    if a.output:
        a.output.write_text(text, encoding="utf-8")
    else:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
