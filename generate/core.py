"""Canonical procedure -> automation script / Dockerfile (KAN-47, KAN-75).

Deterministic templates, no model: every generated line comes from a step
that was actually run successfully in a recorded session, and is commented
with the evidence ids it came from.

Safety rules for generated code (see docs/design/automation.md):
* scripts run with ``set -euo pipefail`` and support ``DRY_RUN=1``;
* scrubbed values (``<HOST_1>``) become required variables, never guesses;
* interactive programs (editors, pagers, htop, ssh) are never executed:
  they become comments;
* file edits are applied as patches that fail loudly if the file differs.
"""

from __future__ import annotations

import re
from typing import Sequence

_PLACEHOLDER = re.compile(r"<([A-Z]+(?:_[A-Z]+)*)_(\d+)>")
INTERACTIVE = frozenset({"vim", "vi", "nvim", "nano", "emacs", "less", "more", "top", "htop", "man", "ssh", "tmux",
                         "screen", "watch", "python3 -i", "mysql", "psql"})
NOT_IN_CONTAINERS = frozenset({"systemctl", "service", "reboot", "shutdown", "journalctl", "docker", "podman"})
SAFE_HEREDOC = "CSC398_PATCH_EOF"


def _head(cmd: str) -> str:
    words = cmd.split()
    while words and words[0] in ("sudo", "doas", "env", "time", "nohup"):
        words = words[1:]
    return words[0] if words else ""


def variables(procedure: Sequence[dict]) -> list[str]:
    """Scrubbed placeholders the user must supply, e.g. HOST_1."""
    seen: list[str] = []
    for st in procedure:
        for text in (st.get("command"), st.get("path"), st.get("diff"), st.get("cwd")):
            for kind, n in _PLACEHOLDER.findall(text or ""):
                v = f"{kind}_{n}"
                if v not in seen:
                    seen.append(v)
    return seen


def _vars_in_shell(cmd: str) -> str:
    """<HOST_1> -> ${HOST_1}. Placeholders sit inside arguments; quoting is left as recorded."""
    return _PLACEHOLDER.sub(lambda m: "${" + f"{m.group(1)}_{m.group(2)}" + "}", cmd)


def _cite(st: dict) -> str:
    return " ".join(st.get("evidence", []))


def _patch_target(st: dict) -> tuple[str, str]:
    """(directory to run patch in, -p level) for a file_edit step's diff (paths are a/<path without leading />)."""
    path = st["path"].replace("\\", "/")
    if path.startswith("/"):
        return "/", "1"
    return st.get("cwd") or ".", "1"


def _diff_body(st: dict) -> str:
    diff = _vars_in_patch(st["diff"])
    if SAFE_HEREDOC in diff:
        raise ValueError("diff contains the heredoc terminator")
    return diff if diff.endswith("\n") else diff + "\n"


def _vars_in_patch(diff: str) -> str:
    # inside an unquoted heredoc ${VAR} expands; everything else must stay literal
    escaped = diff.replace("\\", "\\\\").replace("$", "\\$").replace("`", "\\`")
    return _PLACEHOLDER.sub(lambda m: "${" + f"{m.group(1)}_{m.group(2)}" + "}", escaped)


def to_script(procedure: Sequence[dict], title: str = "Recorded procedure") -> str:
    vs = variables(procedure)
    out = [
        "#!/usr/bin/env bash",
        f"# {title}",
        "# Generated from recorded terminal sessions (python -m generate script). Review before running.",
        "# Every step cites the events it came from. Run with DRY_RUN=1 to print the steps without running them.",
        "set -euo pipefail",
        "",
    ]
    if vs:
        out.append("# Values that were scrubbed from the recording: you must provide them.")
        out += [f': "${{{v}:?set {v} (scrubbed from the recording)}}"' for v in vs]
        out.append("")
    out += [
        'run() { echo "+ $*" >&2; if [ "${DRY_RUN:-0}" != 1 ]; then "$@"; fi; }',
        '# eval, not a subshell: `source`, `export` and `cd` in a step must affect the steps after it',
        'sh_run() { echo "+ $1" >&2; if [ "${DRY_RUN:-0}" != 1 ]; then eval "$1"; fi; }',
        "",
    ]
    cwd = None
    for st in procedure:
        why = f": {st['why']}" if st.get("why") else ""
        out.append(f"# Step {st['n']}{why}  [{_cite(st)}]")
        if st.get("cwd") and st["cwd"] != cwd:
            cwd = st["cwd"]
            out.append(f"run cd {_dq(_vars_in_shell(cwd))}")
        if "command" in st:
            cmd = st["command"]
            head = _head(cmd)
            if head in INTERACTIVE or " ".join(cmd.split()[:2]) in INTERACTIVE:
                out.append(f"# interactive, not automated: {cmd}")
            else:
                out.append(f"sh_run {_dq(_vars_in_shell(cmd))}")
        elif st.get("diff"):
            d, p = _patch_target(st)
            sudo = "sudo " if d == "/" else ""
            out.append(f'if [ "${{DRY_RUN:-0}}" = 1 ]; then echo "+ patch {st["path"]}" >&2; else')
            out.append(f"{sudo}patch --forward -p{p} -d {_dq(_vars_in_shell(d))} <<{SAFE_HEREDOC}")
            out.append(_diff_body(st).rstrip("\n"))
            out.append(SAFE_HEREDOC)
            out.append("fi")
        else:
            out.append(f"# {st['path']} changed but no diff was recorded (binary or too large): do this by hand")
        out.append("")
    out.append('echo "done" >&2')
    return "\n".join(out) + "\n"


