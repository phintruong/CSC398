"""Privacy test on the capture pipeline (KAN-36, design: docs/design/scrubbing.md section 5).

Planted fake secrets must never reach **storage** (every file written) or
**logs** (everything the CLI and hooks print), on both ways in:

* import: each planted-set recording through ``python -m capture import``;
* live: each planted-set recording as the raw .cast of a live session, plus
  hook commands that type every planted value and a file edit that writes
  every planted value into a ``.env`` (the hardest case: values only
  recognisable because they were learned from the recording).

Also: the spool (raw data) is gone after stop, and a failing hook doesn't
echo its input. Prompts (Model 0/1 inputs) and search (Group 6 index) only
ever read these files; when those components land in this repo, add them
to ``SINKS`` below.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from capture import Session
from scrubber.detect import SECRET_KINDS

from planted_set import build_cases

REPO = Path(__file__).resolve().parent.parent
CASES = build_cases()
FRAGMENT = 8


def _cli(*args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONPATH": str(REPO)}
    return subprocess.run([sys.executable, "-m", "capture", *args], capture_output=True, text=True, env=env, encoding="utf-8")


def _all_text(root: Path) -> dict[str, str]:
    return {str(p.relative_to(root)): p.read_text(encoding="utf-8", errors="replace") for p in root.rglob("*") if p.is_file()}


def assert_no_leaks(case, sinks: dict[str, str]) -> None:
    for where, text in sinks.items():
        for p in case.planted:
            assert p.value not in text, f"{case.name}: planted {p.kind} found in {where}"
            if p.kind in SECRET_KINDS and len(p.value) >= FRAGMENT:
                for i in range(len(p.value) - FRAGMENT + 1):
                    frag = p.value[i : i + FRAGMENT]
                    assert frag not in text, f"{case.name}: fragment of planted {p.kind} in {where}"


@pytest.mark.parametrize("case", CASES, ids=[c.name for c in CASES])
def test_import_path_never_leaks(tmp_path, case):
    src_dir = tmp_path / "in"  # the raw input is the only unscrubbed file; it's not part of storage
    src_dir.mkdir()
    src = src_dir / "raw.cast"
    src.write_text(case.cast, encoding="utf-8")
    out = tmp_path / "store"
    out.mkdir()
    terms = [x for t in case.extra_terms for x in ("--term", t)]
    r = _cli("import", str(src), "-o", str(out / "s.events.jsonl"), "--cast-out", str(out / "s.cast"),
             "--source", "test", "--consent", "synthetic", "--license", "none", *terms)
    assert r.returncode == 0, r.stderr
    assert_no_leaks(case, {**_all_text(out), "stdout": r.stdout, "stderr": r.stderr})


@pytest.mark.parametrize("case", CASES, ids=[c.name for c in CASES])
def test_live_path_never_leaks(tmp_path, case):
    d = tmp_path / "s"
    work = tmp_path / "work"
    work.mkdir()
    s = Session.start(d, source="test", consent="synthetic", license="none", extra_terms=case.extra_terms,
                      cols=120, rows=32, now=1760000000.0)
    s.raw_cast_path.write_text(case.cast, encoding="utf-8")

    t = 1.0
    for p in case.planted:  # the user types each value in a later command
        Session.load(d).preexec(f"echo {p.value}", str(work), t=t)
        Session.load(d).precmd(0, t=t + 0.1)
        t += 0.2
    env = work / ".env"
    Session.load(d).preexec("vim .env", str(work), t=t)
    env.write_text("".join(f"VALUE_{i}={p.value}\n" for i, p in enumerate(case.planted)), encoding="utf-8")
    Session.load(d).precmd(0, t=t + 1)

    r = _cli("stop", "--dir", str(d))
    assert r.returncode == 0, r.stderr
    assert not (d / ".spool").exists(), "raw spool must be deleted at stop"
    stored = _all_text(d)
    assert set(stored) == {"session.events.jsonl", "session.cast"}
    assert_no_leaks(case, {**stored, "stdout": r.stdout, "stderr": r.stderr})


def test_failing_hook_does_not_echo_its_input(tmp_path):
    case = CASES[0]
    secret = case.planted[0].value
    r = _cli("hook", "pre", "--dir", str(tmp_path / "not-started"), "--cwd", str(tmp_path), f"--cmd=echo {secret}")
    assert r.returncode == 0
    assert secret not in r.stdout + r.stderr


def test_unscrubbed_session_is_rejected_by_validation():
    from contracts import validate_session_header

    hdr = {"type": "session", "schema": "events-v1", "id": "x", "origin": "live", "provenance": {}, "redaction": {}}
    assert any("not scrubbed" in e for e in validate_session_header(hdr))


@pytest.mark.parametrize("case", CASES, ids=[c.name for c in CASES])
def test_scrubbing_is_idempotent(case):
    """Scrubbing scrubbed text changes nothing, so the prompt guard accepts scrubbed sessions."""
    from capture import import_cast
    from scrubber import Scrubber

    _, evs, clean_cast, _ = import_cast(case.cast, source="t", consent="c", license="l", extra_terms=case.extra_terms)
    for e in evs:
        for k in ("command", "output"):
            if e.get(k):
                assert Scrubber().scrub_text(e[k]) == e[k], f"{case.name}: re-scrubbing changed {k}"
