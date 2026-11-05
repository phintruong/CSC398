"""Live capture session (KAN-26) with scrubbing at stop time (KAN-35).

The shell hooks call ``preexec`` before each command and ``precmd`` after
it. Each hook is a separate short-lived process, so state lives in a
private spool directory (``<dir>/.spool``, mode 0700) between calls. The
spool holds raw, unscrubbed data (commands, file snapshots, the raw .cast),
so:

* it is never read by anything except ``stop``;
* ``stop`` scrubs everything with **one** placeholder map (cast, commands,
  outputs, diffs agree on ``<HOST_1>``), writes the scrubbed files, and
  deletes the spool;
* nothing outside the spool is written before scrubbing.

Files written by ``stop``:  ``session.events.jsonl`` (schema v1) and, when a
.cast was recorded, ``session.cast`` (scrubbed).
"""

from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import stat
import time
import uuid
from pathlib import Path
from typing import Any, Sequence

from contracts import SCHEMA_VERSION, dumps_session
from fileedit import Snapshot, targets_for_command
from fileedit.snapshot import scrub_edits
from scrubber import Scrubber, read_cast, scrub_recording, write_cast

from .text import output_between, parse_resize, term_size

SUSPENDED_EXIT = 148  # 128 + SIGTSTP: Ctrl-Z on an editor
SPOOL = ".spool"
STATE = "state.json"
RAW_CAST = "raw.cast"
EVENTS_FILE = "session.events.jsonl"
CAST_FILE = "session.cast"


def _private_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(p, stat.S_IRWXU)
    except OSError:
        pass


