"""Training records: one format for SFT and preference data (KAN-103, KAN-81, KAN-82).

Design: docs/design/training-records.md.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable, Sequence

from contracts import evidence_id
from postmortem.core import apply_corrections

RECORD_SCHEMA = "trainrec-v1"
TASK = "annotate"
ANN_FIELDS = ("goal", "tool", "result", "mistake", "summary")
CONTEXT_EVENTS = 8
OUTPUT_CHARS = 1000
DIFF_CHARS = 2000

# Policy: which sessions may become training data. Override per run.
ALLOWED_CONSENT = frozenset({"recorded-by-owner", "consented", "synthetic"})
BLOCKED_LICENSES = frozenset({"", "none", "all-rights-reserved", "proprietary", "unknown"})


class NotScrubbed(ValueError):
    pass


def allowed(header: dict, consent: Iterable[str] = ALLOWED_CONSENT, blocked: Iterable[str] = BLOCKED_LICENSES) -> tuple[bool, str]:
    if "version" not in (header.get("redaction") or {}):
        return False, "not scrubbed"
    prov = header.get("provenance") or {}
    if prov.get("consent") not in set(consent):
        return False, f"consent {prov.get('consent')!r} not allowed"
    lic = (prov.get("license") or "").strip().lower()
    if lic in {b.lower() for b in blocked}:
        return False, f"license {prov.get('license')!r} not allowed"
    return True, "ok"


def _clip(s: str | None, n: int) -> str | None:
    if s is None or len(s) <= n:
        return s
    return s[: n // 4] + f"\n[... {len(s) - n} characters trimmed ...]\n" + s[-(n - n // 4) :]


def compact(ev: dict) -> dict:
    """What a model sees of one event."""
    if ev["type"] == "command":
        return {"id": ev["id"], "type": "command", "command": ev["command"], "cwd": ev.get("cwd"),
                "exit_code": ev.get("exit_code"), "output": _clip(ev.get("output"), OUTPUT_CHARS)}
    if ev["type"] == "file_edit":
        return {"id": ev["id"], "type": "file_edit", "path": ev["path"], "tool": ev["tool"], "change": ev["change"],
                "is_config": ev["is_config"], "diff": _clip(ev.get("diff"), DIFF_CHARS)}
    raise ValueError(ev["type"])


def _labels(ann: dict) -> dict:
    return {k: ann.get(k) for k in ANN_FIELDS}


def _record_id(*parts: Any) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]


def _prepare(header: dict, events: Sequence[dict]):
    if "version" not in (header.get("redaction") or {}):
        raise NotScrubbed(f"session {header.get('id')!r} has no redaction block; refusing to export")
    corrected = apply_corrections(events)
    steps = [e for e in corrected if e.get("type") in ("command", "file_edit")]
    order = {e["id"]: i for i, e in enumerate(steps)}
    anns = [e for e in corrected if e.get("type") == "annotation" and e.get("target") in order]
    ann_by_target = {a["target"]: a for a in anns}
    by_id = {e["id"]: e for e in steps}
    return corrected, steps, order, anns, ann_by_target, by_id


def _input(header: dict, steps, order, ann_by_target, target: dict, n_context: int) -> dict:
    i = order[target["id"]]
    past = steps[max(0, i - n_context) : i]  # strictly before the target: no future leakage
    context = []
    for e in past:
        c = compact(e)
        a = ann_by_target.get(e["id"])
        if a is not None:
            c["label"] = _labels(a)  # earlier (corrected) labels: goal context
        context.append(c)
    return {"session_term": header.get("term"), "context": context, "target": compact(target)}


def _provenance(header: dict, ann: dict, evidence: list[str], authors: list[str]) -> dict:
    prov = header.get("provenance") or {}
    return {"session": header["id"], "evidence": evidence, "annotation": ann["id"],
            "redaction": header["redaction"]["version"], "consent": prov.get("consent"),
            "license": prov.get("license"), "source": prov.get("source"),
            "label_source": "human-corrected" if ann.get("corrected_fields") else (ann.get("model") or "unknown"),
            "correction_authors": authors}


def export_sft(header: dict, events: Sequence[dict], n_context: int = CONTEXT_EVENTS) -> list[dict]:
    _, steps, order, anns, ann_by_target, by_id = _prepare(header, events)
    authors = _authors(events)
    out = []
    for a in anns:
        target = by_id[a["target"]]
        inp = _input(header, steps, order, ann_by_target, target, n_context)
        ev_ids = [evidence_id(header["id"], c["id"]) for c in inp["context"]] + [evidence_id(header["id"], target["id"])]
        out.append({
            "schema": RECORD_SCHEMA, "kind": "sft", "task": TASK,
            "id": _record_id("sft", header["id"], a["id"], _labels(a)),
            "input": inp, "output": _labels(a),
            "provenance": _provenance(header, a, ev_ids, authors.get(a["id"], [])),
        })
    return out


def _authors(events: Sequence[dict]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for e in events:
        if e.get("type") == "correction":
            out.setdefault(e["target"], [])
            if e.get("author") not in out[e["target"]]:
                out[e["target"]].append(e.get("author"))
    return out


def export_preferences(header: dict, events: Sequence[dict], n_context: int = CONTEXT_EVENTS) -> list[dict]:
    """chosen = the human-corrected labels, rejected = what the model said. Only where a correction exists."""
    _, steps, order, anns, ann_by_target, by_id = _prepare(header, events)
    originals = {e["id"]: e for e in events if e.get("type") == "annotation"}
    authors = _authors(events)
    out = []
    for a in anns:
        if not a.get("corrected_fields"):
            continue
        chosen, rejected = _labels(a), _labels(originals[a["id"]])
        if chosen == rejected:
            continue  # a correction that set the same value: no preference signal
        target = by_id[a["target"]]
        inp = _input(header, steps, order, ann_by_target, target, n_context)
        ev_ids = [evidence_id(header["id"], c["id"]) for c in inp["context"]] + [evidence_id(header["id"], target["id"])]
        out.append({
            "schema": RECORD_SCHEMA, "kind": "preference", "task": TASK,
            "id": _record_id("pref", header["id"], a["id"], chosen, rejected),
            "input": inp, "chosen": chosen, "rejected": rejected,
            "provenance": _provenance(header, a, ev_ids, authors.get(a["id"], [])),
        })
    return out


def validate_record(r: Any) -> list[str]:
    errs = []
    if not isinstance(r, dict):
        return ["record: not an object"]
    for k in ("schema", "kind", "task", "id", "input", "provenance"):
        if k not in r:
            errs.append(f"missing {k!r}")
    if errs:
        return errs
    if r["schema"] != RECORD_SCHEMA:
        errs.append(f"schema {r['schema']!r}")
    if r["kind"] == "sft":
        if not isinstance(r.get("output"), dict):
            errs.append("sft record needs output")
    elif r["kind"] == "preference":
        if not isinstance(r.get("chosen"), dict) or not isinstance(r.get("rejected"), dict):
            errs.append("preference record needs chosen and rejected")
        elif r["chosen"] == r["rejected"]:
            errs.append("chosen == rejected")
    else:
        errs.append(f"kind {r['kind']!r}")
    inp = r["input"]
    if not isinstance(inp, dict) or "target" not in inp or not isinstance(inp.get("context"), list):
        errs.append("input needs target and context")
    p = r["provenance"]
    for k in ("session", "evidence", "redaction", "consent", "license"):
        if k not in p:
            errs.append(f"provenance missing {k!r}")
    return errs
