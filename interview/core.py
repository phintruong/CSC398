"""The "why?" interviewer (KAN-58): ask the person about their mistakes and discoveries.

A teaching assistant learns most from the moments where something went wrong
and from how the person found the way out. For each incident chain
(postmortem.find_chains) we ask about:

* the **mistake** (root cause): what did you expect, and what went wrong?
* the **discovery**: the first diagnostic step after the failure (what were
  you looking for?) and the fix (how did you figure it out?).

Questions are deterministic templates; an optional model can rephrase them
through the runtime interface, accepted only if it stays a short question.
Answers are stored as ``explanation`` events, scrubbed first. Voice answers
(KAN-59) will use the same event with ``source: "voice"``.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path
from typing import Any

from contracts import evidence_id, loads_session, validate_event
from postmortem.core import Step, _read_only, build_steps, find_chains
from scrubber import PlaceholderMap, Scrubber

MAX_QUESTIONS = 5
_AUTHOR = re.compile(r"^[A-Za-z0-9_.-]{1,40}$")


def _what(s: Step) -> str:
    if s.kind == "file_edit":
        return f"the edit to `{s.ev['path'].replace(chr(92), '/').rsplit('/', 1)[-1]}`"
    return f"`{s.ev['command']}`"


def _q_mistake(s: Step) -> str:
    if s.kind == "file_edit":
        return f"When you made {_what(s)}, what were you trying to change, and what did you miss?"
    return f"When you ran {_what(s)}, what did you expect to happen, and why do you think it failed?"


def _q_diagnose(s: Step, fail: Step) -> str:
    return f"After {_what(fail)} failed, you ran {_what(s)}. What were you looking for?"


def _q_fix(s: Step) -> str:
    return f"How did you figure out that {_what(s)} was the fix?"


def explained_targets(events: list[dict]) -> set[str]:
    return {e["target"] for e in events if e.get("type") == "explanation"}


def questions(header: dict, events: list[dict], model: Any = None, max_questions: int = MAX_QUESTIONS) -> list[dict]:
    """Questions for the moments that most deserve a "why?", most important first."""
    sid = header["id"]
    done = explained_targets(events)
    out: list[dict] = []

    def add(step: Step, kind: str, text: str) -> None:
        if step.ev["id"] in done or any(q["target"] == step.ev["id"] for q in out):
            return
        out.append({"id": f"q{len(out) + 1}", "target": step.ev["id"], "kind": kind, "question": text,
                    "evidence": [step.eid]})

    for ch in find_chains(build_steps(sid, events)):
        first = ch.steps[0]
        add(first, "mistake", _q_mistake(first))
        diag = next((s for s in ch.steps[1:] if not s.failed and _read_only(s)), None)
        if diag:
            add(diag, "discovery", _q_diagnose(diag, first))
        fix = next((s for s in ch.steps[1:] if not s.failed and not _read_only(s)), None)
        if fix:
            add(fix, "discovery", _q_fix(fix))
    out = out[:max_questions]
    if model is not None:
        for q in out:
            q["question"] = _rephrase(model, sid, q)
    return out


def _rephrase(model: Any, sid: str, q: dict) -> str:
    prompt = ("Rephrase this question to a learner so it sounds natural and kind. Keep its meaning, ask only one "
              f"question, under 30 words, no new facts.\n\n{q['question']}")
    try:
        text = model.complete("interview", q["evidence"], prompt).strip()
    except Exception:
        return q["question"]
    ok = text.endswith("?") and text.count("?") == 1 and len(text.split()) <= 30 and "#" not in text
    return text if ok else q["question"]


def answer(path: Path, target: str, question: str, text: str, author: str, source: str = "typed",
           confidence: float | None = None, now: str | None = None) -> dict:
    """Append the person's answer as an explanation event, scrubbed first."""
    if not _AUTHOR.match(author or ""):
        raise ValueError("author must be a short handle, not a full name")
    if source not in ("typed", "voice"):
        raise ValueError("source must be typed or voice")
    header, events = loads_session(Path(path).read_text(encoding="utf-8"), strict=False)
    if target not in {e["id"] for e in events if e.get("type") in ("command", "file_edit")}:
        raise ValueError(f"{target!r} is not a command or file edit in this session")
    if not text.strip():
        raise ValueError("answer is empty")
    # numbering continues after the recording's placeholders, so <HOST_1> keeps its meaning
    scrub = Scrubber(mapping=PlaceholderMap.continuing((header.get("redaction") or {}).get("replaced", {})))
    q_clean, a_clean = scrub.scrub_texts([question, text.strip()])
    n = sum(1 for e in events if e.get("type") == "explanation") + 1
    ev = {"type": "explanation", "id": f"e{n}", "target": target, "question": q_clean, "answer": a_clean,
          "source": source, "author": author,
          "t_created": now or dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}
    if confidence is not None:
        ev["confidence"] = confidence
    errs = validate_event(ev)
    if errs:
        raise ValueError("; ".join(errs))
    with Path(path).open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(ev, ensure_ascii=False) + "\n")
    return ev


def explanations_by_target(session_id: str, events: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for e in events:
        if e.get("type") == "explanation":
            out.setdefault(e["target"], []).append(
                {"question": e["question"], "text": e["answer"], "evidence": [evidence_id(session_id, e["id"])]})
    return out
