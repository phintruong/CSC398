"""Event schema v1 (KAN-102): the shared format every component reads and writes.

A session is one JSONL file, ``<name>.events.jsonl``:

* line 1 is the ``session`` header;
* every other line is one event: ``command``, ``resize``, ``file_edit``,
  ``annotation``, ``explanation`` or ``correction``.

Every event has an ``id`` that is unique inside its session. Downstream
outputs (tags, goal-tree nodes, answers, post-mortems, trajectories) cite
events by **evidence id** = ``"<session_id>#<event_id>"``.

Times are seconds from the start of the recording (the same clock as the
.cast file), so events can be merged with the recording by time.

This module has no dependencies; ``validate_event`` and ``validate_session``
are what the contract tests (KAN-108) call. Unknown extra fields are allowed
(forward compatible); missing or wrongly typed required fields are errors.
"""

from __future__ import annotations

import json
from typing import Any, Iterable, Iterator

SCHEMA_VERSION = "events-v1"

# field -> (accepted types, required)
_NUM = (int, float)
_OPT_STR = (str, type(None))
_OPT_NUM = (int, float, type(None))
_OPT_INT = (int, type(None))

_TERM = {"cols": ((int,), True), "rows": ((int,), True)}

SESSION_FIELDS: dict[str, tuple[tuple[type, ...], bool]] = {
    "type": ((str,), True),
    "schema": ((str,), True),
    "id": ((str,), True),
    "origin": ((str,), True),  # "live" | "import" | "mock"
    "started": (_OPT_STR, False),  # ISO 8601
    "shell": (_OPT_STR, False),
    "term": ((dict, type(None)), False),
    "provenance": ((dict,), True),  # source, consent, license
    "redaction": ((dict,), True),  # version, replaced counts; absent => never store
}

EVENT_FIELDS: dict[str, dict[str, tuple[tuple[type, ...], bool]]] = {
    "command": {
        "t": (_NUM, True),
        "t_end": (_OPT_NUM, True),
        "duration": (_OPT_NUM, True),
        "command": ((str,), True),
        "cwd": (_OPT_STR, True),
        "exit_code": (_OPT_INT, True),  # None when unknown (imported casts)
        "output": (_OPT_STR, True),  # ANSI-stripped, trimmed
        "output_truncated": ((bool,), False),
        "term": ((dict, type(None)), False),
    },
    "resize": {
        "t": (_NUM, True),
        "cols": ((int,), True),
        "rows": ((int,), True),
    },
    "file_edit": {
        "t": (_NUM, True),
        "t_end": (_OPT_NUM, True),
        "path": ((str,), True),
        "tool": ((str,), True),
        "command": ((str,), True),
        "command_id": (_OPT_STR, False),
        "change": ((str,), True),  # created | modified | deleted
        "is_config": ((bool,), True),
        "diff": (_OPT_STR, True),  # None when binary or truncated
        "binary": ((bool,), True),
        "truncated": ((bool,), True),
        "redaction": ((str,), True),
    },
    # Model 1 / tagging output for one event (Group 2 writes these).
    "annotation": {
        "target": ((str,), True),  # event id being annotated
        "goal": (_OPT_STR, False),
        "tool": (_OPT_STR, False),
        "result": (_OPT_STR, False),  # success | failure | partial | unknown
        "mistake": ((bool, type(None)), False),
        "summary": (_OPT_STR, False),
        "parent": (_OPT_STR, False),  # goal-tree parent annotation id
        "model": (_OPT_STR, False),
    },
    # The person's own answer to "why?" about an event (KAN-58/59): typed now, Whisper transcripts later.
    "explanation": {
        "target": ((str,), True),  # event id the question was about
        "question": ((str,), True),
        "answer": ((str,), True),  # scrubbed before it is stored
        "source": ((str,), True),  # typed | voice
        "author": ((str,), True),
        "t_created": (_OPT_STR, False),
        "confidence": (_OPT_NUM, False),  # transcription confidence for voice answers
    },
    # A human fixing a model output (KAN-103). Never edits in place.
    "correction": {
        "target": ((str,), True),  # annotation id being corrected
        "field": ((str,), True),
        "old": ((object,), False),
        "new": ((object,), True),
        "author": ((str,), True),
        "t_created": (_OPT_STR, False),
    },
}

EVENT_TYPES = frozenset(EVENT_FIELDS)
CHANGES = frozenset({"created", "modified", "deleted"})
RESULTS = frozenset({"success", "failure", "partial", "unknown"})
ORIGINS = frozenset({"live", "import", "mock"})


def evidence_id(session_id: str, event_id: str) -> str:
    return f"{session_id}#{event_id}"


def parse_evidence_id(eid: str) -> tuple[str, str]:
    session, sep, event = eid.rpartition("#")
    if not sep or not session or not event:
        raise ValueError(f"not an evidence id: {eid!r}")
    return session, event