class Session:
    def __init__(self, directory: str | os.PathLike, state: dict[str, Any], memory: bool = False) -> None:
        self.dir = Path(directory)
        self.state = state
        self.memory = memory  # True inside the capture daemon: state never touches disk

    # -- lifecycle ------------------------------------------------------------

    @property
    def spool(self) -> Path:
        return self.dir / SPOOL

    @property
    def raw_cast_path(self) -> Path:
        return self.spool / RAW_CAST

    @classmethod
    def start(
        cls,
        directory: str | os.PathLike,
        *,
        source: str,
        consent: str,
        license: str,
        extra_terms: Sequence[str] = (),
        shell: str | None = None,
        cols: int | None = None,
        rows: int | None = None,
        now: float | None = None,
        memory: bool = False,
    ) -> "Session":
        d = Path(directory)
        if (d / SPOOL / STATE).exists() or (d / SPOOL / "daemon.json").exists():
            raise FileExistsError(f"a capture is already running in {d}")
        _private_dir(d / SPOOL)
        started = time.time() if now is None else now
        state = {
            "id": "live-" + uuid.uuid4().hex[:12],
            "started_epoch": started,
            "shell": shell,
            "term": {"cols": cols, "rows": rows} if cols and rows else None,
            "start_term": {"cols": cols, "rows": rows} if cols and rows else None,
            "provenance": {"source": source, "consent": consent, "license": license},
            "extra_terms": list(extra_terms),
            "n": 0,
            "pending": None,
            "suspended": [],
            "known_jobs": [],
            "events": [],
        }
        s = cls(d, state, memory)
        s._save()
        return s

    @classmethod
    def load(cls, directory: str | os.PathLike) -> "Session":
        d = Path(directory)
        path = d / SPOOL / STATE
        if not path.exists():
            raise FileNotFoundError(f"no running capture in {d}")
        return cls(d, json.loads(path.read_text(encoding="utf-8")))

    def _save(self) -> None:
        if self.memory:
            return
        tmp = self.spool / (STATE + ".tmp")
        tmp.write_text(json.dumps(self.state), encoding="utf-8")
        os.replace(tmp, self.spool / STATE)

    def _now(self, t: float | None) -> float:
        if t is not None:
            return t
        return round(time.time() - self.state["started_epoch"], 6)

    def _next_id(self, prefix: str) -> str:
        self.state["n"] += 1
        return f"{prefix}{self.state['n']}"

    # -- hooks ----------------------------------------------------------------

    def preexec(self, command: str, cwd: str, t: float | None = None) -> None:
        command = command.strip()
        if not command:
            return
        targets = targets_for_command(command, cwd)
        self.state["pending"] = {
            "id": self._next_id("c"),
            "command": command,
            "cwd": cwd,
            "t": self._now(t),
            "before": Snapshot.take(targets).to_json(),
        }
        self._save()

    def precmd(
        self,
        exit_code: int,
        t: float | None = None,
        *,
        jobs: Sequence[str] = (),
        cols: int | None = None,
        rows: int | None = None,
    ) -> None:
        now = self._now(t)
        jobs = [j for j in jobs if j]
        if cols and rows and self.state["term"] != {"cols": cols, "rows": rows}:
            if self.state["term"] is not None:
                self.state["events"].append(
                    {"type": "resize", "id": self._next_id("r"), "t": now, "cols": cols, "rows": rows}
                )
            self.state["term"] = {"cols": cols, "rows": rows}
            self.state["start_term"] = self.state.get("start_term") or self.state["term"]

        # suspended editors whose job has now finished (B11: vim, ^Z, ls, fg, :wq)
        still = []
        for s in self.state["suspended"]:
            if s["job"] in jobs:
                still.append(s)
            else:
                self._finish_edits(s["pending"], now)
        self.state["suspended"] = still

        p = self.state["pending"]
        if p is not None:
            self.state["events"].append(
                {
                    "type": "command",
                    "id": p["id"],
                    "t": p["t"],
                    "t_end": now,
                    "duration": round(now - p["t"], 6),
                    "command": p["command"],
                    "cwd": p["cwd"],
                    "exit_code": int(exit_code),
                    "output": None,
                    "term": self.state["term"],
                }
            )
            new_jobs = [j for j in jobs if j not in self.state["known_jobs"]]
            if exit_code == SUSPENDED_EXIT and new_jobs and p["before"]["states"]:
                self.state["suspended"].append({"job": new_jobs[-1], "pending": p})
            else:
                self._finish_edits(p, now)
            self.state["pending"] = None
        self.state["known_jobs"] = list(jobs)
        self._save()

    def _finish_edits(self, p: dict[str, Any], t_end: float) -> None:
        before = Snapshot.from_json(p["before"])
        if not before.states:
            return
        after = Snapshot.take(list(before.tools.items()))
        for ev in before.diff(after, t=p["t"], t_end=t_end, command=p["command"], raw=True, command_id=p["id"]):
            ev["id"] = self._next_id("f")
            self.state["events"].append(ev)

    # -- stop: scrub everything, write, delete the spool ------------------------

    def stop(self, cast_path: str | os.PathLike | None = None, t: float | None = None, scrubber: Scrubber | None = None) -> Path:
        now = self._now(t)
        for s in self.state["suspended"]:
            self._finish_edits(s["pending"], now)
        self.state["suspended"] = []
        if self.state["pending"] is not None:  # shell exited mid-command
            p = self.state["pending"]
            self.state["events"].append(
                {"type": "command", "id": p["id"], "t": p["t"], "t_end": None, "duration": None,
                 "command": p["command"], "cwd": p["cwd"], "exit_code": None, "output": None,
                 "term": self.state["term"]}
            )
            self.state["pending"] = None

        cast = Path(cast_path) if cast_path else (self.raw_cast_path if self.raw_cast_path.exists() else None)
        scrubber = scrubber or Scrubber(self.state["extra_terms"])
        prov = self.state["provenance"]
        events = [dict(e) for e in self.state["events"]]
        start_term = None

        if cast is not None:
            rec = read_cast(cast.read_text(encoding="utf-8"))
            from .text import absolute_times

            rec.events = absolute_times(rec.header, rec.events)
            version = rec.header.get("version", 2)
            rec.header = {**rec.header, "version": 2}
            clean, scrubber = scrub_recording(
                rec, extra_terms=self.state["extra_terms"], mapping=scrubber.map,
                source=prov["source"], consent=prov["consent"], license=prov["license"],
            )
            start_term = term_size(clean.header)
            # align hook times (seconds since `start`) with the cast clock
            offset = 0.0
            if isinstance(clean.header.get("timestamp"), (int, float)):
                offset = self.state["started_epoch"] - clean.header["timestamp"]
            for e in events:
                e["t"] = round(e["t"] + offset, 6)
                if e.get("t_end") is not None:
                    e["t_end"] = round(e["t_end"] + offset, 6)
            for e in events:
                if e["type"] == "command":
                    e["output"], e["output_truncated"] = output_between(clean.events, e["t"], e["t_end"])
            # resizes come from the cast when it has them (more exact than hooks)
            cast_resizes = [ev for ev in clean.events if len(ev) >= 3 and ev[1] == "r" and parse_resize(ev[2])]
            if cast_resizes:
                events = [e for e in events if e["type"] != "resize"]
                for i, ev in enumerate(cast_resizes, 1):
                    events.append({"type": "resize", "id": f"r_cast{i}", "t": float(ev[0]), **parse_resize(ev[2])})
            if version == 3:
                prev, rel = 0.0, []
                for ev in clean.events:
                    rel.append([round(ev[0] - prev, 6), *ev[1:]])
                    prev = ev[0]
                clean.events = rel
                clean.header["version"] = 3
            (self.dir / CAST_FILE).write_text(write_cast(clean), encoding="utf-8")

        # commands and cwds: scrub together so values learned in one spread to all
        cmds = [e for e in events if e["type"] == "command"]
        texts = [e["command"] for e in cmds] + [e["cwd"] or "" for e in cmds]
        clean_texts = scrubber.scrub_texts(texts) if texts else []
        for e, c, w in zip(cmds, clean_texts[: len(cmds)], clean_texts[len(cmds) :]):
            e["command"] = c
            e["cwd"] = w if e["cwd"] is not None else None
        edits = scrub_edits([e for e in events if e["type"] == "file_edit"], scrubber)
        edit_by_id = {e["id"]: e for e in edits}
        events = [edit_by_id.get(e["id"], e) if e["type"] == "file_edit" else e for e in events]
        events.sort(key=lambda e: (e["t"], {"resize": 0, "command": 1, "file_edit": 2}.get(e["type"], 3)))

        header = {
            "type": "session",
            "schema": SCHEMA_VERSION,
            "id": self.state["id"],
            "origin": "live",
            "started": dt.datetime.fromtimestamp(self.state["started_epoch"], dt.timezone.utc).isoformat(),
            "shell": self.state["shell"],
            "term": start_term or self.state.get("start_term"),
            "provenance": prov,
            "redaction": {"version": _version(), "replaced": scrubber.map.counts()},
        }
        out = self.dir / EVENTS_FILE
        out.write_text(dumps_session(header, events), encoding="utf-8")
        shutil.rmtree(self.spool, ignore_errors=True)
        return out


def _version() -> str:
    from scrubber import REDACTION_VERSION

    return REDACTION_VERSION
