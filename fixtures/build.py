"""Deterministic mock sessions + recorded model replies (KAN-106).

    python -m fixtures build fixtures/data      # regenerate (byte-identical)

Every session validates against event schema v1 (contracts/events.py).
Values that would identify someone are already placeholders, as if scrubbed.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from contracts import SCHEMA_VERSION, dumps_session, evidence_id
from fileedit.detect import is_config
from fileedit.snapshot import unified_diff

PROVENANCE = {"source": "fixtures", "consent": "synthetic", "license": "CC0-1.0"}
REDACTION = {"version": "synthetic", "replaced": {}}
PROMPT_USER = "<USER_1>"


@dataclass
class S:
    """Tiny builder: keeps a clock, numbers ids, and remembers file contents for diffs."""

    id: str
    term: dict = field(default_factory=lambda: {"cols": 120, "rows": 32})
    t: float = 0.0
    n: int = 0
    events: list = field(default_factory=list)
    files: dict = field(default_factory=dict)
    last_cmd: str | None = None
    cwd: str = f"/home/{PROMPT_USER}"

    def _id(self, p: str) -> str:
        self.n += 1
        return f"{p}{self.n}"

    def cmd(self, command: str, output: str = "", code: int = 0, dur: float = 1.0, think: float = 3.0) -> str:
        self.t = round(self.t + think, 3)
        if command.startswith("cd "):
            target = command[3:].strip()
            if target.startswith("~"):
                target = f"/home/{PROMPT_USER}" + target[1:]
            self.cwd = target if target.startswith("/") else f"{self.cwd}/{target}"
        eid = self._id("c")
        self.events.append({
            "type": "command", "id": eid, "t": self.t, "t_end": round(self.t + dur, 3), "duration": dur,
            "command": command, "cwd": self.cwd, "exit_code": code, "output": output,
            "output_truncated": False, "term": dict(self.term),
        })
        self.last_cmd = eid
        self.t = round(self.t + dur, 3)
        return eid

    def edit(self, path: str, new: str, tool: str = "vim", dur: float = 20.0) -> str:
        old = self.files.get(path)
        cid = self.cmd(f"{'sudo ' if path.startswith('/etc') else ''}{tool} {path}", "", 0, dur)
        self.files[path] = new
        eid = self._id("f")
        self.events.append({
            "type": "file_edit", "id": eid, "t": self.events[-1]["t"], "t_end": self.events[-1]["t_end"],
            "path": path, "tool": tool, "command": self.events[-1]["command"], "command_id": cid,
            "change": "created" if old is None else "modified", "is_config": is_config(path),
            "diff": unified_diff(path, old or "", new), "binary": False, "truncated": False,
            "redaction": "synthetic",
        })
        return eid

    def resize(self, cols: int, rows: int) -> str:
        self.t = round(self.t + 0.5, 3)
        self.term = {"cols": cols, "rows": rows}
        eid = self._id("r")
        self.events.append({"type": "resize", "id": eid, "t": self.t, "cols": cols, "rows": rows})
        return eid

    def ann(self, target: str, goal: str, tool: str | None, result: str, summary: str,
            mistake: bool = False, parent: str | None = None) -> str:
        eid = self._id("a")
        self.events.append({
            "type": "annotation", "id": eid, "target": target, "goal": goal, "tool": tool, "result": result,
            "mistake": mistake, "summary": summary, "parent": parent, "model": "mock-model-1",
        })
        return eid

    def correct(self, target: str, fld: str, old: Any, new: Any, author: str = "reviewer-1") -> str:
        eid = self._id("x")
        self.events.append({"type": "correction", "id": eid, "target": target, "field": fld, "old": old, "new": new,
                            "author": author, "t_created": "2026-11-01T12:00:00+00:00"})
        return eid

    def header(self) -> dict:
        return {"type": "session", "schema": SCHEMA_VERSION, "id": self.id, "origin": "mock",
                "started": "2026-10-20T15:00:00+00:00", "shell": "bash", "term": {"cols": 120, "rows": 32},
                "provenance": PROVENANCE, "redaction": REDACTION}

    def dumps(self) -> str:
        return dumps_session(self.header(), self.events)


NGINX_DEFAULT = """server {
\tlisten 80 default_server;
\troot /var/www/html;
\tserver_name _;
\tlocation / {
\t\ttry_files $uri $uri/ =404;
\t}
}
"""


def nginx_port(sid: str, typo: bool) -> S:
    """Move nginx to port 8080: the classic config-test-reload loop."""
    s = S(sid)
    root = None
    c = s.cmd("cd /etc/nginx/sites-available", "")
    s.files["/etc/nginx/sites-available/default"] = NGINX_DEFAULT
    c_cat = s.cmd("cat default", NGINX_DEFAULT.rstrip("\n"))
    root = s.ann(c_cat, "Serve the site on port 8080 instead of 80", "cat", "success",
                 "Look at the current nginx site config.")
    if typo:
        f1 = s.edit("/etc/nginx/sites-available/default", NGINX_DEFAULT.replace("listen 80 default_server;", "listen 8080 default_server"))
        a_edit = s.ann(f1, "Change the listen port", "vim", "partial", "Changed the port but dropped the semicolon.", True, root)
        c_t1 = s.cmd("sudo nginx -t", "nginx: [emerg] invalid parameter \"root\" in /etc/nginx/sites-enabled/default:3\n"
                     "nginx: configuration file /etc/nginx/nginx.conf test failed", 1)
        s.ann(c_t1, "Check the config before reloading", "nginx", "failure", "Config test fails: missing semicolon on the listen line.", True, root)
        f2 = s.edit("/etc/nginx/sites-available/default", NGINX_DEFAULT.replace("listen 80", "listen 8080"))
        s.ann(f2, "Fix the syntax error", "vim", "success", "Added the missing semicolon.", False, root)
    else:
        f2 = s.edit("/etc/nginx/sites-available/default", NGINX_DEFAULT.replace("listen 80", "listen 8080"))
        s.ann(f2, "Change the listen port", "vim", "success", "Changed listen 80 to listen 8080.", False, root)
    c_t2 = s.cmd("sudo nginx -t", "nginx: the configuration file /etc/nginx/nginx.conf syntax is ok\n"
                 "nginx: configuration file /etc/nginx/nginx.conf test is successful")
    s.ann(c_t2, "Check the config before reloading", "nginx", "success", "Config test passes.", False, root)
    c_r = s.cmd("sudo systemctl reload nginx", "")
    s.ann(c_r, "Apply the change", "systemctl", "success", "Reload nginx without dropping connections.", False, root)
    c_curl = s.cmd("curl -sI http://localhost:8080 | head -1", "HTTP/1.1 200 OK")
    a = s.ann(c_curl, "Verify the site answers on 8080", "curl", "success", "Site answers on the new port.", False, root)
    if typo:
        s.correct(a_edit, "summary", "Changed the port but dropped the semicolon.",
                  "Changed the port to 8080 but deleted the semicolon at the end of the listen line.")
    return s


def python_venv() -> S:
    s = S("mock-python-venv")
    s.cmd("cd ~/projects/report-tool", "")
    c1 = s.cmd("pip install -r requirements.txt",
               "error: externally-managed-environment\n\n× This environment is externally managed\n"
               "╰─> To install Python packages system-wide, try apt install python3-xyz", 1, 2.0)
    root = s.ann(c1, "Install the project's dependencies", "pip", "failure",
                 "System Python refuses pip installs (PEP 668).", True)
    c2 = s.cmd("python3 -m venv .venv", "", 0, 3.0)
    s.ann(c2, "Create an isolated environment", "python3", "success", "Create a virtual environment in .venv.", False, root)
    c3 = s.cmd("source .venv/bin/activate", "")
    s.ann(c3, "Use the environment", "source", "success", "Activate the venv.", False, root)
    c4 = s.cmd("pip install -r requirements.txt", "Successfully installed jinja2-3.1.4 markupsafe-2.1.5 pandas-2.2.3", 0, 25.0)
    s.ann(c4, "Install the project's dependencies", "pip", "success", "Dependencies install inside the venv.", False, root)
    c5 = s.cmd("python report.py --month 2026-10", "Wrote report-2026-10.html (14 pages)")
    s.ann(c5, "Run the report", "python", "success", "Report generated.", False, root)
    return s


def git_conflict() -> S:
    s = S("mock-git-conflict")
    s.cmd("cd ~/src/webapp", "")
    s.files["src/config.js"] = "export const API_URL = 'https://<HOST_1>/v1';\nexport const TIMEOUT = 5000;\n"
    c1 = s.cmd("git pull origin main",
               "Auto-merging src/config.js\nCONFLICT (content): Merge conflict in src/config.js\n"
               "Automatic merge failed; fix conflicts and then commit the result.", 1)
    root = s.ann(c1, "Update the branch with main", "git", "failure", "Pull hits a merge conflict in src/config.js.", True)
    c2 = s.cmd("git status --short", "UU src/config.js")
    s.ann(c2, "See which files conflict", "git", "success", "One conflicted file.", False, root)
    conflicted = ("<<<<<<< HEAD\nexport const API_URL = 'https://<HOST_1>/v1';\nexport const TIMEOUT = 5000;\n=======\n"
                  "export const API_URL = 'https://<HOST_1>/v2';\nexport const TIMEOUT = 5000;\n>>>>>>> origin/main\n")
    s.files["src/config.js"] = conflicted
    f = s.edit("src/config.js", "export const API_URL = 'https://<HOST_1>/v2';\nexport const TIMEOUT = 5000;\n")
    s.ann(f, "Resolve the conflict", "vim", "success", "Keep main's v2 API URL and remove the conflict markers.", False, root)
    c3 = s.cmd("git add src/config.js && git commit --no-edit", "[feature/login 3f2a91c] Merge branch 'main' of <HOST_2>:team/webapp")
    s.ann(c3, "Finish the merge", "git", "success", "Merge committed.", False, root)
    return s


def docker_build() -> S:
    s = S("mock-docker-build")
    s.cmd("cd ~/src/api", "")
    s.files["Dockerfile"] = "FROM python:3.12-slim\nWORKDIR /app\nCOPY . .\nRUN pip install -r requirement.txt\nCMD [\"python\", \"app.py\"]\n"
    c1 = s.cmd("docker build -t api:dev .",
               "Step 4/5 : RUN pip install -r requirement.txt\n"
               "ERROR: Could not open requirements file: [Errno 2] No such file or directory: 'requirement.txt'\n"
               "The command '/bin/sh -c pip install -r requirement.txt' returned a non-zero code: 1", 1, 12.0)
    root = s.ann(c1, "Build the API image", "docker", "failure", "Build fails: typo in the requirements file name.", True)
    c2 = s.cmd("ls", "Dockerfile  app.py  requirements.txt")
    s.ann(c2, "Find the right file name", "ls", "success", "The file is requirements.txt (plural).", False, root)
    f = s.edit("Dockerfile", s.files["Dockerfile"].replace("requirement.txt", "requirements.txt"))
    s.ann(f, "Fix the Dockerfile", "vim", "success", "Correct the file name in the RUN line.", False, root)
    c3 = s.cmd("docker build -t api:dev .", "Successfully built 9c1e2f7a0b3d\nSuccessfully tagged api:dev", 0, 40.0)
    s.ann(c3, "Build the API image", "docker", "success", "Image builds.", False, root)
    c4 = s.cmd("docker run --rm -p 8000:8000 api:dev", "INFO:     Uvicorn running on http://0.0.0.0:8000", 130, 15.0)
    s.ann(c4, "Run the image", "docker", "success", "Server starts; stopped with Ctrl-C (exit 130).", False, root)
    return s


def resize_session() -> S:
    s = S("mock-resize-htop")
    c1 = s.cmd("uptime", " 15:02:11 up 12 days,  3:41,  1 user,  load average: 3.92, 3.80, 3.71")
    root = s.ann(c1, "Find out why the machine is slow", "uptime", "success", "Load is high (~3.9).")
    s.resize(200, 50)
    c2 = s.cmd("htop", "", 0, 30.0)
    s.ann(c2, "Find the busy process", "htop", "success", "A runaway python worker uses 390% CPU.", False, root)
    s.resize(120, 32)
    c3 = s.cmd("systemctl restart report-worker", "", 0)
    s.ann(c3, "Stop the runaway process", "systemctl", "success", "Restart the worker service.", False, root)
    return s


def build_all() -> dict[str, S]:
    sessions = [
        nginx_port("mock-nginx-port", typo=True),
        nginx_port("mock-nginx-port-rerun", typo=False),
        python_venv(),
        git_conflict(),
        docker_build(),
        resize_session(),
    ]
    return {s.id: s for s in sessions}


# -- recorded model replies ------------------------------------------------------


def request_key(task: str, evidence_ids: list[str]) -> str:
    return hashlib.sha256(json.dumps([task, sorted(evidence_ids)]).encode()).hexdigest()[:16]


def build_replies(sessions: dict[str, S]) -> list[dict]:
    """Annotate / summarize / answer replies so other groups can test without a model."""
    out = []
    for sid, s in sessions.items():
        anns = [e for e in s.events if e["type"] == "annotation"]
        for a in anns:
            ids = [evidence_id(sid, a["target"])]
            reply = {k: a[k] for k in ("goal", "tool", "result", "mistake", "summary")}
            out.append({"task": "annotate", "key": request_key("annotate", ids), "evidence_ids": ids,
                        "reply": json.dumps(reply, ensure_ascii=False)})
        roots = [a for a in anns if a["parent"] is None]
        ids = [evidence_id(sid, e["id"]) for e in s.events if e["type"] == "command"]
        summary = " ".join(a["summary"] for a in anns if a["result"] in ("success", "failure"))
        out.append({"task": "summarize", "key": request_key("summarize", ids), "evidence_ids": ids,
                    "reply": f"{roots[0]['goal'] if roots else sid}: {summary}"})
    # one Q&A pair with citations
    ng = sessions["mock-nginx-port"]
    fix = [e for e in ng.events if e["type"] == "file_edit"][-1]
    ids = [evidence_id(ng.id, fix["id"])]
    out.append({"task": "answer", "key": request_key("answer", ids), "evidence_ids": ids,
                "question": "How did I fix the nginx config test failure?",
                "reply": f"You added the missing semicolon after `listen 8080 default_server` [{ids[0]}]."})
    return out


def write(out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    sessions = build_all()
    paths = []
    for sid, s in sessions.items():
        p = out_dir / f"{sid}.events.jsonl"
        p.write_bytes(s.dumps().encode("utf-8"))
        paths.append(p)
    p = out_dir / "model_replies.jsonl"
    p.write_bytes(("\n".join(json.dumps(r, ensure_ascii=False) for r in build_replies(sessions)) + "\n").encode("utf-8"))
    paths.append(p)
    return paths
