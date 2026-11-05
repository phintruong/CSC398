"""Run the benchmark over a gold directory and a prediction directory (KAN-62).

Gold and predictions are schema-v1 session files with the same session ids.
Predictions may contain only commands (Model 0 output), only annotations
(Model 1 output on gold events), or both.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from contracts import loads_session

from .metrics import descriptions, segmentation, tagging

# Pass rules: placeholders until the team freezes them on the dev set (KAN-64).
DEFAULT_RULES = {
    "segmentation.f1": 0.80,
    "tagging.result_macro_f1": 0.60,
    "tagging.mistake.f1": 0.50,
    "tagging.tree_attachment": 0.60,
    "descriptions.overall": 0.70,
}


def load_dir(d: Path) -> dict[str, tuple[dict, list[dict]]]:
    out = {}
    for p in sorted(Path(d).glob("*.events.jsonl")):
        h, evs = loads_session(p.read_text(encoding="utf-8"), strict=False)
        out[h["id"]] = (h, evs)
    return out


def _mean(dicts: list[dict]) -> dict:
    """Average numeric leaves (micro-averaging counts would hide small sessions)."""
    if not dicts:
        return {}
    out: dict = {}
    for k, v in dicts[0].items():
        if isinstance(v, dict):
            out[k] = _mean([d[k] for d in dicts])
        elif isinstance(v, (int, float)) and not isinstance(v, bool):
            out[k] = round(sum(d[k] for d in dicts) / len(dicts), 4)
    return out


def _get(report: dict, dotted: str):
    cur = report
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def run(gold_dir: Path, pred_dir: Path, rules: dict | None = None, judge: Callable | None = None) -> dict:
    gold, pred = load_dir(gold_dir), load_dir(pred_dir)
    missing = sorted(set(gold) - set(pred))
    seg, tag, desc, per_session = [], [], [], {}
    for sid, (_, gevs) in gold.items():
        if sid not in pred:
            continue
        _, pevs = pred[sid]
        row = {}
        if any(e.get("type") == "command" for e in pevs):
            row["segmentation"] = segmentation(gevs, pevs)
            seg.append(row["segmentation"])
        if any(e.get("type") == "annotation" for e in pevs):
            row["tagging"] = tagging(gevs, pevs)
            row["descriptions"] = descriptions(gevs, pevs, judge)
            tag.append(row["tagging"])
            desc.append(row["descriptions"])
        per_session[sid] = row
    report = {"sessions": len(per_session), "missing_predictions": missing}
    if seg:
        report["segmentation"] = _mean(seg)
    if tag:
        report["tagging"] = _mean(tag)
        report["descriptions"] = _mean(desc)
    checks = []
    for key, threshold in (rules if rules is not None else DEFAULT_RULES).items():
        value = _get(report, key)
        if value is None:
            continue  # metric not applicable to this prediction set
        checks.append({"metric": key, "value": value, "threshold": threshold, "pass": value >= threshold})
    report["checks"] = checks
    report["pass"] = all(c["pass"] for c in checks) and not missing
    report["per_session"] = per_session
    return report


def write_report(report: dict, path: Path) -> None:
    path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
