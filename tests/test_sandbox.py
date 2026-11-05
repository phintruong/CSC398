"""Generated Dockerfiles and scripts really build and run, in a locked-down container (KAN-76).

Skipped when Docker isn't available. The build may fetch the base image's
`patch` package; the *run* has no network, a read-only root filesystem, no
capabilities and small memory/pid limits.
"""

from __future__ import annotations

import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

from fileedit.snapshot import unified_diff
from generate import to_dockerfile, to_script

DOCKER = shutil.which("docker")
BASE = "ubuntu:24.04"
SANDBOX = ["--rm", "--network", "none", "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
           "--memory", "256m", "--pids-limit", "128", "--tmpfs", "/tmp:rw,size=16m"]


def _docker_ok() -> bool:
    if not DOCKER:
        return False
    r = subprocess.run([DOCKER, "info", "--format", "{{.ServerVersion}}"], capture_output=True, text=True, timeout=30)
    return r.returncode == 0


pytestmark = pytest.mark.skipif(not _docker_ok(), reason="Docker daemon not available")

BEFORE = "name = demo\nprice = $5 `not a command`\nport = 80\n"
AFTER = "name = demo\nprice = $6 `not a command`\nport = 8080\n"


def procedure(cwd: str) -> list[dict]:
    return [
        {"n": 1, "action": "edit", "path": "app.cfg", "cwd": cwd, "diff": unified_diff("app.cfg", BEFORE, AFTER),
         "evidence": ["s#f1"], "support": 1.0, "why": "Move to port 8080."},
        {"n": 2, "action": "export", "command": "export GREETING=hello", "cwd": cwd, "evidence": ["s#c2"], "support": 1.0},
        {"n": 3, "action": "write", "command": 'mkdir -p out && echo "$GREETING from $(pwd)" > out/msg.txt', "cwd": cwd,
         "evidence": ["s#c3"], "support": 1.0},
        {"n": 4, "action": "reload", "command": "sudo systemctl reload app", "cwd": cwd, "evidence": ["s#c4"], "support": 1.0},
        {"n": 5, "action": "edit", "command": "vim app.cfg", "cwd": cwd, "evidence": ["s#c5"], "support": 1.0},
    ]


def _build(ctx: Path, dockerfile: str, tag: str) -> subprocess.CompletedProcess:
    (ctx / "Dockerfile").write_text(dockerfile, encoding="utf-8", newline="\n")
    return subprocess.run([DOCKER, "build", "-q", "-t", tag, str(ctx)], capture_output=True, text=True, timeout=600)


def _skip_if_offline(r: subprocess.CompletedProcess) -> None:
    if r.returncode != 0 and any(s in r.stderr for s in ("Temporary failure resolving", "Could not resolve",
                                                         "failed to resolve source metadata", "dial tcp")):
        pytest.skip("no network for apt/base image during build")


@pytest.fixture
def ctx(tmp_path):
    (tmp_path / "app.cfg").write_text(BEFORE, encoding="utf-8", newline="\n")
    return tmp_path


def test_generated_dockerfile_builds_and_runs_sandboxed(ctx):
    tag = f"csc398-sandbox-{uuid.uuid4().hex[:8]}"
    try:
        r = _build(ctx, to_dockerfile(procedure("/srv/app"), base=BASE, title="sandbox test"), tag)
        _skip_if_offline(r)
        assert r.returncode == 0, r.stderr[-2000:]
        run = subprocess.run([DOCKER, "run", *SANDBOX, tag, "bash", "-c", "cat /srv/app/app.cfg; cat /srv/app/out/msg.txt"],
                             capture_output=True, text=True, timeout=120)
        assert run.returncode == 0, run.stderr
        assert "price = $6 `not a command`" in run.stdout and "port = 8080" in run.stdout  # patch applied literally
        assert "hello from /srv/app" in run.stdout  # ENV from export, WORKDIR from cwd
    finally:
        subprocess.run([DOCKER, "rmi", "-f", tag], capture_output=True)


def test_generated_script_runs_in_a_sandboxed_container(ctx):
    """The bash script variant, run with no network as an unprivileged user inside a throwaway container."""
    tag = f"csc398-sandbox-sh-{uuid.uuid4().hex[:8]}"
    base_df = (f"FROM {BASE}\nRUN apt-get update && apt-get install -y --no-install-recommends patch "
               "&& rm -rf /var/lib/apt/lists/*\nRUN useradd -m runner\n")
    try:
        r = _build(ctx, base_df, tag)
        _skip_if_offline(r)
        assert r.returncode == 0, r.stderr[-2000:]
        work = ctx / "work"
        work.mkdir()
        (work / "app.cfg").write_text(BEFORE, encoding="utf-8", newline="\n")
        # no systemd/sudo in a container: drop the reload step (scripts target real machines)
        steps = [st for st in procedure("/work") if "systemctl" not in st.get("command", "")]
        (work / "run.sh").write_bytes(to_script(steps).encode("utf-8"))
        run = subprocess.run(
            [DOCKER, "run", *[a for a in SANDBOX if a != "--read-only"], "--user", "runner",
             "-v", f"{work.resolve()}:/work", "-w", "/work", tag, "bash", "run.sh"],
            capture_output=True, text=True, timeout=120)
        assert run.returncode == 0, run.stderr
        assert "port = 8080" in (work / "app.cfg").read_text(encoding="utf-8")
        assert (work / "out" / "msg.txt").read_text(encoding="utf-8").strip() == "hello from /work"
        assert "interactive, not automated: vim app.cfg" in (work / "run.sh").read_text(encoding="utf-8")
    finally:
        subprocess.run([DOCKER, "rmi", "-f", tag], capture_output=True)
