"""Turning raw asciicast output into readable text with per-character times."""

from __future__ import annotations

import re
from typing import Any, Sequence

_ESC = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\)?|[@-Z\\-_]|.)", re.S)

OUTPUT_HEAD = 1000
OUTPUT_TAIL = 3000


def render(events: Sequence[Sequence[Any]], code: str = "o") -> tuple[str, list[float]]:
    """A light terminal: drop escape sequences, apply backspace and carriage
    return (overwrite back to the start of the line). Returns (text, times)
    where times[i] is the time of the event that produced text[i]."""
    out: list[str] = []
    times: list[float] = []
    line_start = 0
    pending_cr = False
    for ev in events:
        if len(ev) < 3 or ev[1] != code or not isinstance(ev[2], str):
            continue
        t = float(ev[0])
        data = _ESC.sub("", ev[2])
        for ch in data:
            if pending_cr and ch != "\n":
                del out[line_start:]
                del times[line_start:]
            pending_cr = False
            if ch == "\r":
                pending_cr = True
            elif ch == "\n":
                out.append("\n")
                times.append(t)
                line_start = len(out)
            elif ch in "\x08\x7f":
                if len(out) > line_start:
                    out.pop()
                    times.pop()
            elif ch == "\t" or ch >= " ":
                out.append(ch)
                times.append(t)
    return "".join(out), times


def trim_output(text: str) -> tuple[str, bool]:
    text = text.strip("\n")
    if len(text) <= OUTPUT_HEAD + OUTPUT_TAIL:
        return text, False
    cut = len(text) - OUTPUT_HEAD - OUTPUT_TAIL
    return f"{text[:OUTPUT_HEAD]}\n[... {cut} characters trimmed ...]\n{text[-OUTPUT_TAIL:]}", True


def output_between(events: Sequence[Sequence[Any]], t0: float, t1: float | None) -> tuple[str, bool]:
    """Output printed after t0 and up to t1 (absolute times), rendered and trimmed."""
    window = [ev for ev in events if len(ev) >= 3 and ev[0] > t0 and (t1 is None or ev[0] <= t1)]
    text, _ = render(window)
    return trim_output(text)


def absolute_times(header: dict, events: list[list[Any]]) -> list[list[Any]]:
    """asciicast v3 stores intervals; v2 stores absolute times. Return absolute."""
    if header.get("version") != 3:
        return [list(ev) for ev in events]
    t = 0.0
    out = []
    for ev in events:
        t += float(ev[0])
        out.append([round(t, 6), *ev[1:]])
    return out


def term_size(header: dict) -> dict[str, int] | None:
    term = header.get("term")
    if isinstance(term, dict) and isinstance(term.get("cols"), int) and isinstance(term.get("rows"), int):
        return {"cols": term["cols"], "rows": term["rows"]}
    if isinstance(header.get("width"), int) and isinstance(header.get("height"), int):
        return {"cols": header["width"], "rows": header["height"]}
    return None


def parse_resize(data: Any) -> dict[str, int] | None:
    m = re.fullmatch(r"\s*(\d+)\s*x\s*(\d+)\s*", data) if isinstance(data, str) else None
    if not m or int(m.group(1)) <= 0 or int(m.group(2)) <= 0:
        return None
    return {"cols": int(m.group(1)), "rows": int(m.group(2))}
