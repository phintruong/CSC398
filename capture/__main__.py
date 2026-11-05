"""python -m capture <start|hook|stop|record|import|validate> ...

  eval "$(python -m capture start --dir runs/s1 --source laptop --consent recorded-by-owner --license CC-BY-SA-4.0)"
  ... work in this shell ...
  python -m capture stop --dir runs/s1

  python -m capture record --dir runs/s1 --source ... --consent ... --license ...   # asciinema + hooks, then stop
  python -m capture import old.cast -o old.events.jsonl --source ... --consent ... --license ... [--cast-out old.clean.cast]

Hooks never print secrets and never fail the user's shell: errors are
reported by exception type only, and the exit status is always 0.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from contracts import dumps_session, loads_session, validate_session

from .importer import import_cast
from .session import Session

HOOKS = Path(__file__).resolve().parent / "hooks"
REPO = Path(__file__).resolve().parent.parent


def _prov(p: argparse.ArgumentParser) -> None:
    p.add_argument("--source", required=True)
    p.add_argument("--consent", required=True)
    p.add_argument("--license", required=True)
    p.add_argument("--term", action="append", default=[], help="extra name to scrub (repeatable)")


def _terms(a: argparse.Namespace) -> list[str]:
    """--term values plus the always-scrub list from the Privacy & cloud settings (KAN-115)."""
    from dashboard import settings

    return list(dict.fromkeys(a.term + settings.load()["extra_terms"]))


def _shell_name(explicit: str | None) -> str:
    name = explicit or Path(os.environ.get("SHELL", "bash")).name
    return "zsh" if "zsh" in name else "bash"


def _spawn_daemon(a: argparse.Namespace, shell: str, cols: int, rows: int) -> None:
    """Start `python -m capture daemon` detached from this shell and wait until it answers."""
    from .daemon import wait_until_running

    args = [sys.executable, "-m", "capture", "daemon", "--dir", str(a.dir), "--shell", shell,
            "--source", a.source, "--consent", a.consent, "--license", a.license,
            "--cols", str(cols), "--rows", str(rows)] + [x for t in _terms(a) for x in ("--term", t)]
    if a.annotate:
        args += ["--annotate", a.annotate]
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(filter(None, [str(REPO), os.environ.get("PYTHONPATH")]))}
    kw: dict = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL, "env": env}
    if os.name == "nt":
        kw["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kw["start_new_session"] = True
    subprocess.Popen(args, **kw)
    if not wait_until_running(a.dir):
        raise SystemExit("capture daemon did not start")


def cmd_start(a: argparse.Namespace) -> int:
    shell = _shell_name(a.shell)
    cols, rows = shutil.get_terminal_size((0, 0))
    if a.daemon:
        _spawn_daemon(a, shell, cols, rows)
        sid = "(daemon)"
        raw_cast = Path(a.dir) / ".spool" / "raw.cast"
    else:
        s = Session.start(a.dir, source=a.source, consent=a.consent, license=a.license,
                          extra_terms=_terms(a), shell=shell, cols=cols or None, rows=rows or None)
        sid, raw_cast = s.state["id"], s.raw_cast_path
    d = str(Path(a.dir).resolve())
    lines = [
        f"export CAPTURE_DIR={shlex.quote(d)}",
        f"export CAPTURE_PY={shlex.quote(sys.executable)}",
        f"export PYTHONPATH={shlex.quote(str(REPO))}${{PYTHONPATH:+{os.pathsep}$PYTHONPATH}}",
        f"source {shlex.quote(str(HOOKS / ('capture.' + shell)))}",
    ]
    print("\n".join(lines))
    print(f"# capture {sid} started; raw cast path for asciinema: {raw_cast}", file=sys.stderr)
    return 0


def cmd_hook(a: argparse.Namespace) -> int:
    try:
        from .daemon import running, send

        if running(a.dir):
            if a.phase == "pre":
                send(a.dir, {"op": "pre", "cmd": a.cmd, "cwd": a.cwd})
            else:
                send(a.dir, {"op": "post", "exit": a.exit, "jobs": (a.jobs or "").split(),
                             "cols": a.cols, "rows": a.rows})
            return 0
        s = Session.load(a.dir)
        if a.phase == "pre":
            s.preexec(a.cmd, a.cwd)
        else:
            s.precmd(a.exit, jobs=(a.jobs or "").split(), cols=a.cols or None, rows=a.rows or None)
    except Exception as e:  # never break the user's shell, never echo values
        print(f"capture hook: {type(e).__name__}", file=sys.stderr)
    return 0


def cmd_stop(a: argparse.Namespace) -> int:
    from .daemon import running, send

    if running(a.dir):
        reply = send(a.dir, {"op": "stop"}, timeout=120)
        if not reply.get("ok"):
            print(f"stop failed: {reply.get('error')}", file=sys.stderr)
            return 1
        out = Path(reply["events"])
    else:
        out = Session.load(a.dir).stop(cast_path=a.cast)
    header, events = loads_session(out.read_text(encoding="utf-8"))
    print(json.dumps({"events": str(out), "count": len(events), "replaced": header["redaction"]["replaced"]}))
    return 0


def cmd_daemon(a: argparse.Namespace) -> int:
    """Foreground daemon (start --daemon runs this detached)."""
    from .daemon import CaptureDaemon, serve

    s = Session.start(a.dir, source=a.source, consent=a.consent, license=a.license, extra_terms=a.term,
                      shell=a.shell, cols=a.cols or None, rows=a.rows or None, memory=True)
    factory = None
    if a.annotate == "rules":
        from runtime import Guarded
        from runtime.rules import RuleAnnotator
        from streaming import StreamingAnnotator

        factory = lambda header: StreamingAnnotator(Guarded(RuleAnnotator()), header, model_name="rules-v1")  # noqa: E731
    serve(CaptureDaemon(s, factory))
    return 0


def cmd_record(a: argparse.Namespace) -> int:
    asciinema = shutil.which("asciinema")
    if not asciinema:
        print("asciinema not found; use `start` + `stop` instead", file=sys.stderr)
        return 2
    shell = _shell_name(a.shell)
    s = Session.start(a.dir, source=a.source, consent=a.consent, license=a.license, extra_terms=_terms(a), shell=shell)
    env = {
        **os.environ,
        "CAPTURE_DIR": str(Path(a.dir).resolve()),
        "CAPTURE_PY": sys.executable,
        "PYTHONPATH": os.pathsep.join(filter(None, [str(REPO), os.environ.get("PYTHONPATH")])),
    }
    if shell == "zsh":
        env["ZDOTDIR"] = str(HOOKS / "zdotdir")
        child = "zsh -i"
    else:
        child = f"bash --rcfile {shlex.quote(str(HOOKS / 'bashrc'))} -i"
    try:
        subprocess.run([asciinema, "rec", "--stdin", "-q", "-c", child, str(s.raw_cast_path)], env=env, check=False)
    finally:
        out = Session.load(a.dir).stop()
    print(json.dumps({"events": str(out)}))
    return 0


def cmd_import(a: argparse.Namespace) -> int:
    text = a.input.read_text(encoding="utf-8", errors="replace")
    header, events, cast_text, scrubber = import_cast(
        text, source=a.source, consent=a.consent, license=a.license, extra_terms=_terms(a), prompt_regex=a.prompt
    )
    errs = validate_session(header, events)
    if errs:
        print("\n".join(errs[:20]), file=sys.stderr)
        return 1
    a.output.write_text(dumps_session(header, events), encoding="utf-8")
    if a.cast_out:
        a.cast_out.write_text(cast_text, encoding="utf-8")
    summary = {"events": str(a.output), "commands": sum(e["type"] == "command" for e in events),
               "replaced": scrubber.map.counts(), **header.get("import", {})}
    print(json.dumps(summary), file=sys.stderr)
    return 0


def cmd_validate(a: argparse.Namespace) -> int:
    bad = 0
    for path in a.files:
        header, events = loads_session(Path(path).read_text(encoding="utf-8"))
        errs = validate_session(header, events)
        bad += bool(errs)
        print(f"{path}: {'OK' if not errs else str(len(errs)) + ' error(s)'}")
        for e in errs[:20]:
            print("  " + e)
    return 1 if bad else 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m capture")
    sub = p.add_subparsers(dest="cmd", required=True)

    st = sub.add_parser("start", help="start a capture; prints shell lines to eval")
    st.add_argument("--dir", required=True)
    st.add_argument("--shell", choices=["bash", "zsh"])
    st.add_argument("--daemon", action="store_true", help="keep state in memory and stream scrubbed events live (KAN-27)")
    st.add_argument("--annotate", choices=["rules"], help="with --daemon: annotate events as they stream")
    _prov(st)
    st.set_defaults(fn=cmd_start)

    dm = sub.add_parser("daemon", help="run the capture daemon in the foreground")
    dm.add_argument("--dir", required=True)
    dm.add_argument("--shell", choices=["bash", "zsh"])
    dm.add_argument("--cols", type=int, default=0)
    dm.add_argument("--rows", type=int, default=0)
    dm.add_argument("--annotate", choices=["rules"])
    _prov(dm)
    dm.set_defaults(fn=cmd_daemon)

    hk = sub.add_parser("hook", help="called by the shell hooks")
    hk.add_argument("phase", choices=["pre", "post"])
    hk.add_argument("--dir", required=True)
    hk.add_argument("--cwd", default=os.getcwd())
    hk.add_argument("--exit", type=int, default=0)
    hk.add_argument("--cols", type=int, default=0)
    hk.add_argument("--rows", type=int, default=0)
    hk.add_argument("--jobs", default="")
    hk.add_argument("--cmd", default="", help="the command line (pre); pass as --cmd=VALUE")
    hk.set_defaults(fn=cmd_hook)

    sp = sub.add_parser("stop", help="scrub, write session.events.jsonl, delete the spool")
    sp.add_argument("--dir", required=True)
    sp.add_argument("--cast", help="raw .cast recorded alongside (default: <dir>/.spool/raw.cast if present)")
    sp.set_defaults(fn=cmd_stop)

    rc = sub.add_parser("record", help="run asciinema with the hooks, then stop")
    rc.add_argument("--dir", required=True)
    rc.add_argument("--shell", choices=["bash", "zsh"])
    _prov(rc)
    rc.set_defaults(fn=cmd_record)

    im = sub.add_parser("import", help="convert an old .cast into schema v1 events (scrubbed)")
    im.add_argument("input", type=Path)
    im.add_argument("-o", "--output", type=Path, required=True)
    im.add_argument("--cast-out", type=Path, help="also write the scrubbed .cast")
    im.add_argument("--prompt", help="regex for an unusual shell prompt; must define a group named p")
    _prov(im)
    im.set_defaults(fn=cmd_import)

    va = sub.add_parser("validate", help="check session files against schema v1")
    va.add_argument("files", nargs="+")
    va.set_defaults(fn=cmd_validate)

    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
