"""Benchmark metrics for the pipeline (KAN-62).

* Model 0 (segmentation): event-boundary precision/recall/F1 with a time tolerance.
* Model 1 (tagging): per-field accuracy, macro F1 for ``result``, binary F1 for ``mistake``.
* Goal tree: parent-attachment accuracy.
* Descriptions: a deterministic rubric (see ``rubric``), optionally an LLM judge.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Callable, Sequence

from postmortem.core import apply_corrections

TOLERANCE = 1.0  # seconds


def prf(tp: int, fp: int, fn: int) -> dict[str, float]:
    p = tp / (tp + fp) if tp + fp else 1.0 if not fn else 0.0
    r = tp / (tp + fn) if tp + fn else 1.0
    f = 2 * p * r / (p + r) if p + r else 0.0
    return {"precision": round(p, 4), "recall": round(r, 4), "f1": round(f, 4), "tp": tp, "fp": fp, "fn": fn}


# -- Model 0 -------------------------------------------------------------------


def boundaries(events: Sequence[dict]) -> list[float]:
    return sorted(float(e["t"]) for e in events if e.get("type") == "command")


def segmentation(gold: Sequence[dict], pred: Sequence[dict], tol: float = TOLERANCE) -> dict:
    """Greedy one-to-one matching of event start times within ``tol`` seconds."""
    g, p = boundaries(gold), boundaries(pred)
    used = [False] * len(g)
    tp = 0
    for t in p:
        best, best_d = None, tol + 1e-9
        for i, gt in enumerate(g):
            d = abs(gt - t)
            if not used[i] and d <= best_d:
                best, best_d = i, d
        if best is not None:
            used[best] = True
            tp += 1
    return prf(tp, len(p) - tp, len(g) - tp)


# -- Model 1 -------------------------------------------------------------------


def _anns(events: Sequence[dict]) -> dict[str, dict]:
    return {e["target"]: e for e in apply_corrections(events) if e.get("type") == "annotation"}


def _norm(v):
    return v.strip().lower() if isinstance(v, str) else v


def tagging(gold: Sequence[dict], pred: Sequence[dict]) -> dict:
    """Compare annotations on the same target event. Gold labels have corrections applied."""
    g, p = _anns(gold), _anns(pred)
    targets = sorted(g)
    covered = [t for t in targets if t in p]
    out: dict = {"gold": len(targets), "predicted": len(p), "coverage": round(len(covered) / len(targets), 4) if targets else 1.0}
    for f in ("tool", "result"):
        hits = sum(_norm(g[t].get(f)) == _norm(p[t].get(f)) for t in covered)
        out[f"{f}_accuracy"] = round(hits / len(targets), 4) if targets else 1.0
    # macro F1 over result classes
    labels = sorted({g[t].get("result") for t in targets} | {p[t].get("result") for t in covered} - {None})
    f1s = []
    for lab in labels:
        tp = sum(g[t].get("result") == lab and p.get(t, {}).get("result") == lab for t in targets)
        fp = sum(g[t].get("result") != lab and p.get(t, {}).get("result") == lab for t in targets)
        fn = sum(g[t].get("result") == lab and p.get(t, {}).get("result") != lab for t in targets)
        f1s.append(prf(tp, fp, fn)["f1"])
    out["result_macro_f1"] = round(sum(f1s) / len(f1s), 4) if f1s else 1.0
    tp = sum(bool(g[t].get("mistake")) and bool(p.get(t, {}).get("mistake")) for t in targets)
    fp = sum(not g[t].get("mistake") and bool(p.get(t, {}).get("mistake")) for t in targets) + sum(
        bool(p[t].get("mistake")) for t in p if t not in g)
    fn = sum(bool(g[t].get("mistake")) and not p.get(t, {}).get("mistake") for t in targets)
    out["mistake"] = prf(tp, fp, fn)
    out["goal_similarity"] = round(sum(_jaccard(g[t].get("goal"), p[t].get("goal")) for t in covered) / len(targets), 4) if targets else 1.0
    out["tree_attachment"] = tree_attachment(g, p)
    return out


def _jaccard(a: str | None, b: str | None) -> float:
    wa, wb = set(_words(a or "")), set(_words(b or ""))
    if not wa and not wb:
        return 1.0
    return len(wa & wb) / len(wa | wb)


def _words(s: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", s.lower())


def tree_attachment(g: dict[str, dict], p: dict[str, dict]) -> float:
    """Share of gold events whose predicted parent points at the same event as gold's parent."""
    g_by_id = {a["id"]: a for a in g.values()}
    p_by_id = {a["id"]: a for a in p.values()}

    def parent_target(a, by_id):
        par = a.get("parent")
        return by_id[par]["target"] if par in by_id else None

    if not g:
        return 1.0
    hits = sum(t in p and parent_target(g[t], g_by_id) == parent_target(p[t], p_by_id) for t in g)
    return round(hits / len(g), 4)


