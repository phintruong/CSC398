"""Package training records for the data flywheel (KAN-83 -> 2.1.27).

    python -m trainrec dataset SESSIONS_DIR -o out_dir [--dev 0.1 --test 0.1 --salt v1]

Writes, per split (train/dev/test):
  sft.<split>.jsonl   {"messages": [system, user, assistant], "meta": {...}}      (chat SFT)
  dpo.<split>.jsonl   {"prompt": [system, user], "chosen": [assistant], "rejected": [assistant], "meta": {...}}
and manifest.json (counts, which sessions went where, redaction versions, policy skips).

Splits are by **session** (hash of salt + session id), so no session's events
appear in two splits and adding new sessions never moves old ones. Records
are deduplicated by id. The user prompt is the same text the streaming
annotator sends at serving time (streaming.build_prompt), so training and
serving match.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Iterable

from contracts import loads_session

from .core import allowed, export_preferences, export_sft

SYSTEM = "You annotate terminal events to help a learner understand a recorded work session."


def split_of(session_id: str, dev: float, test: float, salt: str) -> str:
    h = int(hashlib.sha256(f"{salt}:{session_id}".encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
    return "test" if h < test else "dev" if h < test + dev else "train"


def task_groups(sessions, threshold: float | None = None) -> dict[str, str]:
    """session id -> group key (smallest session id in its same-task cluster)."""
    from procedures import make_run, same_task_score
    from procedures.__main__ import SAME_TASK_THRESHOLD

    thr = SAME_TASK_THRESHOLD if threshold is None else threshold
    runs = [make_run(h, evs) for h, evs in sessions]
    parent = {r.session: r.session for r in runs}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i, a in enumerate(runs):
        for b in runs[i + 1 :]:
            if same_task_score(a, b) >= thr:
                ra, rb = find(a.session), find(b.session)
                parent[max(ra, rb)] = min(ra, rb)
    return {r.session: find(r.session) for r in runs}


def _user_prompt(record: dict) -> str:
    from streaming.annotator import INSTRUCTIONS

    inp = record["input"]
    lines = [INSTRUCTIONS, "", "Earlier events:"]
    lines += [json.dumps(c, ensure_ascii=False) for c in inp["context"]]
    lines += ["", "Event to annotate:", json.dumps(inp["target"], ensure_ascii=False)]
    return "\n".join(lines)


def _answer(labels: dict) -> str:
    return json.dumps(labels, ensure_ascii=False)


def _meta(record: dict) -> dict:
    p = record["provenance"]
    return {"id": record["id"], "session": p["session"], "evidence": p["evidence"], "redaction": p["redaction"],
            "license": p["license"], "label_source": p["label_source"]}


def to_sft_chat(record: dict) -> dict:
    return {"messages": [{"role": "system", "content": SYSTEM},
                         {"role": "user", "content": _user_prompt(record)},
                         {"role": "assistant", "content": _answer(record["output"])}],
            "meta": _meta(record)}


def to_dpo(record: dict) -> dict:
    return {"prompt": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": _user_prompt(record)}],
            "chosen": [{"role": "assistant", "content": _answer(record["chosen"])}],
            "rejected": [{"role": "assistant", "content": _answer(record["rejected"])}],
            "meta": _meta(record)}


def build(session_files: Iterable[Path], out_dir: Path, dev: float = 0.1, test: float = 0.1, salt: str = "v1") -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    rows: dict[tuple[str, str], list[str]] = {}
    seen: set[str] = set()
    manifest = {"salt": salt, "dev": dev, "test": test, "splits": {}, "skipped": [], "redaction_versions": [],
                "duplicates_dropped": 0}
    sessions = []
    for path in sorted(session_files):
        h, evs = loads_session(Path(path).read_text(encoding="utf-8"), strict=False)
        ok, why = allowed(h)
        if not ok:
            manifest["skipped"].append({"session": h.get("id", Path(path).name), "reason": why})
            continue
        sessions.append((h, evs))
    group = task_groups(sessions)
    manifest["task_groups"] = sorted({tuple(sorted(s for s, g in group.items() if g == key)) for key in group.values()
                                      if sum(1 for g in group.values() if g == key) > 1})
    for h, evs in sessions:
        # repeated runs of one task share a split, so test never holds a near-copy of a training session
        split = split_of(group[h["id"]], dev, test, salt)
        manifest["splits"].setdefault(split, {"sessions": [], "sft": 0, "dpo": 0})["sessions"].append(h["id"])
        v = h["redaction"]["version"]
        if v not in manifest["redaction_versions"]:
            manifest["redaction_versions"].append(v)
        for kind, records, conv in (("sft", export_sft(h, evs), to_sft_chat), ("dpo", export_preferences(h, evs), to_dpo)):
            for r in records:
                if r["id"] in seen:
                    manifest["duplicates_dropped"] += 1
                    continue
                seen.add(r["id"])
                rows.setdefault((kind, split), []).append(json.dumps(conv(r), ensure_ascii=False))
                manifest["splits"][split][kind] += 1
    for split in ("train", "dev", "test"):
        for kind in ("sft", "dpo"):
            lines = rows.get((kind, split), [])
            (out_dir / f"{kind}.{split}.jsonl").write_text("".join(l + "\n" for l in lines), encoding="utf-8", newline="\n")
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest
