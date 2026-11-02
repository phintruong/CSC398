"""python -m scrubber in.cast -o out.cast [--source S] [--consent C] [--license L] [--term NAME ...]

Prints only counts per kind, never the original values.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .cast import read_cast, scrub_recording, write_cast


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m scrubber", description="Scrub secrets and PII from an asciinema recording.")
    p.add_argument("input", type=Path)
    p.add_argument("-o", "--output", type=Path, required=True)
    p.add_argument("--source")
    p.add_argument("--consent")
    p.add_argument("--license")
    p.add_argument("--term", action="append", default=[], help="extra name to scrub everywhere, e.g. a customer name (repeatable)")
    a = p.parse_args(argv)

    if a.output.resolve() == a.input.resolve():
        p.error("output must differ from input")
    rec, scrubber = scrub_recording(
        read_cast(a.input.read_text(encoding="utf-8")),
        extra_terms=a.term,
        source=a.source,
        consent=a.consent,
        license=a.license,
    )
    a.output.write_text(write_cast(rec), encoding="utf-8")
    print(json.dumps({"output": str(a.output), "replaced": scrubber.map.counts()}), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