def _check(obj: dict, fields: dict[str, tuple[tuple[type, ...], bool]], where: str) -> list[str]:
    errs = []
    for name, (types, required) in fields.items():
        if name not in obj:
            if required:
                errs.append(f"{where}: missing field {name!r}")
            continue
        v = obj[name]
        if object in types:
            continue
        # bool is an int subclass; don't let True pass as a number
        if isinstance(v, bool) and bool not in types:
            errs.append(f"{where}: {name!r} must not be a bool")
        elif not isinstance(v, types):
            errs.append(f"{where}: {name!r} has type {type(v).__name__}")
    return errs


def _check_term(term: Any, where: str) -> list[str]:
    if term is None:
        return []
    return _check(term, _TERM, where + ".term")


def validate_session_header(h: Any) -> list[str]:
    if not isinstance(h, dict):
        return ["header: not an object"]
    errs = _check(h, SESSION_FIELDS, "header")
    if errs:
        return errs
    if h["type"] != "session":
        errs.append("header: type must be 'session'")
    if h["schema"] != SCHEMA_VERSION:
        errs.append(f"header: schema {h['schema']!r} != {SCHEMA_VERSION!r}")
    if h["origin"] not in ORIGINS:
        errs.append(f"header: origin {h['origin']!r} not in {sorted(ORIGINS)}")
    if "version" not in h["redaction"]:
        errs.append("header: redaction.version missing (recording was not scrubbed)")
    errs += _check_term(h.get("term"), "header")
    return errs


def validate_event(ev: Any) -> list[str]:
    if not isinstance(ev, dict):
        return ["event: not an object"]
    kind = ev.get("type")
    if not isinstance(kind, str) or kind not in EVENT_TYPES:
        return [f"event: unknown type {kind!r}"]
    where = f"{kind} {ev.get('id', '?')}"
    errs = _check(ev, {"id": ((str,), True)}, where) + _check(ev, EVENT_FIELDS[kind], where)
    if errs:
        return errs
    if "t" in ev and ev["t"] < 0:
        errs.append(f"{where}: t is negative")
    if ev.get("t_end") is not None and ev["t_end"] < ev["t"]:
        errs.append(f"{where}: t_end before t")
    if kind == "file_edit":
        if ev["change"] not in CHANGES:
            errs.append(f"{where}: change {ev['change']!r} not in {sorted(CHANGES)}")
        if ev["diff"] is None and not (ev["binary"] or ev["truncated"]):
            errs.append(f"{where}: diff is null but file is neither binary nor truncated")
    if kind == "annotation" and ev.get("result") is not None and ev["result"] not in RESULTS:
        errs.append(f"{where}: result {ev['result']!r} not in {sorted(RESULTS)}")
    if kind == "resize" and (ev["cols"] <= 0 or ev["rows"] <= 0):
        errs.append(f"{where}: terminal size must be positive")
    errs += _check_term(ev.get("term"), where)
    return errs


def validate_session(header: Any, events: Iterable[Any]) -> list[str]:
    """Header + events, plus cross-event rules (unique ids, valid references)."""
    errs = validate_session_header(header)
    events = list(events)
    seen: set[str] = set()
    for ev in events:
        errs += validate_event(ev)
        eid = ev.get("id") if isinstance(ev, dict) else None
        if isinstance(eid, str):
            if eid in seen:
                errs.append(f"duplicate event id {eid!r}")
            seen.add(eid)
    for ev in events:
        if not isinstance(ev, dict):
            continue
        for ref in ("target", "command_id", "parent"):
            r = ev.get(ref)
            if isinstance(r, str) and r not in seen:
                errs.append(f"{ev.get('type')} {ev.get('id')}: {ref} {r!r} does not exist")
    return errs


# -- reading and writing ----------------------------------------------------


def dumps_session(header: dict, events: Iterable[dict]) -> str:
    lines = [json.dumps(header, ensure_ascii=False)]
    lines += [json.dumps(e, ensure_ascii=False) for e in events]
    return "\n".join(lines) + "\n"


def iter_lines(text: str) -> Iterator[tuple[int, Any]]:
    """Yield (line_no, obj) for each non-blank line; obj is None if the line is broken."""
    for n, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            yield n, json.loads(line)
        except json.JSONDecodeError:
            yield n, None


def loads_session(text: str, *, strict: bool = True) -> tuple[dict, list[dict]]:
    """Parse a session file. With ``strict=False`` broken lines are skipped
    (and counted in ``header["_skipped_lines"]``) instead of raising."""
    items = list(iter_lines(text))
    if not items or not isinstance(items[0][1], dict):
        raise ValueError("session file has no header line")
    header = items[0][1]
    events, skipped = [], 0
    for n, obj in items[1:]:
        if not isinstance(obj, dict):
            if strict:
                raise ValueError(f"line {n}: not a JSON object")
            skipped += 1
            continue
        events.append(obj)
    if skipped:
        header = {**header, "_skipped_lines": skipped}
    return header, events
