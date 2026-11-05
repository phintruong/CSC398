"""Integration test (KAN-109): a golden recording through every stage, against committed expected outputs.

    .cast -> import + scrub -> streaming annotation (rule model) -> post-mortem
          -> canonical procedure -> automation script -> training dataset -> benchmark

Regenerate after an intended change, then review the diff:
    GOLDEN_UPDATE=1 pytest tests/test_golden.py
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from capture import import_cast
from contracts import dumps_session, validate_session
from generate import to_script
from postmortem import postmortems, render_markdown
from procedures import canonical_procedure, make_run
from runtime import Guarded, Logged
from runtime.rules import RuleAnnotator
from streaming import annotate_session
from trainrec import export_preferences, export_sft, validate_record

HERE = Path(__file__).resolve().parent / "golden"
EXPECTED = HERE / "expected"
UPDATE = os.environ.get("GOLDEN_UPDATE") == "1"
PLANTED = ["dana", "web-01.corp.internal", "Zq8v!R2mLx7Tn"]  # values in the golden cast that must never come out


def run_pipeline() -> dict[str, str]:
    cast = (HERE / "nginx-fix.cast").read_text(encoding="utf-8")
    header, events, clean_cast, _ = import_cast(cast, source="golden", consent="synthetic", license="CC0-1.0")
    log: list = []
    anns, _ = annotate_session(Logged(Guarded(RuleAnnotator()), log), header, events)
    session = events + anns
    assert validate_session(header, session) == []
    reports = postmortems(header, session)
    proc = canonical_procedure([make_run(header, session)])
    records = export_sft(header, session) + export_preferences(header, session)
    assert all(validate_record(r) == [] for r in records)
    return {
        "session.events.jsonl": dumps_session(header, session),
        "clean.cast": clean_cast,
        "postmortem.md": "\n".join(render_markdown(r) for r in reports),
        "procedure.json": json.dumps(proc, indent=2, ensure_ascii=False) + "\n",
        "script.sh": to_script(proc, "Golden: nginx port fix"),
        "records.jsonl": "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in records),
        "model_log.json": json.dumps([{k: v for k, v in x.items() if k != "ms"} for x in log], indent=1) + "\n",
    }


OUTPUTS = run_pipeline()


@pytest.mark.parametrize("name", sorted(OUTPUTS))
def test_stage_matches_golden(name):
    path = EXPECTED / name
    if UPDATE:
        EXPECTED.mkdir(parents=True, exist_ok=True)
        path.write_bytes(OUTPUTS[name].encode("utf-8"))
    assert path.exists(), f"missing golden file {name}: run GOLDEN_UPDATE=1 pytest tests/test_golden.py"
    assert OUTPUTS[name] == path.read_bytes().decode("utf-8"), f"{name} changed; if intended, regenerate and review the diff"


def test_no_planted_value_in_any_stage():
    for name, text in OUTPUTS.items():
        for v in PLANTED:
            assert v not in text, f"planted value leaked into {name}"


def test_pipeline_finds_the_incident_and_the_fix():
    pm = OUTPUTS["postmortem.md"]
    assert "**Status:** resolved" in pm and "[emerg]" in pm and "sed -i" in pm
    script = OUTPUTS["script.sh"]
    assert "sed -i" in script and "systemctl reload nginx" in script
    assert script.count('"sudo nginx -t"') == 1  # only the passing test, not the failed one
