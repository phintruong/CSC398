"""Capture daemon: live streaming of scrubbed events (KAN-27).

One process per session keeps the session state, file snapshots and the
placeholder map **in memory**, so nothing raw is written between hooks
(only asciinema's own raw.cast, deleted at stop). Each closed command is
scrubbed immediately with the session's map and:

* appended to ``<dir>/live.events.jsonl`` (header first; scrubbed only),
  which tagging (Group 2) and the goal tree can tail;
* fed to an in-process ``StreamingAnnotator`` (optional), whose annotations
  are appended to the same file.

At ``stop`` the whole session is written again (``session.events.jsonl`` and
``session.cast``) with the same map, so placeholders match the live stream.

Protocol: the hooks connect to 127.0.0.1:<port> and send one JSON line
``{"token": ..., "op": "pre"|"post"|"stop"|"ping", ...}``; the reply is one
JSON line. Port and token are in ``<dir>/.spool/daemon.json`` (mode 0600).
"""

from __future__ import annotations

import json
import os
import secrets
import socket
import socketserver
import threading
import time
from pathlib import Path
from typing import Any, Callable

from contracts import SCHEMA_VERSION
from fileedit.snapshot import scrub_edits
from scrubber import REDACTION_VERSION, Recording, Scrubber, read_cast, scrub_recording

from .session import SPOOL, Session
from .text import absolute_times, output_between

DAEMON_FILE = "daemon.json"
LIVE_FILE = "live.events.jsonl"


