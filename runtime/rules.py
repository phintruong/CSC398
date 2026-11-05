"""A rule-based annotator that speaks the runtime interface (no model needed).

Uses only the event in the prompt: exit code and error words decide
result/mistake, the command decides the tool, and a short template makes
the summary. It's the floor any real Model 1 must beat on the benchmark,
and it makes end-to-end tests deterministic.
"""

from __future__ import annotations

import json
import re

_ERR = re.compile(r"(?i)\b(error|failed|failure|fatal|emerg|denied|not found|no such|conflict|traceback|refused|invalid|cannot)\b")
_WRAP = {"sudo", "env", "time", "nohup", "doas"}
_NAVIGATION = {"cd", "pwd", "clear", "exit", "history"}


def _target(prompt: str) -> dict:
    for line in reversed(prompt.strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            return json.loads(line)
    raise ValueError("no target event in prompt")


def _tool(cmd: str) -> str | None:
    words = [w for w in cmd.split() if "=" not in w or w.startswith("-")]
    while words and words[0] in _WRAP:
        words = words[1:]
    return words[0] if words else None


class RuleAnnotator:
    name = "rules-v1"

    def complete(self, task: str, evidence_ids: list[str], prompt: str) -> str:
        if task != "annotate":
            raise KeyError(task)
        ev = _target(prompt)
        if ev["type"] == "file_edit":
            name = ev["path"].replace("\\", "/").rsplit("/", 1)[-1]
            return json.dumps({"goal": f"Edit {name}", "tool": ev.get("tool"), "result": "success", "mistake": False,
                               "summary": f"{ev['change'].capitalize()} {name}" + (" (config file)." if ev.get("is_config") else ".")})
        cmd = ev.get("command") or ""
        if (cmd.split() or [""])[0] in _NAVIGATION:
            raise KeyError("navigation only")  # nothing to teach; don't let `cd` become a goal-tree root
        out = ev.get("output") or ""
        code = ev.get("exit_code")
        errors = [ln.strip() for ln in out.splitlines() if _ERR.search(ln)]
        failed = (code not in (None, 0, 130)) or (code is None and bool(errors))
        tool = _tool(cmd)
        summary = f"`{cmd}` failed: {errors[0][:120]}" if failed and errors else (
            f"`{cmd}` failed with exit {code}." if failed else f"Ran `{cmd}`.")
        return json.dumps({"goal": f"Use {tool}" if tool else "Run a command", "tool": tool,
                           "result": "failure" if failed else ("success" if code is not None or out or not errors else "unknown"),
                           "mistake": failed, "summary": summary})
