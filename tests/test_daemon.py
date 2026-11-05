"""Capture daemon: live, scrubbed streaming into tagging and the goal tree (KAN-27)."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from capture import Session
from capture.daemon import CaptureDaemon, running, send, serve, wait_until_running
from contracts import loads_session, validate_session
from runtime import Guarded
from runtime.rules import RuleAnnotator
from streaming import StreamingAnnotator

from planted_set import build_cases

REPO = Path(__file__).resolve().parent.parent
SECRET = next(p.value for c in build_cases() for p in c.planted if p.kind == "TOKEN")


@pytest.fixture
def daemon(tmp_path):
    s = Session.start(tmp_path / "s", source="t", consent="synthetic", license="none", cols=80, rows=24,
                      now=1000.0, memory=True)
    factory = lambda h: StreamingAnnotator(Guarded(RuleAnnotator()), h, model_name="rules-v1")  # noqa: E731
    d = CaptureDaemon(s, factory)
    ready = threading.Event()
    t = threading.Thread(target=serve, args=(d,), kwargs={"ready": ready}, daemon=True)
    t.start()
    assert ready.wait(5)
    yield s.dir, tmp_path
    if running(s.dir):
        send(s.dir, {"op": "stop"})
    t.join(5)


def _live(d: Path):
    return loads_session((d / "live.events.jsonl").read_text(encoding="utf-8"))


def test_events_stream_scrubbed_and_annotated_as_they_close(daemon):
    d, work = daemon
    (work / ".env").write_text("A=1\n")
    send(d, {"op": "pre", "cmd": f"export GITHUB_TOKEN={SECRET}", "cwd": str(work), "t": 1.0})
    send(d, {"op": "post", "exit": 0, "t": 1.5})
    h, evs = _live(d)
    assert [e["type"] for e in evs] == ["command", "annotation"]  # streamed before the session ended
    assert SECRET not in (d / "live.events.jsonl").read_text() and "<TOKEN_1>" in evs[0]["command"]

    send(d, {"op": "pre", "cmd": "vim .env", "cwd": str(work), "t": 2.0})
    (work / ".env").write_text(f"A=1\nTOKEN={SECRET}\n")
    send(d, {"op": "post", "exit": 0, "t": 9.0})
    send(d, {"op": "pre", "cmd": "make", "cwd": str(work), "t": 10.0})
    send(d, {"op": "post", "exit": 2, "t": 11.0})
    h, evs = _live(d)
    assert validate_session(h, evs) == []
    edit = next(e for e in evs if e["type"] == "file_edit")
    assert "<TOKEN_1>" in edit["diff"]  # same placeholder as the earlier command: one map
    anns = [e for e in evs if e["type"] == "annotation"]
    assert anns[-1]["result"] == "failure" and anns[-1]["parent"] == anns[0]["id"]  # live goal tree
    # nothing raw on disk while running: the spool holds only the daemon's port file
    assert sorted(p.name for p in (d / ".spool").iterdir()) == ["daemon.json"]

    reply = send(d, {"op": "stop"})
    assert reply["ok"] and not (d / ".spool").exists()
    h2, final = loads_session(Path(reply["events"]).read_text(encoding="utf-8"))
    assert validate_session(h2, final) == []
    assert SECRET not in Path(reply["events"]).read_text()
    assert next(e for e in final if e["type"] == "file_edit")["diff"] == edit["diff"]  # same placeholders as live


def test_bad_token_and_unknown_op(daemon):
    d, _ = daemon
    import json
    import socket

    info = json.loads((d / ".spool" / "daemon.json").read_text())
    with socket.create_connection(("127.0.0.1", info["port"])) as s:
        s.sendall(b'{"token": "wrong", "op": "stop"}\n')
        assert json.loads(s.makefile("rb").readline()) == {"ok": False, "error": "bad token"}
    assert send(d, {"op": "nope"})["ok"] is False
    assert running(d)


def test_cannot_start_spool_capture_over_running_daemon(daemon):
    d, _ = daemon
    with pytest.raises(FileExistsError):
        Session.start(d, source="t", consent="c", license="l")


BASH = shutil.which("bash")


@pytest.mark.skipif(not BASH, reason="bash not installed")
def test_cli_daemon_with_real_bash_hooks(tmp_path):
    env = {**os.environ, "PYTHONPATH": str(REPO)}
    d = tmp_path / "s"
    r = subprocess.run([sys.executable, "-m", "capture", "start", "--daemon", "--annotate", "rules", "--dir", str(d),
                        "--shell", "bash", "--source", "t", "--consent", "synthetic", "--license", "none"],
                       capture_output=True, text=True, env=env, timeout=60)
    assert r.returncode == 0, r.stderr
    try:
        assert wait_until_running(d)
        env_sh = tmp_path / "env.sh"
        env_sh.write_text(r.stdout, encoding="utf-8")
        script = f"source '{env_sh.as_posix()}'\necho one > a.txt\nexport API_TOKEN={SECRET}\nfalse\nexit\n"
        subprocess.run([BASH, "--norc", "-i"], input=script, text=True, capture_output=True, cwd=tmp_path,
                       env={**os.environ, "HISTFILE": os.devnull}, timeout=60)
        h, live = loads_session((d / "live.events.jsonl").read_text(encoding="utf-8"))
        cmds = [e["command"] for e in live if e["type"] == "command"]
        assert "echo one > a.txt" in cmds and "false" in cmds
        assert any(e["type"] == "annotation" for e in live) and any(e["type"] == "file_edit" for e in live)
        assert SECRET not in (d / "live.events.jsonl").read_text()
    finally:
        r = subprocess.run([sys.executable, "-m", "capture", "stop", "--dir", str(d)], capture_output=True, text=True,
                           env=env, timeout=60)
    assert r.returncode == 0, r.stderr
    assert not running(d) and (d / "session.events.jsonl").exists()
    assert SECRET not in (d / "session.events.jsonl").read_text()
