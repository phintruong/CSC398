"""The "why?" interviewer (KAN-58).

  python -m interview questions SESSION.events.jsonl [--max 5] [--json]
  python -m interview answer SESSION.events.jsonl --target f5 --question "..." --text "..." --author HANDLE
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from contracts import loads_session

from .core import MAX_QUESTIONS, answer, questions


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m interview")
    sub = p.add_subparsers(dest="cmd", required=True)
    q = sub.add_parser("questions")
    q.add_argument("session", type=Path)
    q.add_argument("--max", type=int, default=MAX_QUESTIONS)
    q.add_argument("--json", action="store_true")
    a_ = sub.add_parser("answer")
    a_.add_argument("session", type=Path)
    a_.add_argument("--target", required=True)
    a_.add_argument("--question", required=True)
    a_.add_argument("--text", required=True)
    a_.add_argument("--author", required=True)
    a = p.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    if a.cmd == "questions":
        h, evs = loads_session(a.session.read_text(encoding="utf-8"), strict=False)
        qs = questions(h, evs, max_questions=a.max)
        if a.json:
            print(json.dumps(qs, indent=2, ensure_ascii=False))
        else:
            for x in qs:
                print(f"{x['id']} [{x['kind']}] on {x['target']}: {x['question']}")
            if not qs:
                print("Nothing to ask: no mistakes or discoveries found (or all already explained).")
        return 0
    try:
        ev = answer(a.session, a.target, a.question, a.text, a.author)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    print(f"saved {ev['id']} on {ev['target']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