def _dq(s: str) -> str:
    """Double-quote for bash, keeping ${VAR} expansion but nothing else."""
    s = s.replace("\\", "\\\\").replace('"', '\\"').replace("`", "\\`")
    s = re.sub(r"\$(?!\{[A-Z]+(?:_[A-Z]+)*_\d+\})", r"\\$", s)
    return f'"{s}"'


def to_dockerfile(procedure: Sequence[dict], base: str = "ubuntu:24.04", title: str = "Recorded procedure") -> str:
    vs = variables(procedure)
    out = [
        "# syntax=docker/dockerfile:1",
        f"# {title}: generated from recorded terminal sessions (python -m generate dockerfile). Review before building.",
        f"FROM {base}",
        "SHELL [\"/bin/bash\", \"-euo\", \"pipefail\", \"-c\"]",
    ]
    out += [f"ARG {v}" for v in vs]
    if any(st.get("diff") for st in procedure):
        out.append("RUN apt-get update && apt-get install -y --no-install-recommends patch && rm -rf /var/lib/apt/lists/*")
    cwd = None
    copied = False
    for st in procedure:
        out.append(f"# Step {st['n']}" + (f": {st['why']}" if st.get("why") else "") + f"  [{_cite(st)}]")
        if st.get("cwd") and st["cwd"] != cwd and "command" in st:
            cwd = st["cwd"]
            out.append(f"WORKDIR {_vars_in_shell(cwd).replace('~', '/root')}")
            if not copied:
                out.append("# the recorded steps ran inside a project directory: build with that project as the context")
                out.append("COPY . .")
                copied = True
        if "command" in st:
            cmd = re.sub(r"^\s*sudo\s+", "", st["command"])
            head = _head(cmd)
            if head in INTERACTIVE:
                out.append(f"# interactive, not automated: {cmd}")
            elif head in NOT_IN_CONTAINERS:
                out.append(f"# not available in containers: {cmd}")
            elif head in ("curl", "wget") and ("localhost" in cmd or "127.0.0.1" in cmd):
                out.append(f"# verification against a running service, see HEALTHCHECK idea: {cmd}")
            elif _activate(cmd):
                venv = _activate(cmd)
                venv = venv if venv.startswith("/") else f"{(cwd or '/').rstrip('/')}/{venv}"
                venv = _vars_in_shell(venv).replace("~", "/root")
                out.append(f"ENV VIRTUAL_ENV={venv} PATH={venv}/bin:$PATH")
            elif re.match(r"^export\s+[A-Za-z_][A-Za-z0-9_]*=", cmd):
                out.append("ENV " + _vars_in_shell(cmd.split(None, 1)[1]))
            elif head == "cd":
                out.append(f"WORKDIR {cmd.split(None, 1)[1] if ' ' in cmd else '/root'}")
            else:
                out.append(f"RUN {_vars_in_shell(cmd)}")
        elif st.get("diff"):
            d, p = _patch_target(st)
            body = _diff_body(st).rstrip("\n")
            out.append(f"RUN <<{SAFE_HEREDOC} patch --forward -p{p} -d {_vars_in_shell(d) if d != '.' else '.'}")
            out.append(body)
            out.append(SAFE_HEREDOC)
    return "\n".join(out) + "\n"


_ACTIVATE = re.compile(r"^\s*(?:source|\.)\s+(\S+)/bin/activate\s*$")


def _activate(cmd: str) -> str | None:
    """The venv directory if ``cmd`` activates a Python virtual environment."""
    m = _ACTIVATE.match(cmd)
    return m.group(1) if m else None
