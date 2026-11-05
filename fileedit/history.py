"""File history across sessions (KAN-31): what Group 6's ``get_file_history`` tool returns.

    from fileedit.history import file_history
    file_history(sessions, "/etc/nginx/sites-available/default")

``sessions`` is ``{session_id: (header, events)}`` (e.g. ``fixtures.load_sessions()``).
Paths match after normalising slashes, ``~`` and placeholder numbering, so
``/home/<USER_1>/app/.env`` in one session matches ``/home/<USER_3>/app/.env``
in another (placeholders are numbered per recording). A relative query
matches any recorded path ending with it.
"""

from __future__ import annotations

import re
from typing import Iterable

from contracts import evidence_id
from postmortem.core import apply_corrections

_PH = re.compile(r"<([A-Z_]+)_\d+>")


def normalize_path(p: str) -> str:
    p = p.replace("\\", "/")
    p = _PH.sub(lambda m: f"<{m.group(1)}>", p)
    p = re.sub(r"^~(?=/|$)", "/home/<USER>", p)
    return re.sub(r"/+", "/", p).rstrip("/")


def _matches(recorded: str, query: str) -> bool:
    r, q = normalize_path(recorded), normalize_path(query)
    return r == q if q.startswith("/") else (r == q or r.endswith("/" + q))


def file_history(sessions: dict[str, tuple[dict, list[dict]]], path: str, limit: int | None = None) -> list[dict]:
    """Every recorded change to ``path``, oldest first, each with its evidence id and why (if annotated)."""
    out = []
    for sid, (header, events) in sessions.items():
        evs = apply_corrections(events)
        anns = {e["target"]: e for e in evs if e.get("type") == "annotation"}
        for e in evs:
            if e.get("type") != "file_edit" or not _matches(e["path"], path):
                continue
            a = anns.get(e["id"], {})
            out.append({
                "evidence": evidence_id(sid, e["id"]),
                "session": sid,
                "started": header.get("started"),
                "t": e["t"],
                "path": e["path"],
                "change": e["change"],
                "tool": e["tool"],
                "command": e["command"],
                "is_config": e["is_config"],
                "diff": e.get("diff"),
                "binary": e["binary"],
                "truncated": e["truncated"],
                "why": a.get("summary"),
                "mistake": a.get("mistake"),
            })
    out.sort(key=lambda x: (x["started"] or "", x["session"], x["t"]))  # ties: keep each session together
    return out[-limit:] if limit else out


def edited_files(sessions: dict[str, tuple[dict, list[dict]]]) -> list[dict]:
    """Index of every file with recorded edits: normalised path, count, sessions, config flag."""
    idx: dict[str, dict] = {}
    for sid, (_, events) in sessions.items():
        for e in events:
            if e.get("type") == "file_edit":
                key = normalize_path(e["path"])
                row = idx.setdefault(key, {"path": key, "edits": 0, "sessions": [], "is_config": e["is_config"]})
                row["edits"] += 1
                if sid not in row["sessions"]:
                    row["sessions"].append(sid)
    return sorted(idx.values(), key=lambda r: (-r["edits"], r["path"]))


def iter_sessions_dir(directory) -> Iterable:
    from fixtures import load_sessions

    return load_sessions(directory)