class CaptureDaemon:
    def __init__(self, session: Session, annotator_factory: Callable[[dict], Any] | None = None) -> None:
        self.session = session
        self.scrubber = Scrubber(session.state["extra_terms"])
        self.lock = threading.Lock()
        self.emitted = 0  # raw events already streamed
        self.cast_seen_t = 0.0  # cast time already scrubbed
        self.cast_events: list[list] = []  # scrubbed cast events (absolute times)
        self.live_path = session.dir / LIVE_FILE
        self.header = self._header()
        self.live_path.write_text(json.dumps(self.header) + "\n", encoding="utf-8")
        self.annotator = annotator_factory(self.header) if annotator_factory else None
        self.stopped: Path | None = None

    def _header(self) -> dict:
        import datetime as dt

        st = self.session.state
        return {"type": "session", "schema": SCHEMA_VERSION, "id": st["id"], "origin": "live",
                "started": dt.datetime.fromtimestamp(st["started_epoch"], dt.timezone.utc).isoformat(),
                "shell": st["shell"], "term": st.get("start_term"), "provenance": st["provenance"],
                "redaction": {"version": REDACTION_VERSION, "replaced": {}, "streaming": True}}

    # -- cast tailing -------------------------------------------------------------

    def _tail_cast(self) -> None:
        """Scrub cast events recorded since the last call (same map) and keep them."""
        raw = self.session.raw_cast_path
        if not raw.exists():
            return
        try:
            rec = read_cast(raw.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            return  # asciinema mid-write; try again next time
        events = absolute_times(rec.header, rec.events)
        new = [e for e in events if e[0] > self.cast_seen_t]
        if not new:
            return
        clean, _ = scrub_recording(Recording({**rec.header, "version": 2}, new),
                                   extra_terms=self.session.state["extra_terms"], mapping=self.scrubber.map)
        self.cast_events += clean.events
        self.cast_seen_t = new[-1][0]
        ts = rec.header.get("timestamp")
        self.offset = self.session.state["started_epoch"] - ts if isinstance(ts, (int, float)) else 0.0

    # -- streaming --------------------------------------------------------------------

    def _emit_new(self) -> list[dict]:
        raw = self.session.state["events"][self.emitted :]
        self.emitted = len(self.session.state["events"])
        if not raw:
            return []
        self._tail_cast()
        offset = getattr(self, "offset", 0.0)
        out = []
        for ev in raw:
            ev = dict(ev)
            ev["t"] = round(ev["t"] + offset, 6)
            if ev.get("t_end") is not None:
                ev["t_end"] = round(ev["t_end"] + offset, 6)
            if ev["type"] == "command":
                c, w = self.scrubber.scrub_texts([ev["command"], ev["cwd"] or ""])
                ev["command"], ev["cwd"] = c, (w if ev["cwd"] is not None else None)
                if self.cast_events:
                    ev["output"], ev["output_truncated"] = output_between(self.cast_events, ev["t"], ev["t_end"])
            elif ev["type"] == "file_edit":
                ev = scrub_edits([ev], self.scrubber)[0]
            out.append(ev)
        anns = []
        if self.annotator is not None:
            for ev in out:
                anns += self.annotator.feed(ev)
            anns += self.annotator.flush()
        with self.live_path.open("a", encoding="utf-8") as fh:
            for ev in out + anns:
                fh.write(json.dumps(ev, ensure_ascii=False) + "\n")
        return out + anns

    # -- operations ---------------------------------------------------------------------

    def handle(self, msg: dict) -> dict:
        op = msg.get("op")
        with self.lock:
            if self.stopped is not None:
                return {"ok": False, "error": "stopped"}
            if op == "ping":
                return {"ok": True}
            if op == "pre":
                self.session.preexec(msg.get("cmd", ""), msg.get("cwd") or "/", t=msg.get("t"))
                return {"ok": True}
            if op == "post":
                self.session.precmd(int(msg.get("exit", 0)), t=msg.get("t"), jobs=msg.get("jobs") or [],
                                    cols=msg.get("cols") or None, rows=msg.get("rows") or None)
                return {"ok": True, "streamed": len(self._emit_new())}
            if op == "stop":
                self._emit_new()
                out = self.session.stop(t=msg.get("t"), scrubber=self.scrubber)
                self.stopped = out
                return {"ok": True, "events": str(out), "replaced": self.scrubber.map.counts()}
        return {"ok": False, "error": "unknown op"}


# -- server --------------------------------------------------------------------------


class _Handler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        line = self.rfile.readline(1 << 20)
        try:
            msg = json.loads(line)
            if not secrets.compare_digest(str(msg.get("token", "")), self.server.token):  # type: ignore[attr-defined]
                reply = {"ok": False, "error": "bad token"}
            else:
                reply = self.server.daemon_obj.handle(msg)  # type: ignore[attr-defined]
        except Exception as e:  # never echo content back
            reply = {"ok": False, "error": type(e).__name__}
        self.wfile.write((json.dumps(reply) + "\n").encode())
        if reply.get("ok") and msg.get("op") == "stop":
            threading.Thread(target=self.server.shutdown, daemon=True).start()


class _Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def serve(daemon_obj: CaptureDaemon, ready: threading.Event | None = None) -> None:
    srv = _Server(("127.0.0.1", 0), _Handler)
    srv.token = secrets.token_hex(16)  # type: ignore[attr-defined]
    srv.daemon_obj = daemon_obj  # type: ignore[attr-defined]
    info = daemon_obj.session.spool / DAEMON_FILE
    tmp = info.with_suffix(".tmp")
    tmp.write_text(json.dumps({"port": srv.server_address[1], "token": srv.token, "pid": os.getpid()}), encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, info)
    if ready:
        ready.set()
    try:
        srv.serve_forever(poll_interval=0.2)
    finally:
        srv.server_close()
        info.unlink(missing_ok=True)


def send(directory: str | os.PathLike, msg: dict, timeout: float = 5.0) -> dict:
    """Client used by the hooks: one request, one reply."""
    info = json.loads((Path(directory) / SPOOL / DAEMON_FILE).read_text(encoding="utf-8"))
    with socket.create_connection(("127.0.0.1", info["port"]), timeout=timeout) as s:
        s.sendall((json.dumps({**msg, "token": info["token"]}) + "\n").encode())
        f = s.makefile("rb")
        return json.loads(f.readline())


def running(directory: str | os.PathLike) -> bool:
    return (Path(directory) / SPOOL / DAEMON_FILE).exists()


def wait_until_running(directory: str | os.PathLike, timeout: float = 10.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if running(directory):
            try:
                return send(directory, {"op": "ping"}, timeout=1).get("ok", False)
            except OSError:
                pass
        time.sleep(0.05)
    return False
