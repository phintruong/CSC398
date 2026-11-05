"""Import old asciinema recordings into event schema v1 (KAN-26).

Old DocStream recordings have no shell hooks, so commands are recovered from
the screen: a line that starts with a shell prompt is a command, and the
output runs until the next prompt. Exit codes are unknown (``null``).

The recording is **scrubbed first** (KAN-35); segmentation only ever sees
scrubbed text, so nothing unscrubbed can reach the events file.
Broken lines (bad JSON, wrong shape) are skipped and counted, not fatal.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Sequence

from contracts import SCHEMA_VERSION
from scrubber import Recording, Scrubber, scrub_recording
from scrubber.cast import write_cast

from .text import absolute_times, parse_resize, render, term_size, trim_output

# Accepted prompt shapes (the prompt group must end with "$ ", "# ", "% " or "> " after ➜):
#   user@host:~/dir$      [user@host dir]$     (venv) user@host:~$     $     #     ➜  dir
_PROMPTS = [
    re.compile(r"^(?P<p>(?:\([^)\n]{1,40}\) )?[^\s@]{1,40}@[^\s:]{1,80}:(?P<cwd>[~/][^\n$#%]{0,200}?)[$#%] )"),
    re.compile(r"^(?P<p>(?:\([^)\n]{1,40}\) )?\[[^\s@\]]{1,40}@[^\s\]]{1,80} (?P<cwd>[^\]\n]{1,200})\][$#%] )"),
    re.compile(r"^(?P<p>(?:\([^)\n]{1,40}\) )?(?P<cwd>[~/][^\s$#%]{0,200}) ?[$#%] )"),
    re.compile(r"^(?P<p>➜ {1,2}(?P<cwd>\S{1,200}) (?:git:\([^)]*\) )?(?:✗ )?)"),
    re.compile(r"^(?P<p>(?:\([^)\n]{1,40}\) )?[$#%] )"),
]


def _match_prompt(line: str, custom: re.Pattern | None) -> re.Match | None:
    for pat in ([custom] if custom else []) + _PROMPTS:
        m = pat.match(line)
        if m:
            return m
    return None


def load_cast_tolerant(text: str) -> tuple[dict, list[list[Any]], int]:
    """(header, absolute-time events, skipped line count). Raises only if no header."""
    header: dict | None = None
    events: list[list[Any]] = []
    skipped = 0
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        try:
            obj = json.loads(s)
        except json.JSONDecodeError:
            skipped += 1
            continue
        if header is None:
            if isinstance(obj, dict) and obj.get("version") in (2, 3):
                header = obj
            else:
                skipped += 1
            continue
        ok = (
            isinstance(obj, list)
            and len(obj) >= 3
            and isinstance(obj[0], (int, float))
            and not isinstance(obj[0], bool)
            and isinstance(obj[1], str)
        )
        if ok:
            events.append(obj)
        else:
            skipped += 1
    if header is None:
        raise ValueError("no asciicast v2/v3 header found")
    events = absolute_times(header, events)
    # out-of-order times (a broken recorder) would break everything downstream
    last = 0.0
    for ev in events:
        if ev[0] < last:
            ev[0] = last
        last = ev[0]
    return header, events, skipped


def segment(
    events: Sequence[Sequence[Any]],
    *,
    initial_term: dict | None,
    prompt_regex: str | None = None,
    id_prefix: str = "",
) -> list[dict[str, Any]]:
    """Recover command and resize events from (already scrubbed) output."""
    custom = re.compile(prompt_regex) if prompt_regex else None
    text, times = render(events)
    end_time = float(events[-1][0]) if events else 0.0

    # terminal size over time
    sizes: list[tuple[float, dict]] = [(0.0, initial_term)] if initial_term else []
    out: list[dict[str, Any]] = []
    n_resize = 0
    for ev in events:
        if len(ev) >= 3 and ev[1] == "r":
            size = parse_resize(ev[2])
            if size:
                n_resize += 1
                sizes.append((float(ev[0]), size))
                out.append({"type": "resize", "id": f"{id_prefix}r{n_resize}", "t": float(ev[0]), **size})

    def size_at(t: float) -> dict | None:
        cur = None
        for st, sz in sizes:
            if st <= t:
                cur = sz
        return cur

    # prompts: (line_start, cmd_end (index of "\n"), prompt match)
    found = []
    pos = 0
    for line in text.split("\n"):
        m = _match_prompt(line, custom)
        if m:
            found.append((pos, pos + len(line), m, line))
        pos += len(line) + 1

    n_cmd = 0
    for k, (start, nl, m, line) in enumerate(found):
        cmd = line[m.end("p") :].strip()
        if not cmd:
            continue
        nxt = found[k + 1][0] if k + 1 < len(found) else len(text)
        t = times[nl] if nl < len(times) else (times[-1] if times else 0.0)
        t_end = times[nxt] if nxt < len(times) else end_time
        t_end = max(t_end, t)
        output, truncated = trim_output(text[nl + 1 : nxt])
        cwd = m.groupdict().get("cwd")
        n_cmd += 1
        out.append(
            {
                "type": "command",
                "id": f"{id_prefix}c{n_cmd}",
                "t": round(t, 6),
                "t_end": round(t_end, 6),
                "duration": round(t_end - t, 6),
                "command": cmd,
                "cwd": cwd.strip() if cwd else None,
                "exit_code": None,
                "output": output,
                "output_truncated": truncated,
                "term": size_at(t),
            }
        )
    out.sort(key=lambda e: (e["t"], e["type"] != "resize"))
    return out


def session_id_for(text: str) -> str:
    return "imp-" + hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:12]


def import_cast(
    text: str,
    *,
    source: str | None = None,
    consent: str | None = None,
    license: str | None = None,
    extra_terms: Sequence[str] = (),
    prompt_regex: str | None = None,
    session_id: str | None = None,
) -> tuple[dict, list[dict], str, Scrubber]:
    """Return (session header, events, scrubbed .cast text, scrubber).

    The scrubber's map holds originals: keep it in memory only."""
    header, events, skipped = load_cast_tolerant(text)
    hdr_for_scrub = {**header, "version": 2}  # times are absolute now
    rec, scrubber = scrub_recording(
        Recording(hdr_for_scrub, events), extra_terms=list(extra_terms), source=source, consent=consent, license=license
    )
    clean_header = {**rec.header, "version": header.get("version", 2)}
    if header.get("version") == 3:
        # write v3 back with intervals
        prev = 0.0
        rel = []
        for ev in rec.events:
            rel.append([round(ev[0] - prev, 6), *ev[1:]])
            prev = ev[0]
        cast_text = write_cast(Recording(clean_header, rel))
    else:
        cast_text = write_cast(Recording(clean_header, rec.events))

    sid = session_id or session_id_for(text)
    evs = segment(rec.events, initial_term=term_size(header), prompt_regex=prompt_regex)
    sess = {
        "type": "session",
        "schema": SCHEMA_VERSION,
        "id": sid,
        "origin": "import",
        "started": None,
        "shell": None,
        "term": term_size(header),
        "provenance": rec.header.get("provenance", {}),
        "redaction": rec.header["redaction"],
    }
    if isinstance(header.get("timestamp"), (int, float)):
        import datetime as _dt

        sess["started"] = _dt.datetime.fromtimestamp(header["timestamp"], _dt.timezone.utc).isoformat()
    if skipped:
        sess["import"] = {"skipped_lines": skipped}
    return sess, evs, cast_text, scrubber
