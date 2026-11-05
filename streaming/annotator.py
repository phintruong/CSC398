"""Annotate each event as soon as it is closed, from past context only.

    ann = StreamingAnnotator(model, header)
    for ev in live_events:            # command / file_edit / resize, in time order
        for out in ann.feed(ev):      # annotation events, emitted immediately
            ...                       # out["parent"] is the live goal-tree node
    ann.tree.to_dict()                # current goal/subgoal tree

The prompt is built from ``self.history`` (events already seen) only, so the
model cannot see the future: the same input format as training records
(trainrec.compact), which keeps training and serving consistent.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from contracts import evidence_id
from trainrec.core import ANN_FIELDS, CONTEXT_EVENTS, compact

INSTRUCTIONS = (
    "You annotate one terminal event for a learner. Using only the events shown, reply with JSON: "
    '{"goal": str, "tool": str|null, "result": "success"|"failure"|"partial"|"unknown", '
    '"mistake": bool, "summary": str, "new_goal": bool}. new_goal=true when the event starts a new top-level task.'
)
RESULTS = {"success", "failure", "partial", "unknown"}


def build_prompt(history: Sequence[dict], labels: dict[str, dict], target: dict, n_context: int = CONTEXT_EVENTS) -> str:
    lines = [INSTRUCTIONS, "", "Earlier events:"]
    for e in list(history)[-n_context:]:
        c = compact(e)
        if e["id"] in labels:
            c["label"] = labels[e["id"]]
        lines.append(json.dumps(c, ensure_ascii=False))
    lines += ["", "Event to annotate:", json.dumps(compact(target), ensure_ascii=False)]
    return "\n".join(lines)


def parse_labels(reply: str) -> dict[str, Any]:
    """Tolerant: extracts the first JSON object; bad or missing fields become unknown."""
    start, end = reply.find("{"), reply.rfind("}")
    try:
        obj = json.loads(reply[start : end + 1]) if start != -1 and end > start else {}
    except json.JSONDecodeError:
        obj = {}
    if not isinstance(obj, dict):
        obj = {}
    out = {
        "goal": obj.get("goal") if isinstance(obj.get("goal"), str) else None,
        "tool": obj.get("tool") if isinstance(obj.get("tool"), str) else None,
        "result": obj.get("result") if obj.get("result") in RESULTS else "unknown",
        "mistake": obj.get("mistake") if isinstance(obj.get("mistake"), bool) else None,
        "summary": obj.get("summary") if isinstance(obj.get("summary"), str) else None,
        "new_goal": obj.get("new_goal") is True,
    }
    return out


@dataclass
class Node:
    id: str  # annotation id
    target: str
    goal: str | None
    children: list["Node"] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"id": self.id, "target": self.target, "goal": self.goal, "children": [c.to_dict() for c in self.children]}


class GoalTree:
    """Roots are top-level goals; every other annotation hangs under the current root.

    A new root starts when the model says new_goal, when there is no root
    yet, or when the goal matches an earlier root (returning to an old task)."""

    def __init__(self) -> None:
        self.roots: list[Node] = []
        self.current: Node | None = None
        self.nodes: dict[str, Node] = {}

    def add(self, ann_id: str, target: str, goal: str | None, new_goal: bool) -> str | None:
        node = Node(ann_id, target, goal)
        self.nodes[ann_id] = node
        earlier = next((r for r in self.roots if goal and r.goal == goal), None)
        if earlier is not None and earlier is not self.current:
            self.current = earlier
            earlier.children.append(node)
            return earlier.id
        if self.current is None or new_goal:
            self.roots.append(node)
            self.current = node
            return None
        self.current.children.append(node)
        return self.current.id

    def to_dict(self) -> list[dict]:
        return [r.to_dict() for r in self.roots]


class StreamingAnnotator:
    def __init__(self, model, header: dict, n_context: int = CONTEXT_EVENTS, model_name: str = "streaming") -> None:
        if "version" not in (header.get("redaction") or {}):
            raise ValueError("refusing to annotate an unscrubbed session")
        self.model = model
        self.sid = header["id"]
        self.n_context = n_context
        self.model_name = model_name
        self.history: list[dict] = []
        self.labels: dict[str, dict] = {}
        self.tree = GoalTree()
        self._n = 0
        self.failures = 0
        self._held: dict | None = None

    def feed(self, ev: dict) -> list[dict]:
        """Take the next closed event; return annotation events ready now.

        A command is held until the next event: if that is a file_edit made by
        the command (same moment, from the same precmd), the edit is annotated
        instead, since it says more than ``vim app.py``. Call ``flush()`` at
        the end of each precmd (or at the end of a session)."""
        if ev.get("type") not in ("command", "file_edit"):
            return []
        out: list[dict] = []
        if self._held is not None:
            if ev["type"] == "file_edit" and ev.get("command_id") == self._held["id"]:
                self.history.append(self._held)  # context, not annotated
                self._held = None
                return self._annotate(ev)
            out += self.flush()
        if ev["type"] == "command":
            self._held = ev
            return out
        return out + self._annotate(ev)

    def flush(self) -> list[dict]:
        held, self._held = self._held, None
        return self._annotate(held) if held is not None else []

    def _annotate(self, ev: dict) -> list[dict]:
        prompt = build_prompt(self.history, self.labels, ev, self.n_context)
        try:
            reply = self.model.complete("annotate", [evidence_id(self.sid, ev["id"])], prompt)
        except KeyError:  # no reply (replay miss): skip, keep streaming
            self.failures += 1
            self.history.append(ev)
            return []
        labels = parse_labels(reply)
        self._n += 1
        ann_id = f"sa{self._n}"
        parent = self.tree.add(ann_id, ev["id"], labels["goal"], labels["new_goal"])
        self.labels[ev["id"]] = {k: labels[k] for k in ANN_FIELDS}
        self.history.append(ev)
        return [{"type": "annotation", "id": ann_id, "target": ev["id"],
                 **{k: labels[k] for k in ANN_FIELDS}, "parent": parent, "model": self.model_name}]


def annotate_session(model, header: dict, events: Iterable[dict], **kw) -> tuple[list[dict], StreamingAnnotator]:
    """Batch helper: stream a finished session through the annotator."""
    sa = StreamingAnnotator(model, header, **kw)
    out = []
    for ev in sorted((e for e in events if e.get("type") in ("command", "file_edit", "resize")), key=lambda e: e["t"]):
        out += sa.feed(ev)
    out += sa.flush()
    return out, sa
