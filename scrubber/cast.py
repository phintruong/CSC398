"""Scrub asciinema recordings (asciicast v2 and v3).

The output stream ("o" events) and the input stream ("i" events, present
when recorded with ``--stdin`` / input capture) are each joined into one
string before detection. Keystrokes arrive one character per event and
output can break anywhere, so a secret is usually split across many events;
detecting on the joined stream and splitting back keeps event timing intact
while still removing the whole secret.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from .core import REDACTION_VERSION, PlaceholderMap, Scrubber
from .detect import PASSWORD, USER, Finding, strip_controls

# Output that ends with one of these means the next line typed is secret.
PASSWORD_PROMPT = re.compile(
    r"(?i)(?:pass ?(?:word|phrase|code)|passwd|\bpin\b|secret|token|one-time|\botp\b|verification code)"
    r"[^\n\r]{0,80}?[:?][ \t]*$"
)
USER_PROMPT = re.compile(r"(?i)(?:\busername|\blogin)[^\n\r]{0,80}?:[ \t]*$")

_ENTER = "\r\n"
_ABORT = "\x03\x04"  # Ctrl-C, Ctrl-D
_ERASE = "\x7f\x08"
_ESCAPE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|O.|.)")


@dataclass
class Recording:
    header: dict[str, Any]
    events: list[list[Any]]  # [time, code, data]
    comments: list[str] = field(default_factory=list)


def read_cast(text: str) -> Recording:
    lines = [ln for ln in text.splitlines() if ln.strip()]
    comments = [ln for ln in lines if ln.lstrip().startswith("#")]
    body = [ln for ln in lines if not ln.lstrip().startswith("#")]
    if not body:
        raise ValueError("empty recording")
    header = json.loads(body[0])
    if not isinstance(header, dict) or header.get("version") not in (2, 3):
        raise ValueError("not an asciicast v2/v3 file")
    return Recording(header, [json.loads(ln) for ln in body[1:]], comments)


def write_cast(rec: Recording) -> str:
    lines = [json.dumps(rec.header, ensure_ascii=False)]
    lines += [json.dumps(ev, ensure_ascii=False) for ev in rec.events]
    return "\n".join(lines) + "\n"


def typed_value(raw: str) -> str:
    """What the user actually entered, after applying backspaces."""
    out: list[str] = []
    for ch in _ESCAPE.sub("", raw):  # drop arrow keys etc., keep backspaces
        if ch in _ERASE:
            if out:
                out.pop()
        elif ch >= " ":
            out.append(ch)
    return "".join(out)


def prompted_input(events: list[list[Any]], input_offsets: dict[int, int], input_text: str) -> list[Finding]:
    """Find everything typed after a password (or username) prompt.

    Echo is usually off at a password prompt, so the secret only exists in
    the input stream and no pattern can recognise it. We watch the output
    for a prompt and take the next line of input, up to Enter.
    """
    found: list[Finding] = []
    tail = ""
    awaiting: str | None = None
    start: int | None = None
    kind = PASSWORD

    def close(end: int) -> None:
        nonlocal start
        raw = input_text[start:end]
        value = typed_value(raw)
        if value:
            found.append(Finding(start, end, kind, value, 0))
        start = None

    for i, ev in enumerate(events):
        code, data = ev[1], ev[2]
        if code == "o" and start is None:
            tail = (tail + strip_controls(data)[0])[-400:]
            awaiting = PASSWORD if PASSWORD_PROMPT.search(tail) else USER if USER_PROMPT.search(tail) else None
        elif code == "i":
            base = input_offsets[i]
            for j, ch in enumerate(data):
                pos = base + j
                if start is not None:
                    if ch in _ENTER or ch in _ABORT:
                        close(pos)
                        awaiting = None
                elif awaiting:
                    if ch in _ENTER or ch in _ABORT:
                        awaiting = None
                    else:
                        start, kind = pos, awaiting
    if start is not None:
        close(len(input_text))
    return found


def _join(events: list[list[Any]], code: str) -> tuple[str, list[int], list[tuple[int, int]], dict[int, int]]:
    parts, idx, bounds, offsets = [], [], [], {}
    pos = 0
    for i, ev in enumerate(events):
        if len(ev) >= 3 and ev[1] == code and isinstance(ev[2], str):
            idx.append(i)
            bounds.append((pos, pos + len(ev[2])))
            offsets[i] = pos
            parts.append(ev[2])
            pos += len(ev[2])
    return "".join(parts), idx, bounds, offsets


def scrub_recording(
    rec: Recording,
    *,
    extra_terms: tuple[str, ...] | list[str] = (),
    source: str | None = None,
    consent: str | None = None,
    license: str | None = None,
    mapping: PlaceholderMap | None = None,
) -> tuple[Recording, Scrubber]:
    """Return a scrubbed copy of ``rec`` and the scrubber used.

    The returned scrubber's ``map`` holds the originals; keep it in memory
    only (tests use it to check consistency).
    """
    scrubber = Scrubber(extra_terms, mapping)
    events = [list(ev) for ev in rec.events]

    out_text, out_idx, out_bounds, _ = _join(events, "o")
    in_text, in_idx, in_bounds, in_offsets = _join(events, "i")
    typed = prompted_input(events, in_offsets, in_text)

    header = json.loads(json.dumps(rec.header))
    header_keys = [k for k in ("title", "command") if isinstance(header.get(k), str)]
    marker_idx = [i for i, ev in enumerate(events) if len(ev) >= 3 and ev[1] == "m" and isinstance(ev[2], str)]

    texts = [out_text, in_text] + [header[k] for k in header_keys] + [events[i][2] for i in marker_idx]
    spans = scrubber.find_spans(texts, extra=[[], typed])

    for idx, bounds, text, sp in ((out_idx, out_bounds, out_text, spans[0]), (in_idx, in_bounds, in_text, spans[1])):
        for i, new in zip(idx, scrubber.rewrite(text, bounds, sp)):
            events[i][2] = new
    rest = spans[2:]
    for k, sp in zip(header_keys, rest[: len(header_keys)]):
        header[k] = scrubber.rewrite(header[k], [(0, len(header[k]))], sp)[0]
    for i, sp in zip(marker_idx, rest[len(header_keys) :]):
        events[i][2] = scrubber.rewrite(events[i][2], [(0, len(events[i][2]))], sp)[0]

    provenance = {k: v for k, v in (("source", source), ("consent", consent), ("license", license)) if v}
    if provenance:
        header["provenance"] = {**header.get("provenance", {}), **provenance}
    header["redaction"] = {"version": REDACTION_VERSION, "replaced": scrubber.map.counts()}

    return Recording(header, events), scrubber


def scrub_cast_text(text: str, **kwargs: Any) -> str:
    rec, _ = scrub_recording(read_cast(text), **kwargs)
    return write_cast(rec)
