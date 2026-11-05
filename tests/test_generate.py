"""Automation scripts and Dockerfiles from canonical procedures (KAN-47, KAN-75)."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from fileedit.snapshot import unified_diff
from fixtures import load_sessions
from generate import to_dockerfile, to_script, variables
from procedures import canonical_procedure, make_run

S = load_sessions()
BASH = shutil.which("bash")


def proc_for(*sids):
    return canonical_procedure([make_run(*S[s]) for s in sids])


def _bash(script: str, cwd: Path, env: dict | None = None) -> subprocess.CompletedProcess:
    f = cwd / "gen.sh"
    f.write_bytes(script.encode("utf-8"))  # LF endings even on Windows
    return subprocess.run([BASH, f.as_posix()], capture_output=True, text=True, cwd=cwd,
                          env={**os.environ, **(env or {})}, timeout=60)


@pytest.mark.skipif(not BASH, reason="bash not installed")
@pytest.mark.parametrize("sid", sorted(S))
def test_scripts_from_every_fixture_are_valid_bash(sid, tmp_path):
    f = tmp_path / "s.sh"
    f.write_bytes(to_script(proc_for(sid)).encode())
    r = subprocess.run([BASH, "-n", f.as_posix()], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_nginx_script_contents():
    sh = to_script(proc_for("mock-nginx-port", "mock-nginx-port-rerun"))
    assert "set -euo pipefail" in sh
    assert "+\tlisten 8080 default_server;" in sh and "default_server\n" not in sh.split("listen 8080")[1][:20]
    assert sh.index("patch --forward") < sh.index('"sudo nginx -t"') < sh.index("systemctl reload")
    assert "[mock-nginx-port-rerun#c7 mock-nginx-port#c12]" in sh  # evidence on every step


def test_variables_from_placeholders():
    p = proc_for("mock-git-conflict")
    assert sorted(variables(p)) == ["HOST_1", "USER_1"]
    sh = to_script(p)
    assert ': "${HOST_1:?set HOST_1' in sh and "https://${HOST_1}/v2" in sh


def _sandbox_procedure(tmp: Path) -> list[dict]:
    before = "name = demo\nprice = $5 `not a command`\n"
    after = "name = demo\nprice = $6 `not a command`\nhost = <HOST_1>\n"
    return [
        {"n": 1, "action": "edit", "path": "app.cfg", "cwd": tmp.as_posix(), "diff": unified_diff("app.cfg", before, after),
         "evidence": ["s#f1"], "support": 1.0},
        {"n": 2, "action": "export", "command": "export GREETING=hello", "cwd": tmp.as_posix(), "evidence": ["s#c2"], "support": 1.0},
        {"n": 3, "action": "mkdir", "command": "mkdir -p sub && cd sub", "cwd": tmp.as_posix(), "evidence": ["s#c3"], "support": 1.0},
        {"n": 4, "action": "write", "command": 'echo "$GREETING from $(basename "$PWD")" > out.txt', "cwd": tmp.as_posix(),
         "evidence": ["s#c4"], "support": 1.0},
        {"n": 5, "action": "edit cfg", "command": "vim app.cfg", "cwd": tmp.as_posix(), "evidence": ["s#c5"], "support": 1.0},
    ], before


@pytest.mark.skipif(not BASH or not shutil.which("patch"), reason="bash/patch not installed")
def test_generated_script_really_runs_in_a_sandbox(tmp_path):
    proc, before = _sandbox_procedure(tmp_path)
    (tmp_path / "app.cfg").write_text(before, newline="\n")
    r = _bash(to_script(proc), tmp_path, {"HOST_1": "db.example.org"})
    assert r.returncode == 0, r.stderr
    cfg = (tmp_path / "app.cfg").read_text()
    assert "price = $6 `not a command`" in cfg and "host = db.example.org" in cfg  # literal $ and backticks kept
    # export and cd persisted between steps (steps share one shell)
    assert (tmp_path / "sub" / "out.txt").read_text().strip() == "hello from sub"
    assert "interactive, not automated: vim app.cfg" in to_script(proc)


@pytest.mark.skipif(not BASH, reason="bash not installed")
def test_dry_run_changes_nothing_and_missing_vars_fail(tmp_path):
    proc, before = _sandbox_procedure(tmp_path)
    (tmp_path / "app.cfg").write_text(before, newline="\n")
    r = _bash(to_script(proc), tmp_path, {"HOST_1": "x", "DRY_RUN": "1"})
    assert r.returncode == 0, r.stderr
    assert (tmp_path / "app.cfg").read_text() == before and not (tmp_path / "sub").exists()
    assert "+ export GREETING=hello" in r.stderr
    env = {k: v for k, v in os.environ.items() if k != "HOST_1"}
    f = tmp_path / "gen.sh"
    f.write_bytes(to_script(proc).encode())
    r = subprocess.run([BASH, f.as_posix()], capture_output=True, text=True, cwd=tmp_path, env=env)
    assert r.returncode != 0 and "set HOST_1" in r.stderr


@pytest.mark.skipif(not BASH or not shutil.which("patch"), reason="bash/patch not installed")
def test_patch_refuses_a_file_that_drifted(tmp_path):
    proc, _ = _sandbox_procedure(tmp_path)
    (tmp_path / "app.cfg").write_text("something else entirely\n", newline="\n")
    r = _bash(to_script(proc[:1]), tmp_path, {"HOST_1": "x"})
    assert r.returncode != 0  # fails loudly instead of guessing


def test_dockerfile_rules():
    venv = to_dockerfile(proc_for("mock-python-venv"))
    assert "ENV VIRTUAL_ENV=/home/${USER_1}/projects/report-tool/.venv" in venv
    assert "RUN source" not in venv and "COPY . ." in venv and "ARG USER_1" in venv
    nginx = to_dockerfile(proc_for("mock-nginx-port", "mock-nginx-port-rerun"))
    assert "sudo" not in nginx.replace("# ", "")
    assert "# not available in containers: systemctl reload nginx" in nginx
    assert "RUN nginx -t" in nginx and "RUN <<CSC398_PATCH_EOF patch --forward -p1 -d /" in nginx
    docker = to_dockerfile(proc_for("mock-docker-build"))
    assert "RUN docker" not in docker  # no docker-in-docker


def test_cli_refuses_unscrubbed(tmp_path):
    import sys

    f = tmp_path / "raw.events.jsonl"
    f.write_text('{"type":"session","id":"x","redaction":{}}\n')
    r = subprocess.run([sys.executable, "-m", "generate", "script", str(f)], capture_output=True, text=True,
                       cwd=Path(__file__).resolve().parent.parent)
    assert r.returncode == 1 and "not scrubbed" in r.stderr
