"""Incident post-mortems from mistake-tagged event chains (KAN-78).

Design: docs/design/postmortem.md. Deterministic: the same session always
gives the same report. An optional wording model may rephrase the Markdown,
but its output is only accepted if every statement cites evidence ids that
exist in the report.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

from contracts import evidence_id

INTERRUPTED = 130  # Ctrl-C: the user stopped it, not a failure
_WRAPPERS = {"sudo", "env", "time", "nohup", "command", "doas"}
_ERROR_LINE = re.compile(
    r"(?i)\b(error|errno|failed|failure|fatal|emerg|denied|not found|no such|conflict|traceback|"
    r"exception|refused|invalid|cannot|can't|unable|externally-managed)\b"
)


# -- inputs -------------------------------------------------------------------


def apply_corrections(events: Sequence[dict]) -> list[dict]:
    """Return events with the latest correction per (annotation, field) applied.
    Corrections never edit in place; this builds corrected copies."""
    latest: dict[tuple[str, str], tuple[str, int, Any]] = {}
    for i, e in enumerate(events):
        if e.get("type") == "correction":
            key = (e["target"], e["field"])
            stamp = (e.get("t_created") or "", i)
            if key not in latest or stamp >= latest[key][:2]:
                latest[key] = (*stamp, e["new"])
    out = []
    for e in events:
        if e.get("type") == "annotation":
            fixes = {f: v for (t, f), (_, _, v) in latest.items() if t == e["id"]}
            if fixes:
                changed = sorted(f for f, v in fixes.items() if f != "reviewed" and e.get(f) != v)
                # a person looked at it; "changed_fields" are the ones they actually changed
                e = {**e, **fixes, "corrected_fields": sorted(fixes), "changed_fields": changed, "reviewed": True}
        out.append(e)
    return out


@dataclass
class Step:
    """A command or file edit, with its (corrected) annotation if any."""

    ev: dict
    ann: dict | None
    eid: str  # evidence id

    @property
    def kind(self) -> str:
        return self.ev["type"]

    @property
    def t(self) -> float:
        return float(self.ev["t"])

    @property
    def goal(self) -> str | None:
        return (self.ann or {}).get("goal")

    @property
    def failed(self) -> bool:
        if self.ann is not None and (self.ann.get("mistake") or self.ann.get("result") == "failure"):
            return True
        if self.ann is None and self.kind == "command":
            code = self.ev.get("exit_code")
            return code not in (None, 0, INTERRUPTED)
        return False

    @property
    def succeeded(self) -> bool:
        if self.failed:
            return False
        if self.ann is not None:
            return self.ann.get("result") in ("success", None)
        return self.kind == "file_edit" or self.ev.get("exit_code") in (0, INTERRUPTED)

    @property
    def head(self) -> str:
        """Command identity for 'same thing retried': first word (no sudo) + first argument."""
        words = (self.ev.get("command") or "").split()
        while words and words[0] in _WRAPPERS:
            words = words[1:]
        if not words:
            return ""
        if self.kind == "file_edit":
            return "edit " + self.ev["path"]
        return " ".join(words[:2]) if len(words) > 1 and not words[1].startswith(("/", "~", ".")) else words[0]

    def label(self) -> str:
        if self.kind == "file_edit":
            return f"{self.ev['change'].capitalize()} `{self.ev['path']}` with {self.ev['tool']}"
        code = self.ev.get("exit_code")
        suffix = f" (exit {code})" if code not in (None, 0) else ""
        return f"`{self.ev['command']}`{suffix}"


def build_steps(session_id: str, events: Sequence[dict]) -> list[Step]:
    events = apply_corrections(events)
    anns: dict[str, dict] = {}
    for e in events:
        if e.get("type") == "annotation":
            anns[e["target"]] = e
    edit_cmds = {e.get("command_id") for e in events if e.get("type") == "file_edit"}
    steps = []
    for e in events:
        if e.get("type") == "file_edit" or (e.get("type") == "command" and e["id"] not in edit_cmds):
            steps.append(Step(e, anns.get(e["id"]), evidence_id(session_id, e["id"])))
    steps.sort(key=lambda s: s.t)
    return steps


def error_lines(output: str | None, limit: int = 3) -> list[str]:
    if not output:
        return []
    lines = [ln.strip() for ln in output.splitlines() if ln.strip()]
    hits = [ln for ln in lines if _ERROR_LINE.search(ln)]
    return (hits or lines[-limit:])[:limit]


# -- chains -------------------------------------------------------------------


@dataclass
class Chain:
    steps: list[Step] = field(default_factory=list)
    resolution: Step | None = None
    inferred: bool = False


def find_chains(steps: Sequence[Step], max_gap: int = 12) -> list[Chain]:
    """Start at a failed step; follow until a related step succeeds.

    Related = same goal, same command head (a retry), or an edit of a file
    already touched in the chain. ``max_gap`` unrelated steps in a row end
    an unresolved chain (the user moved on)."""
    chains: list[Chain] = []
    used: set[int] = set()
    i = 0
    while i < len(steps):
        s = steps[i]
        if i in used or not s.failed:
            i += 1
            continue
        ch = Chain([s])
        used.add(i)
        goals = {s.goal} - {None}
        heads = {s.head}
        failing_keys = {("g", g) for g in goals} | {("h", s.head)}
        roots = {_root(s)} - {None}
        gap = 0
        j = i + 1
        while j < len(steps):
            n = steps[j]
            related = (n.goal in goals) or (n.head in heads) or (_root(n) in roots and roots)
            if not related:
                gap += 1
                if gap > max_gap:
                    break
                j += 1
                continue
            gap = 0
            ch.steps.append(n)
            used.add(j)
            if n.failed:
                goals |= {n.goal} - {None}
                heads.add(n.head)
                failing_keys |= {("g", n.goal), ("h", n.head)}
            elif n.succeeded and (("g", n.goal) in failing_keys or ("h", n.head) in failing_keys) and n.kind == "command":
                ch.resolution = n
                break
            j += 1
        if ch.resolution is None:
            # everything after the last failure under the same goal tree succeeded
            last_fail = max(k for k, st in enumerate(ch.steps) if st.failed)
            tail = ch.steps[last_fail + 1 :]
            if tail and all(st.succeeded for st in tail):
                ch.resolution, ch.inferred = tail[-1], True
        chains.append(ch)
        i += 1
    return chains


def _root(s: Step) -> str | None:
    """The goal-tree root annotation this step belongs to (its parent, or itself)."""
    if s.ann is None:
        return None
    return s.ann.get("parent") or s.ann["id"]


# -- report -------------------------------------------------------------------

_READ_ONLY = {
    "ls", "cat", "less", "more", "head", "tail", "grep", "rg", "find", "which", "ps", "top", "htop", "df", "du",
    "pwd", "man", "history", "file", "stat", "tree", "env", "printenv", "id", "whoami", "uptime", "free",
    "git status", "git log", "git diff", "git show", "git branch", "docker ps", "docker images", "docker logs",
    "kubectl get", "kubectl describe", "kubectl logs", "systemctl status", "journalctl",
}


def _read_only(s: Step) -> bool:
    """Diagnostics (looking around) are part of the timeline but not of the fix."""
    head = s.head
    return head in _READ_ONLY or head.split(" ")[0] in _READ_ONLY


def _item(step: Step, text: str) -> dict:
    return {"t": step.t, "text": text, "evidence": [step.eid]}


def build_report(session_id: str, ch: Chain, root_goals: dict[str, str]) -> dict:
    first = ch.steps[0]
    t0 = first.t
    timeline = []
    for s in ch.steps:
        what = s.label()
        summary = (s.ann or {}).get("summary")
        status = "failed" if s.failed else "ok"
        timeline.append(_item(s, f"+{s.t - t0:.0f}s {what}: {status}." + (f" {summary}" if summary else "")))

    # root cause: a mistaken edit beats the command that exposed it
    cause_step = first
    exposed_by = next((s for s in ch.steps if s.failed and s.kind == "command"), None)
    root_cause = {"text": f"{cause_step.label()}" + (f": {cause_step.ann['summary']}" if cause_step.ann and cause_step.ann.get("summary") else ""),
                  "evidence": [cause_step.eid]}
    if cause_step.kind == "file_edit":
        root_cause["diff"] = cause_step.ev.get("diff")
    if exposed_by is not None:
        root_cause["errors"] = error_lines(exposed_by.ev.get("output"))
        if exposed_by is not cause_step:
            root_cause["evidence"].append(exposed_by.eid)

    fixes = [s for s in ch.steps[1:] if not s.failed and (s.kind == "file_edit" or not _read_only(s))]
    fix_steps = fixes + ([ch.resolution] if ch.resolution and ch.resolution not in fixes else [])
    fix = [
        {"text": f"{s.label()}" + (f": {s.ann['summary']}" if s.ann and s.ann.get("summary") else ""),
         "evidence": [s.eid], **({"diff": s.ev.get("diff")} if s.kind == "file_edit" else {})}
        for s in fix_steps
    ]

    lessons = []
    for s in ch.steps:
        if s.failed and s.ann and s.ann.get("summary"):
            lessons.append({"text": f"Watch for: {s.ann['summary']}", "evidence": [s.eid]})
    if cause_step.kind == "file_edit" and cause_step.ev.get("is_config") and exposed_by:
        lessons.append({"text": f"Test config changes with `{exposed_by.ev['command']}` before applying them.",
                        "evidence": [cause_step.eid, exposed_by.eid]})

    goal = root_goals.get(_root(first) or "", None) or first.goal or first.head
    end = ch.resolution.t if ch.resolution else ch.steps[-1].t
    status = "resolved" + (" (inferred)" if ch.inferred else "") if ch.resolution else "unresolved"
    summary = {
        "text": f"{first.label()} failed while working on: {goal}. "
                + (f"Resolved after {len(ch.steps) - 1} more step(s), {end - t0:.0f}s later." if ch.resolution
                   else "Not resolved in this session."),
        "evidence": [first.eid] + ([ch.resolution.eid] if ch.resolution else []),
    }
    return {
        "session": session_id,
        "title": f"Incident: {goal}",
        "status": status,
        "started": t0,
        "duration": round(end - t0, 3),
        "summary": summary,
        "timeline": timeline,
        "root_cause": root_cause,
        "fix": fix,
        "lessons": lessons,
        "evidence": sorted({e for part in _cited(timeline, [root_cause, summary], fix, lessons) for e in part}),
    }


def _cited(*groups) -> list[list[str]]:
    return [x["evidence"] for g in groups for x in g]


def postmortems(header: dict, events: Sequence[dict]) -> list[dict]:
    sid = header["id"]
    steps = build_steps(sid, events)
    corrected = apply_corrections(events)
    root_goals = {e["id"]: e.get("goal") for e in corrected if e.get("type") == "annotation" and e.get("parent") is None}
    said: dict[str, list[dict]] = {}
    for e in events:  # the person's own answers to "why?" (KAN-58), quoted as they gave them
        if e.get("type") == "explanation":
            said.setdefault(e["target"], []).append(
                {"question": e["question"], "text": e["answer"], "evidence": [evidence_id(sid, e["id"])]})
    reports = []
    for ch in find_chains(steps):
        r = build_report(sid, ch, root_goals)
        quotes = [q for st in ch.steps for q in said.get(st.ev["id"], [])]
        if quotes:
            r["explanations"] = quotes
            r["evidence"] = sorted(set(r["evidence"]) | {e for q in quotes for e in q["evidence"]})
        reports.append(r)
    return reports


# -- rendering ------------------------------------------------------------------


def _cite(ev: Sequence[str]) -> str:
    return " " + " ".join(f"[{e}]" for e in ev)


def render_markdown(r: dict) -> str:
    out = [f"## {r['title']}", "", f"**Status:** {r['status']} · **Duration:** {r['duration']:.0f}s", "",
           r["summary"]["text"] + _cite(r["summary"]["evidence"]), "", "### Timeline", ""]
    out += [f"- {x['text']}{_cite(x['evidence'])}" for x in r["timeline"]]
    rc = r["root_cause"]
    out += ["", "### Root cause", "", rc["text"] + _cite(rc["evidence"])]
    if rc.get("errors"):
        out += ["", "```", *rc["errors"], "```"]
    if rc.get("diff"):
        out += ["", "```diff", rc["diff"].rstrip("\n"), "```"]
    out += ["", "### Fix", ""]
    if not r["fix"]:
        out.append("- No fix recorded in this session.")
    for f in r["fix"]:
        out.append(f"- {f['text']}{_cite(f['evidence'])}")
        if f.get("diff"):
            out += ["", "```diff", f["diff"].rstrip("\n"), "```", ""]
    if r["lessons"]:
        out += ["", "### Lessons", ""]
        out += [f"- {x['text']}{_cite(x['evidence'])}" for x in r["lessons"]]
    if r.get("explanations"):
        out += ["", "### In the author's words", ""]
        out += [f"- *{x['question']}* {x['text']}{_cite(x['evidence'])}" for x in r["explanations"]]
    return "\n".join(out).rstrip() + "\n"


_CITATION = re.compile(r"\[([^\[\]\s]+#[^\[\]\s]+)\]")


def check_wording(text: str, report: dict) -> str | None:
    """None if acceptable; else why it was rejected. Every bullet and paragraph
    must cite, and every citation must be one of the report's evidence ids."""
    allowed = set(report["evidence"])
    cited = set(_CITATION.findall(text))
    if not cited:
        return "no citations"
    unknown = cited - allowed
    if unknown:
        return f"cites unknown evidence {sorted(unknown)[:3]}"
    in_code = False
    for block in text.split("\n"):
        line = block.strip()
        if line.startswith("```"):
            in_code = not in_code
            continue
        if in_code or not line or line.startswith("#") or line.startswith("**Status"):
            continue
        if not _CITATION.search(line):
            return f"uncited statement: {line[:60]!r}"
    return None


def render(report: dict, wording_model: Callable[[dict, str], str] | None = None) -> tuple[str, str]:
    """(markdown, how) — how is 'deterministic', 'model' or 'deterministic (model rejected: …)'."""
    base = render_markdown(report)
    if wording_model is None:
        return base, "deterministic"
    try:
        text = wording_model(report, base)
    except Exception as e:  # a model failure must not lose the report
        return base, f"deterministic (model error: {type(e).__name__})"
    why = check_wording(text, report)
    if why:
        return base, f"deterministic (model rejected: {why})"
    return text, "model"