# -- description rubric ----------------------------------------------------------

RUBRIC = {
    "faithful": "Every specific token in the summary (file names, numbers, flags, commands) appears in the event.",
    "specific": "Names the tool, file or concrete change instead of a generic phrase.",
    "concise": "Between 3 and 30 words.",
    "explains_outcome": "For failures and mistakes, says what went wrong.",
}
_GENERIC = re.compile(r"(?i)^(ran a command|did something|executed|performed an action|ok|done|success)\.?$")
_ERRORISH = re.compile(
    r"(?i)\b(fail|error|missing|wrong|typo|refus|conflict|denied|not found|invalid|broke|forgot|delet|drop|"
    r"instead|without|but)\w*"
)
_SPECIFIC_TOKEN = re.compile(r"[\w.-]*[./_\d][\w./-]*")


def rubric(summary: str | None, event: dict, ann: dict) -> dict[str, bool]:
    s = (summary or "").strip()
    words = s.split()
    evidence = " ".join(str(event.get(k) or "") for k in ("command", "output", "diff", "path", "exit_code")).lower()
    # "specific" tokens: file names, flags, versions, numbers (after trimming sentence punctuation)
    tokens = [t.strip(".,;:()`'\"%").lower() for t in _SPECIFIC_TOKEN.findall(s)]
    tokens = [t for t in tokens if re.search(r"[./_\d]", t) and len(t) >= 2]
    faithful = all(t in evidence for t in tokens)
    tool = (ann.get("tool") or "").lower()
    specific = bool(s) and not _GENERIC.match(s) and (bool(tokens) or (tool and tool in s.lower()) or len(words) >= 5)
    failing = ann.get("mistake") or ann.get("result") == "failure"
    return {
        "faithful": bool(s) and faithful,
        "specific": bool(specific),
        "concise": 3 <= len(words) <= 30,
        "explains_outcome": (not failing) or bool(_ERRORISH.search(s)),
    }


def descriptions(gold_events: Sequence[dict], pred: Sequence[dict],
                 judge: Callable[[str, dict], dict[str, bool]] | None = None) -> dict:
    """Score predicted summaries with the rubric (and an optional judge that returns the same keys)."""
    by_id = {e["id"]: e for e in gold_events if e.get("type") in ("command", "file_edit")}
    scores: Counter = Counter()
    n = 0
    for a in _anns(pred).values():
        ev = by_id.get(a["target"])
        if ev is None:
            continue
        n += 1
        r = rubric(a.get("summary"), ev, a)
        if judge is not None:
            j = judge(a.get("summary") or "", ev)
            r = {k: r[k] and j.get(k, True) for k in r}
        scores.update(k for k, v in r.items() if v)
    out = {k: round(scores[k] / n, 4) if n else 0.0 for k in RUBRIC}
    out["overall"] = round(sum(out[k] for k in RUBRIC) / len(RUBRIC), 4)
    out["n"] = n
    return out
