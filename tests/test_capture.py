"""Live capture + importer tests (KAN-26), including B11 from the file-edit design."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from capture import Session, import_cast, load_cast_tolerant
from capture.text import render
from contracts import loads_session, validate_session

REPO = Path(__file__).resolve().parent.parent


def cast(events, version=2, width=80, height=24, **hdr):
    h = {"version": version, **({"width": width, "height": height} if version == 2 else {"term": {"cols": width, "rows": height}}), **hdr}
    return "\n".join([json.dumps(h)] + [json.dumps(e) for e in events]) + "\n"


SIMPLE = [
    [0.1, "o", "\x1b[32mdev@box\x1b[0m:\x1b[34m~/proj\x1b[0m$ "],
    [0.5, "o", "lss"],
    [0.6, "o", "\b \b"],
    [0.7, "o", "\r\n"],
    [0.8, "o", "README.md  src\r\n"],
    [0.9, "o", "dev@box:~/proj$ "],
    [1.0, "o", "make\r\n"],
    [1.5, "r", "120x40"],
    [2.0, "o", "make: *** No rule to make target.  Stop.\r\n"],
    [2.1, "o", "dev@box:~/proj$ "],
    [2.2, "o", "\r\n"],
    [2.3, "o", "dev@box:~/proj$ "],
]


def _import(text, **kw):
    return import_cast(text, source="test", consent="synthetic", license="none", **kw)


def test_render_applies_backspace_and_cr():
    text, times = render([[0, "o", "abc\b\bX\r\n"], [1, "o", "progress 10%\rprogress 100%\n"]])
    assert text == "aX\nprogress 100%\n"
    assert len(times) == len(text)


def test_import_segments_commands():
    h, evs, cast_text, _ = _import(cast(SIMPLE))
    assert validate_session(h, evs) == []
    cmds = [e for e in evs if e["type"] == "command"]
    assert [c["command"] for c in cmds] == ["ls", "make"]
    assert cmds[0]["output"] == "README.md  src"
    assert cmds[0]["cwd"] == "~/proj"
    assert cmds[0]["exit_code"] is None
    assert "No rule" in cmds[1]["output"]
    # term is the size when the command started; the resize at 1.5 happened during make
    assert cmds[0]["term"] == cmds[1]["term"] == {"cols": 80, "rows": 24}
    resize = [e for e in evs if e["type"] == "resize"][0]
    assert resize["cols"] == 120 and cmds[1]["t"] < resize["t"] < cmds[1]["t_end"]
    assert h["origin"] == "import" and h["redaction"]["version"].startswith("scrub-")
    assert "dev@box" not in cast_text  # user@host scrubbed in the cast too


def test_import_v3_intervals():
    v3 = [[0.1, "o", "$ "], [0.2, "o", "echo hi\r\n"], [0.1, "o", "hi\r\n"], [0.3, "o", "$ "]]
    h, evs, cast_text, _ = _import(cast(v3, version=3))
    cmd = [e for e in evs if e["type"] == "command"][0]
    assert cmd["command"] == "echo hi" and cmd["t"] == pytest.approx(0.3) and cmd["t_end"] == pytest.approx(0.7)
    assert json.loads(cast_text.splitlines()[0])["version"] == 3
    assert json.loads(cast_text.splitlines()[2])[0] == pytest.approx(0.2)  # written back as intervals


def test_import_tolerates_broken_lines():
    text = cast(SIMPLE[:5]) + "{not json\n[1]\n[\"x\", \"o\", \"y\"]\n"
    lines = text.splitlines()
    lines.insert(3, "garbage")
    h, evs, _, _ = _import("\n".join(lines))
    assert h["import"]["skipped_lines"] == 4
    assert validate_session(h, evs) == []


def test_import_rejects_non_cast():
    with pytest.raises(ValueError):
        load_cast_tolerant('{"hello": 1}\n')


def test_import_out_of_order_times_are_clamped():
    _, evs, _ = load_cast_tolerant(cast([[1.0, "o", "a"], [0.5, "o", "b"], [2.0, "o", "c"]]))
    assert [e[0] for e in evs] == [1.0, 1.0, 2.0]


def test_import_custom_prompt():
    evs = [[0.1, "o", "λ> "], [0.2, "o", "uptime\r\n"], [0.3, "o", "up 3 days\r\n"], [0.4, "o", "λ> "]]
    _, out, _, _ = _import(cast(evs), prompt_regex=r"^(?P<p>λ> )")
    assert [e["command"] for e in out if e["type"] == "command"] == ["uptime"]


# -- live session ---------------------------------------------------------------


@pytest.fixture
def sess(tmp_path):
    return Session.start(tmp_path / "s", source="t", consent="synthetic", license="none", cols=80, rows=24, now=1000.0)


def test_session_basic_flow(tmp_path, sess):
    work = tmp_path / "w"
    work.mkdir()
    f = work / "app.conf"
    f.write_text("port=80\n")
    Session.load(sess.dir).preexec("sed -i s/80/8080/ app.conf", str(work), t=1.0)
    f.write_text("port=8080\n")
    Session.load(sess.dir).precmd(0, t=2.0, cols=100, rows=30)
    Session.load(sess.dir).preexec("false", str(work), t=3.0)
    Session.load(sess.dir).precmd(1, t=3.5, cols=100, rows=30)
    out = Session.load(sess.dir).stop(t=4.0)
    h, evs = loads_session(out.read_text(encoding="utf-8"))
    assert validate_session(h, evs) == []
    assert [e["type"] for e in evs] == ["command", "file_edit", "resize", "command"]
    edit = evs[1]
    assert edit["command_id"] == evs[0]["id"] and edit["is_config"] and "+port=8080" in edit["diff"]
    assert evs[3]["exit_code"] == 1
    assert h["term"] == {"cols": 80, "rows": 24}
    assert not (sess.dir / ".spool").exists()


def test_b11_suspended_editor(tmp_path, sess):
    work = tmp_path / "w"
    work.mkdir()
    f = work / "app.py"
    f.write_text("x = 1\n")
    S = lambda: Session.load(sess.dir)  # noqa: E731  (every hook is a fresh process)
    S().preexec("vim app.py", str(work), t=1.0)
    S().precmd(148, t=2.0, jobs=["4242"])  # ^Z
    S().preexec("ls", str(work), t=3.0)
    S().precmd(0, t=3.1, jobs=["4242"])
    S().preexec("fg", str(work), t=4.0)
    f.write_text("x = 2\n")  # edit + :wq after fg
    S().precmd(0, t=9.0, jobs=[])
    out = S().stop(t=10.0)
    _, evs = loads_session(out.read_text(encoding="utf-8"))
    edits = [e for e in evs if e["type"] == "file_edit"]
    assert len(edits) == 1
    assert edits[0]["t_end"] == 9.0 and edits[0]["tool"] == "vim"


def test_stop_mid_command_and_suspended_flush(tmp_path, sess):
    work = tmp_path / "w"
    work.mkdir()
    (work / "a.txt").write_text("a\n")
    Session.load(sess.dir).preexec("vim a.txt", str(work), t=1.0)
    Session.load(sess.dir).precmd(148, t=2.0, jobs=["7"])
    (work / "a.txt").write_text("b\n")
    Session.load(sess.dir).preexec("sleep 100", str(work), t=3.0)
    out = Session.load(sess.dir).stop(t=5.0)
    h, evs = loads_session(out.read_text(encoding="utf-8"))
    assert validate_session(h, evs) == []
    assert any(e["type"] == "file_edit" for e in evs)
    last = [e for e in evs if e["type"] == "command"][-1]
    assert last["command"] == "sleep 100" and last["exit_code"] is None and last["t_end"] is None


def test_session_with_cast_gets_output_and_scrubbed_cast(tmp_path, sess):
    rec = [
        [0.5, "o", "$ "], [1.0, "o", "echo hi\r\n"], [1.2, "o", "hi\r\n"], [1.4, "o", "$ "],
        [1.6, "r", "90x30"],
    ]
    raw = sess.raw_cast_path
    raw.write_text(cast(rec, timestamp=1000), encoding="utf-8")
    Session.load(sess.dir).preexec("echo hi", str(tmp_path), t=1.05)
    Session.load(sess.dir).precmd(0, t=1.3)
    out = Session.load(sess.dir).stop(t=2.0)
    h, evs = loads_session(out.read_text(encoding="utf-8"))
    cmd = [e for e in evs if e["type"] == "command"][0]
    assert cmd["output"] == "hi"
    assert [e for e in evs if e["type"] == "resize"][0]["cols"] == 90
    clean = (sess.dir / "session.cast").read_text(encoding="utf-8")
    assert '"redaction"' in clean and not raw.exists()


def test_cannot_start_twice(tmp_path, sess):
    with pytest.raises(FileExistsError):
        Session.start(sess.dir, source="t", consent="c", license="l")


# -- CLI and real shell hooks ------------------------------------------------------


def _cli(*args, **kw):
    env = {**os.environ, "PYTHONPATH": str(REPO)}
    return subprocess.run([sys.executable, "-m", "capture", *args], capture_output=True, text=True, env=env, **kw)


def test_hook_never_fails_the_shell(tmp_path):
    r = _cli("hook", "post", "--dir", str(tmp_path / "nothing-running"), "--exit", "0")
    assert r.returncode == 0
    assert r.stdout == "" and "FileNotFoundError" in r.stderr


def test_cli_import_and_validate(tmp_path):
    src = tmp_path / "old.cast"
    src.write_text(cast(SIMPLE), encoding="utf-8")
    out = tmp_path / "old.events.jsonl"
    r = _cli("import", str(src), "-o", str(out), "--cast-out", str(tmp_path / "clean.cast"),
             "--source", "t", "--consent", "synthetic", "--license", "none")
    assert r.returncode == 0, r.stderr
    assert _cli("validate", str(out)).returncode == 0


BASH = shutil.which("bash")


@pytest.mark.skipif(not BASH, reason="bash not installed")
def test_bash_hooks_end_to_end(tmp_path):
    d = tmp_path / "s"
    r = _cli("start", "--dir", str(d), "--shell", "bash", "--source", "t", "--consent", "synthetic", "--license", "none")
    assert r.returncode == 0, r.stderr
    env_sh = tmp_path / "env.sh"
    env_sh.write_text(r.stdout, encoding="utf-8")
    script = (
        f"source '{env_sh.as_posix()}'\n"
        "echo one > a.txt\n"
        "\n"  # empty Enter: must not duplicate the previous command
        "false\n"
        "exit\n"
    )
    subprocess.run([BASH, "--norc", "-i"], input=script, text=True, capture_output=True, cwd=tmp_path,
                   env={**os.environ, "HISTFILE": os.devnull}, timeout=60)
    r = _cli("stop", "--dir", str(d))
    assert r.returncode == 0, r.stderr
    h, evs = loads_session((d / "session.events.jsonl").read_text(encoding="utf-8"))
    assert validate_session(h, evs) == []
    cmds = [e["command"] for e in evs if e["type"] == "command"]
    assert cmds.count("echo one > a.txt") == 1 and "false" in cmds
    assert [e["exit_code"] for e in evs if e["type"] == "command" and e["command"] == "false"] == [1]
    assert [e["change"] for e in evs if e["type"] == "file_edit"] == ["created"]
