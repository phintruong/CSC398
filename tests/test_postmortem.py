"""Post-mortem generator acceptance tests (KAN-77/KAN-78, docs/design/postmortem.md section 6)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from contracts import parse_evidence_id
from fixtures import load_sessions
from postmortem import apply_corrections, check_wording, postmortems, render, render_markdown

SESSIONS = load_sessions()
REPO = Path(__file__).resolve().parent.parent


def pm(sid):
    h, evs = SESSIONS[sid]
    return postmortems(h, evs)


def test_p1_nginx_typo():
    (r,) = pm("mock-nginx-port")
    assert r["status"] == "resolved"
    assert r["root_cause"]["evidence"][0].endswith("#f5")  # the edit, not the nginx -t that exposed it
    assert "+\tlisten 8080 default_server\n" in r["root_cause"]["diff"]
    assert any("[emerg]" in ln for ln in r["root_cause"]["errors"])
    assert [f["evidence"][0].split("#")[1] for f in r["fix"]] == ["f10", "c12"]
    assert any("nginx -t" in x["text"] for x in r["lessons"])


def test_p2_venv_fix_excludes_nothing_needed():
    (r,) = pm("mock-python-venv")
    fix = " ".join(f["text"] for f in r["fix"])
    assert "venv" in fix and "activate" in fix and "pip install" in fix


def test_p3_git_conflict_inferred():
    (r,) = pm("mock-git-conflict")
    assert r["status"] == "resolved (inferred)"
    assert any("CONFLICT" in ln for ln in r["root_cause"]["errors"])


def test_p4_docker_diagnostics_not_in_fix():
    (r,) = pm("mock-docker-build")
    assert any("`ls`" in x["text"] for x in r["timeline"])
    assert not any("`ls`" in f["text"] for f in r["fix"])


@pytest.mark.parametrize("sid", ["mock-nginx-port-rerun", "mock-resize-htop"])
def test_p5_clean_sessions(sid):
    assert pm(sid) == []


def _cmd(i, command, code, t, output=""):
    return {"type": "command", "id": f"c{i}", "t": t, "t_end": t + 1, "duration": 1, "command": command,
            "cwd": "/", "exit_code": code, "output": output}


HDR = {"type": "session", "schema": "events-v1", "id": "syn", "origin": "mock", "provenance": {},
       "redaction": {"version": "x", "replaced": {}}}


def test_p6_two_incidents_and_p11_no_annotations():
    evs = [
        _cmd(1, "make", 2, 0, "make: *** [build] Error 1"),
        _cmd(2, "vim Makefile", 0, 5),
        _cmd(3, "make", 0, 10),
        *[_cmd(10 + i, f"echo step{i}", 0, 20 + i) for i in range(3)],
        _cmd(4, "pytest", 1, 40, "FAILED tests/test_x.py::test_y - AssertionError"),
        _cmd(5, "pytest", 0, 60),
    ]
    rs = postmortems(HDR, evs)
    assert [r["status"] for r in rs] == ["resolved", "resolved"]
    assert rs[1]["root_cause"]["errors"] == ["FAILED tests/test_x.py::test_y - AssertionError"]


def test_p7_unresolved():
    rs = postmortems(HDR, [_cmd(1, "terraform apply", 1, 0, "Error: quota exceeded"), _cmd(2, "terraform apply", 1, 9)])
    assert len(rs) == 1 and rs[0]["status"] == "unresolved"
    assert "No fix recorded" in render_markdown(rs[0])


def test_ctrl_c_is_not_a_failure():
    assert postmortems(HDR, [_cmd(1, "python server.py", 130, 0)]) == []


def test_p8_corrections_override_annotations():
    h, evs = SESSIONS["mock-nginx-port"]
    corrected = apply_corrections(evs)
    a = next(e for e in corrected if e.get("corrected_fields"))
    assert "deleted the semicolon" in a["summary"]
    md = render_markdown(pm("mock-nginx-port")[0])
    assert "deleted the semicolon" in md and "dropped the semicolon" not in md
    # the original events are untouched
    assert any(e.get("summary") == "Changed the port but dropped the semicolon." for e in evs)


def test_latest_correction_wins():
    ann = {"type": "annotation", "id": "a1", "target": "c1", "summary": "v0"}
    x1 = {"type": "correction", "id": "x1", "target": "a1", "field": "summary", "new": "v2", "author": "r",
          "t_created": "2026-11-02T00:00:00Z"}
    x2 = {**x1, "id": "x2", "new": "v1", "t_created": "2026-11-01T00:00:00Z"}
    assert apply_corrections([ann, x1, x2])[0]["summary"] == "v2"


@pytest.mark.parametrize("sid", sorted(SESSIONS))
def test_p9_every_citation_exists(sid):
    h, evs = SESSIONS[sid]
    ids = {e["id"] for e in evs}
    for r in postmortems(h, evs):
        for eid in r["evidence"]:
            s, e = parse_evidence_id(eid)
            assert s == sid and e in ids
        for x in r["timeline"] + r["fix"] + r["lessons"] + [r["root_cause"], r["summary"]]:
            assert x["evidence"], x


def test_p10_wording_model_guard():
    (r,) = pm("mock-docker-build")
    base = render_markdown(r)
    assert check_wording(base, r) is None  # our own output passes the check

    ok = lambda rep, md: md.replace("Build fails", "The build failed")  # noqa: E731
    text, how = render(r, ok)
    assert how == "model" and "The build failed" in text

    adds_fact = lambda rep, md: md + "\nAlso the disk was full.\n"  # noqa: E731
    text, how = render(r, adds_fact)
    assert text == base and "uncited" in how

    bad_cite = lambda rep, md: md + "\n- Something else [other-session#c1]\n"  # noqa: E731
    assert "unknown evidence" in render(r, bad_cite)[1]

    def broken(rep, md):
        raise TimeoutError

    assert render(r, broken) == (base, "deterministic (model error: TimeoutError)")


def test_cli(tmp_path):
    out = tmp_path / "pm.md"
    r = subprocess.run([sys.executable, "-m", "postmortem", str(REPO / "fixtures/data/mock-docker-build.events.jsonl"),
                        "-o", str(out)], capture_output=True, text=True, cwd=REPO)
    assert r.returncode == 0, r.stderr
    assert out.read_text(encoding="utf-8").startswith("## Incident: Build the API image")
    unscrubbed = tmp_path / "raw.events.jsonl"
    unscrubbed.write_text('{"type": "session", "id": "x", "redaction": {}}\n', encoding="utf-8")
    r = subprocess.run([sys.executable, "-m", "postmortem", str(unscrubbed)], capture_output=True, text=True, cwd=REPO)
    assert r.returncode == 1 and "not scrubbed" in r.stderr
