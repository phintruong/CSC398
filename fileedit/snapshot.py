"""Before/after snapshots and file_edit events (design section 4 and 5)."""

from __future__ import annotations

import difflib
import hashlib
import os
from dataclasses import asdict, dataclass
from typing import Any, Iterable, Sequence

from .detect import is_config

MAX_BYTES = 1024 * 1024  # 1 MiB
_SNIFF = 8192


@dataclass(frozen=True)
class FileState:
    path: str  # as targeted (absolute, not resolved)
    exists: bool
    digest: str | None = None
    binary: bool = False
    truncated: bool = False
    text: str | None = None  # only for small text files

    @classmethod
    def read(cls, path: str) -> "FileState | None":
        """None when the path is something we don't track (a directory, a device)."""
        real = os.path.realpath(path)
        if not os.path.lexists(real):
            return cls(path, False)
        if not os.path.isfile(real):
            return None
        try:
            size = os.path.getsize(real)
            with open(real, "rb") as fh:
                if size > MAX_BYTES:
                    h = hashlib.sha256()
                    for chunk in iter(lambda: fh.read(1 << 16), b""):
                        h.update(chunk)
                    return cls(path, True, h.hexdigest(), truncated=True)
                data = fh.read()
        except OSError:
            return None
        digest = hashlib.sha256(data).hexdigest()
        if b"\x00" in data[:_SNIFF]:
            return cls(path, True, digest, binary=True)
        return cls(path, True, digest, text=data.decode("utf-8", errors="replace"))

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> "FileState":
        return cls(**d)


def _diff_path(path: str) -> str:
    return path.replace("\\", "/").lstrip("/")


def unified_diff(path: str, before: str, after: str) -> str:
    p = _diff_path(path)
    lines = difflib.unified_diff(
        before.splitlines(), after.splitlines(), fromfile="a/" + p, tofile="b/" + p, lineterm="", n=3
    )
    return "\n".join(lines) + "\n"


class Snapshot:
    def __init__(self, states: dict[str, FileState], tools: dict[str, str]) -> None:
        self.states = states
        self.tools = tools

    @classmethod
    def take(cls, targets: Iterable[str | tuple[str, str]]) -> "Snapshot":
        states, tools = {}, {}
        for t in targets:
            path, tool = (t, "unknown") if isinstance(t, str) else t
            st = FileState.read(path)
            if st is not None:
                states[path] = st
                tools[path] = tool
        return cls(states, tools)

    @property
    def paths(self) -> list[str]:
        return list(self.states)

    def to_json(self) -> dict[str, Any]:
        return {"states": {p: s.to_json() for p, s in self.states.items()}, "tools": self.tools}

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> "Snapshot":
        return cls({p: FileState.from_json(s) for p, s in d["states"].items()}, dict(d["tools"]))

    def diff(
        self,
        after: "Snapshot",
        *,
        t: float,
        t_end: float | None,
        command: str,
        scrubber: Any = None,
        raw: bool = False,
        command_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """file_edit events for every target that changed between self and ``after``.

        Unless ``raw`` is true, paths, the command and diffs are scrubbed
        (with ``scrubber`` if given, so placeholders match the recording)."""
        events = []
        for path, b in self.states.items():
            a = after.states.get(path)
            if a is None:
                continue
            if not b.exists and not a.exists:
                continue
            if b.exists and a.exists and b.digest == a.digest:
                continue
            change = "created" if not b.exists else "deleted" if not a.exists else "modified"
            binary = b.binary or a.binary
            truncated = b.truncated or a.truncated
            diff = None
            if not (binary or truncated):
                diff = unified_diff(path, b.text or "", a.text or "")
            ev: dict[str, Any] = {
                "type": "file_edit",
                "t": t,
                "t_end": t_end,
                "path": path,
                "tool": self.tools.get(path, "unknown"),
                "command": command,
                "change": change,
                "is_config": is_config(path),
                "diff": diff,
                "binary": binary,
                "truncated": truncated,
            }
            if command_id is not None:
                ev["command_id"] = command_id
            events.append(ev)
        if raw:
            for ev in events:
                ev["redaction"] = "pending"
            return events
        return scrub_edits(events, scrubber)


def scrub_edits(events: Sequence[dict[str, Any]], scrubber: Any = None) -> list[dict[str, Any]]:
    """Scrub path/command/diff of file_edit events with one shared scrubber."""
    from scrubber import REDACTION_VERSION, Scrubber

    s = scrubber or Scrubber()
    out = []
    for ev in events:
        keys = [k for k in ("path", "command", "diff") if isinstance(ev.get(k), str)]
        clean = s.scrub_texts([ev[k] for k in keys])
        ev = {**ev, **dict(zip(keys, clean)), "redaction": REDACTION_VERSION}
        out.append(ev)
    return out
