"""Human review and correction of tags (KAN-39).

Corrections are **appended** as ``correction`` events; nothing is edited in
place (docs/design/training-records.md section 1). Works on finished
sessions and on a daemon's ``live.events.jsonl`` while it is still growing.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path
from typing import Any

from contracts import loads_session, validate_event
from postmortem.core import apply_corrections

FIELDS = ("goal", "tool", "result", "mistake", "summary", "parent", "reviewed")
RESULTS = ("success", "failure", "partial", "unknown")
_AUTHOR = re.compile(r"^[A-Za-z0-9_.-]{1,40}$")


class ReviewError(ValueError):
    pass


def load(path: Path) -> tuple[dict, list[dict]]:
    return loads_session(Path(path).read_text(encoding="utf-8"), strict=False)


def coerce(field: str, value: Any, events: list[dict]) -> Any:
    """Turn CLI text into the right type for the field, or raise ReviewError."""
    if field not in FIELDS:
        raise ReviewError(f"field must be one of {', '.join(FIELDS)}")
    if field in ("mistake", "reviewed"):
        if isinstance(value, bool):
            return value
        v = str(value).strip().lower()
        if v in ("true", "yes", "y", "1"):
            return True
        if v in ("false", "no", "n", "0"):
            return False
        raise ReviewError(f"{field} must be true or false")
    if field == "result":
        if value not in RESULTS:
            raise ReviewError(f"result must be one of {', '.join(RESULTS)}")
        return value
    if field == "parent":
        if value in (None, "", "none", "null"):
            return None
        if value not in {e["id"] for e in events if e.get("type") == "annotation"}:
            raise ReviewError(f"parent {value!r} is not an annotation in this session")
        return value
    v = str(value).strip()
    if not v:
        raise ReviewError(f"{field} can't be empty")
    return v


def rows(events: list[dict], *, unreviewed: bool = False, mistakes: bool = False) -> list[dict]:
    """Annotations with their (corrected) labels and the event they describe."""
    corrected = apply_corrections(events)
    by_id = {e["id"]: e for e in corrected}
    out = []
    for a in corrected:
        if a.get("type") != "annotation":
            continue
        if unreviewed and a.get("reviewed"):
            continue
        if mistakes and not (a.get("mistake") or a.get("result") == "failure"):
            continue
        tgt = by_id.get(a["target"], {})
        what = tgt.get("command") if tgt.get("type") == "command" else f"{tgt.get('change', '')} {tgt.get('path', '')}".strip()
        out.append({"id": a["id"], "target": a["target"], "event": what, "goal": a.get("goal"), "tool": a.get("tool"),
                    "result": a.get("result"), "mistake": a.get("mistake"), "summary": a.get("summary"),
                    "parent": a.get("parent"), "reviewed": bool(a.get("reviewed")),
                    "changed": a.get("changed_fields", [])})
    return out


def _next_id(events: list[dict]) -> str:
    used = {e.get("id") for e in events}
    n = sum(1 for e in events if e.get("type") == "correction") + 1
    while f"x{n}" in used:
        n += 1
    return f"x{n}"


def make_corrections(path: Path, annotation_id: str, changes: dict[str, Any], author: str,
                     now: str | None = None) -> list[dict]:
    """Append one correction per field. Returns the events written."""
    if not _AUTHOR.match(author or ""):
        raise ReviewError("author must be a short handle (letters, digits, _ . -), not a full name")
    _, events = load(path)
    current = {a["id"]: a for a in apply_corrections(events) if a.get("type") == "annotation"}
    if annotation_id not in current:
        raise ReviewError(f"{annotation_id!r} is not an annotation in this session")
    stamp = now or dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    written = []
    all_events = list(events)
    for field, raw in changes.items():
        new = coerce(field, raw, events)
        ev = {"type": "correction", "id": _next_id(all_events), "target": annotation_id, "field": field,
              "old": current[annotation_id].get(field), "new": new, "author": author, "t_created": stamp}
        errs = validate_event(ev)
        if errs:
            raise ReviewError("; ".join(errs))
        all_events.append(ev)
        written.append(ev)
    with Path(path).open("a", encoding="utf-8") as fh:
        for ev in written:
            fh.write(json.dumps(ev, ensure_ascii=False) + "\n")
    return written


def accept(path: Path, annotation_id: str, author: str, now: str | None = None) -> list[dict]:
    """A person checked the labels and agrees: recorded, not silently assumed."""
    return make_corrections(path, annotation_id, {"reviewed": True}, author, now)
