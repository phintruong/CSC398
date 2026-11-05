"""Embed event summaries, align repeated runs, merge into a canonical procedure (KAN-46).

The default embedder is a dependency-free hashed bag of words + character
trigrams. Anything with ``embed(texts) -> list[list[float]]`` (e.g. a
sentence-transformer) can replace it; the alignment doesn't change.
"""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from typing import Protocol, Sequence

from postmortem.core import Step, _read_only, build_steps

DIM = 512
GAP = -0.4  # alignment gap penalty (similarities are in [0, 1])
MATCH_MIN = 0.35  # aligned pairs below this are treated as different steps


class Embedder(Protocol):
    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


_TOKEN = re.compile(r"[a-z0-9_]+")
_PLACEHOLDER = re.compile(r"<([A-Z_]+)_\d+>")


def normalize_command(cmd: str) -> str:
    """Make runs comparable: drop sudo, placeholders lose their number, numbers become N."""
    cmd = _PLACEHOLDER.sub(lambda m: f"<{m.group(1)}>", cmd)
    cmd = re.sub(r"^(sudo|doas)\s+", "", cmd.strip())
    return re.sub(r"\b\d+\b", "N", cmd)


class HashEmbedder:
    def __init__(self, dim: int = DIM) -> None:
        self.dim = dim

    def _features(self, text: str) -> list[str]:
        t = text.lower()
        words = _TOKEN.findall(t)
        grams = [t[i : i + 3] for i in range(max(0, len(t) - 2))]
        return [f"w:{w}" for w in words] + [f"g:{g}" for g in grams]

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        out = []
        for text in texts:
            v = [0.0] * self.dim
            for f in self._features(text):
                h = int.from_bytes(hashlib.md5(f.encode()).digest()[:4], "little")
                v[h % self.dim] += (1.0 if f.startswith("w:") else 0.3) * (1 if h & 0x80000000 else -1)
            n = math.sqrt(sum(x * x for x in v)) or 1.0
            out.append([x / n for x in v])
        return out


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    return max(0.0, sum(x * y for x, y in zip(a, b)))


ACTION_WEIGHT = 0.6  # what was done (command / file) counts more than how it was described


def step_action(s: Step) -> str:
    return f"edit {s.ev['path']}" if s.kind == "file_edit" else normalize_command(s.ev["command"])


def step_text(s: Step) -> str:
    ann = s.ann or {}
    return " | ".join(x for x in (ann.get("goal"), ann.get("summary")) if x) or step_action(s)


@dataclass
class Run:
    session: str
    steps: list[Step]
    vectors: list[list[float]]


def make_run(header: dict, events: Sequence[dict], embedder: Embedder | None = None) -> Run:
    steps = [s for s in build_steps(header["id"], events) if not _is_noise(s)]
    emb = embedder or HashEmbedder()
    actions = emb.embed([step_action(s) for s in steps])
    texts = emb.embed([step_text(s) for s in steps])
    wa, wt = math.sqrt(ACTION_WEIGHT), math.sqrt(1 - ACTION_WEIGHT)
    # concatenating weighted unit vectors makes cosine = 0.6*action_sim + 0.4*text_sim
    vecs = [[wa * x for x in a] + [wt * y for y in t] for a, t in zip(actions, texts)]
    return Run(header["id"], steps, vecs)


def _is_noise(s: Step) -> bool:
    return s.kind == "command" and s.ev["command"].strip().split(" ")[0] in ("cd", "clear", "exit", "history")


def align(a: Run, b: Run, gap: float = GAP) -> list[tuple[int | None, int | None, float]]:
    """Needleman-Wunsch global alignment. Returns (i, j, similarity); None = gap."""
    n, m = len(a.steps), len(b.steps)
    sim = [[cosine(a.vectors[i], b.vectors[j]) for j in range(m)] for i in range(n)]
    score = [[0.0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        score[i][0] = i * gap
    for j in range(1, m + 1):
        score[0][j] = j * gap
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            score[i][j] = max(score[i - 1][j - 1] + sim[i - 1][j - 1] - 0.3, score[i - 1][j] + gap, score[i][j - 1] + gap)
    out = []
    i, j = n, m
    while i > 0 or j > 0:
        if i > 0 and j > 0 and abs(score[i][j] - (score[i - 1][j - 1] + sim[i - 1][j - 1] - 0.3)) < 1e-9:
            s = sim[i - 1][j - 1]
            if s >= MATCH_MIN:
                out.append((i - 1, j - 1, round(s, 4)))
            else:  # a forced mismatch: report as two gaps
                out += [(i - 1, None, 0.0), (None, j - 1, 0.0)]
            i, j = i - 1, j - 1
        elif i > 0 and abs(score[i][j] - (score[i - 1][j] + gap)) < 1e-9:
            out.append((i - 1, None, 0.0))
            i -= 1
        else:
            out.append((None, j - 1, 0.0))
            j -= 1
    return out[::-1]


def same_task_score(a: Run, b: Run) -> float:
    """How alike two runs are: matched similarity over the longer run's length."""
    if not a.steps or not b.steps:
        return 0.0
    pairs = align(a, b)
    matched = sum(s for i, j, s in pairs if i is not None and j is not None)
    return round(matched / max(len(a.steps), len(b.steps)), 4)


def _good(s: Step) -> bool:
    return not s.failed and not _read_only(s)


def canonical_procedure(runs: Sequence[Run], min_support: float = 0.5) -> list[dict]:
    """Merge runs of the same task into one clean procedure.

    The reference is the run with the fewest failures. Every step that is
    good (no failure/mistake, not just looking around) and appears in at
    least ``min_support`` of the runs is kept, in reference order, with
    evidence from every run where it appears. Steps that only some runs
    needed are kept with their support so a reader can see they're optional.
    """
    if not runs:
        return []
    ref = min(runs, key=lambda r: (sum(s.failed for s in r.steps), len(r.steps)))
    slots: list[dict] = [{"step": s, "evidence": [s.eid], "runs": {ref.session}} for s in ref.steps]
    for run in runs:
        if run is ref:
            continue
        # failed steps never join a procedure: drop them so the fix aligns, not the mistake
        keep = [k for k, s in enumerate(run.steps) if _good(s)]
        run = Run(run.session, [run.steps[k] for k in keep], [run.vectors[k] for k in keep])
        for i, j, _ in align(ref, run):
            if i is not None and j is not None:
                slots[i]["evidence"].append(run.steps[j].eid)
                slots[i]["runs"].add(run.session)
    out = []
    for n, slot in enumerate(slots):
        s: Step = slot["step"]
        support = len(slot["runs"]) / len(runs)
        if not _good(s) or support < min_support:
            continue
        item = {"n": len(out) + 1, "action": s.label(), "support": round(support, 2), "evidence": slot["evidence"]}
        if s.kind == "command":
            item["command"] = s.ev["command"]
        else:
            item["path"], item["diff"] = s.ev["path"], s.ev.get("diff")
        if s.ann and s.ann.get("summary"):
            item["why"] = s.ann["summary"]
        out.append(item)
    return out
