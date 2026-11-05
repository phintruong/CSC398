"""Replay recorded model replies so tests never call a real model (KAN-106)."""

from __future__ import annotations

import json
from pathlib import Path

from .build import request_key

DATA = Path(__file__).resolve().parent / "data"


class ReplayModel:
    """``complete(task, evidence_ids)`` returns the recorded reply or raises KeyError.

    Keyed by (task, sorted evidence ids), so prompt wording changes don't
    break tests; what the model was asked *about* is what matters."""

    def __init__(self, path: str | Path = DATA / "model_replies.jsonl") -> None:
        self.replies: dict[str, dict] = {}
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                self.replies[r["key"]] = r
        self.calls: list[tuple[str, list[str]]] = []

    def complete(self, task: str, evidence_ids: list[str], prompt: str | None = None) -> str:
        self.calls.append((task, list(evidence_ids)))
        key = request_key(task, list(evidence_ids))
        if key not in self.replies:
            raise KeyError(f"no recorded reply for task={task!r} evidence={sorted(evidence_ids)}")
        return self.replies[key]["reply"]


def load_sessions(directory: str | Path = DATA) -> dict[str, tuple[dict, list[dict]]]:
    from contracts import loads_session

    out = {}
    for p in sorted(Path(directory).glob("*.events.jsonl")):
        h, evs = loads_session(p.read_text(encoding="utf-8"))
        out[h["id"]] = (h, evs)
    return out
