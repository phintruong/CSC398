"""Contract tests (KAN-108): every component reads/writes event schema v1,
even from broken recordings. Plus unit tests of the validator itself (KAN-102).

When a new producer or consumer lands (tagging, search, post-mortems...),
add it to PRODUCERS / the consumer test below.
"""

from __future__ import annotations

import copy
import json
import random

import pytest

from capture import Session, import_cast
from contracts import (
    dumps_session,
    evidence_id,
    loads_session,
    parse_evidence_id,
    validate_event,
    validate_session,
    validate_session_header,
)
from fixtures import load_sessions
from planted_set import build_cases

HEADER = {"type": "session", "schema": "events-v1", "id": "s1", "origin": "mock",
          "provenance": {"source": "t"}, "redaction": {"version": "x", "replaced": {}}}
CMD = {"type": "command", "id": "c1", "t": 1.0, "t_end": 2.0, "duration": 1.0, "command": "ls", "cwd": "/",
       "exit_code": 0, "output": ""}
EDIT = {"type": "file_edit", "id": "f1", "t": 1.0, "t_end": 2.0, "path": "/a", "tool": "vim", "command": "vim /a",
        "change": "modified", "is_config": False, "diff": "--- a/a\n+++ b/a\n", "binary": False, "truncated": False,
        "redaction": "scrub-0.2.0"}
ANN = {"type": "annotation", "id": "a1", "target": "c1", "goal": "g", "result": "success"}


# -- the validator --------------------------------------------------------------


def test_good_session_validates():
    assert validate_session(HEADER, [CMD, EDIT, ANN]) == []


@pytest.mark.parametrize("field", [k for k in CMD if k != "type"])
def test_missing_required_command_field(field):
    ev = {k: v for k, v in CMD.items() if k != field}
    assert validate_event(ev)


@pytest.mark.parametrize("ev,needle", [
    ({**CMD, "exit_code": True}, "bool"),
    ({**CMD, "t": "1"}, "type str"),
    ({**CMD, "t": -1}, "negative"),
    ({**CMD, "t_end": 0.5}, "before"),
    ({**EDIT, "change": "renamed"}, "change"),
    ({**EDIT, "diff": None}, "neither binary"),
    ({**ANN, "result": "maybe"}, "result"),
    ({"type": "resize", "id": "r", "t": 1, "cols": 0, "rows": 5}, "positive"),
    ({**CMD, "term": {"cols": 80}}, "rows"),
    ({"type": "nope", "id": "x"}, "unknown type"),
    ([1, "o", "x"], "not an object"),
])
def test_bad_events(ev, needle):
    errs = validate_event(ev)
    assert errs and any(needle in e for e in errs), errs


def test_extra_fields_are_allowed():
    assert validate_event({**CMD, "x_custom": [1, 2]}) == []


def test_header_rules():
    assert validate_session_header(HEADER) == []
    assert validate_session_header({**HEADER, "schema": "events-v0"})
    assert validate_session_header({**HEADER, "origin": "elsewhere"})
    assert any("not scrubbed" in e for e in validate_session_header({**HEADER, "redaction": {}}))


def test_duplicate_ids_and_dangling_references():
    errs = validate_session(HEADER, [CMD, CMD, {**ANN, "target": "c99"}, {**EDIT, "command_id": "c42"}])
    assert any("duplicate" in e for e in errs)
    assert any("'c99' does not exist" in e for e in errs)
    assert any("'c42' does not exist" in e for e in errs)


def test_round_trip_and_tolerant_loading():
    text = dumps_session(HEADER, [CMD, EDIT])
    assert loads_session(text) == (HEADER, [CMD, EDIT])
    broken = text + "{oops\n[1,2]\n"
    with pytest.raises(ValueError):
        loads_session(broken)
    h, evs = loads_session(broken, strict=False)
    assert h["_skipped_lines"] == 2 and len(evs) == 2
    with pytest.raises(ValueError):
        loads_session("")


def test_evidence_ids():
    eid = evidence_id("live-abc", "c3")
    assert eid == "live-abc#c3" and parse_evidence_id(eid) == ("live-abc", "c3")
    with pytest.raises(ValueError):
        parse_evidence_id("no-hash")


# -- producers: everything that writes events must write valid schema v1 ---------


def _broken_variants(cast_text: str, seed: int) -> list[str]:
    """Realistic damage: truncated file, garbage lines, missing fields, bad times."""
    rng = random.Random(seed)
    lines = cast_text.splitlines()
    out = [
        "\n".join(lines[: max(2, len(lines) // 2)]) + '\n[3.5, "o", "half a li',  # recorder killed mid-write
        "\n".join(lines[:1] + ["not json", "[]", '["x", "o", "y"]', "{}"] + lines[1:]),
        "\n".join(lines[:1] + [json.dumps([rng.random() * 100, "o", "jump"])] + lines[1:]),  # time out of order
        "\n".join(lines[:1] + [json.dumps([1.0, "r", "garbage-size"]), json.dumps([1.0, "r", "0x0"])] + lines[1:]),
        lines[0] + "\n",  # header only
    ]
    return out


CASTS = [(c.name, c.cast) for c in build_cases()]


@pytest.mark.parametrize("name,text", CASTS, ids=[n for n, _ in CASTS])
def test_importer_output_is_valid_even_when_broken(name, text):
    for i, variant in enumerate([text] + _broken_variants(text, hash(name) & 0xFFFF)):
        h, evs, _, _ = import_cast(variant, source="t", consent="synthetic", license="none")
        errs = validate_session(h, evs)
        assert errs == [], f"variant {i}: {errs[:3]}"


def test_live_session_output_is_valid(tmp_path):
    s = Session.start(tmp_path / "s", source="t", consent="c", license="l", cols=80, rows=24, now=0.0)
    (tmp_path / "x.txt").write_text("a\n")
    for i, (cmd, code) in enumerate([("vim x.txt", 0), ("make", 2), ("", 0)]):
        Session.load(s.dir).preexec(cmd, str(tmp_path), t=i + 0.1)
        (tmp_path / "x.txt").write_text(f"v{i}\n")
        Session.load(s.dir).precmd(code, t=i + 0.5, cols=80 + i, rows=24)
    out = Session.load(s.dir).stop(t=9)
    assert validate_session(*loads_session(out.read_text(encoding="utf-8"))) == []


def test_fixtures_are_valid():
    sessions = load_sessions()
    assert len(sessions) >= 6
    for sid, (h, evs) in sessions.items():
        assert validate_session(h, evs) == [], sid


# -- consumers: a reader must survive what producers emit ---------------------------


def test_reader_survives_corrupted_session_file():
    text = dumps_session(HEADER, [CMD, EDIT, ANN])
    lines = text.splitlines()
    corrupted = "\n".join(lines[:2] + ["\x00\x00garbage", lines[2][:20]] + lines[3:])
    h, evs = loads_session(corrupted, strict=False)
    assert h["_skipped_lines"] == 2
    assert [e["id"] for e in evs] == ["c1", "a1"]


def test_validator_never_crashes_on_mutations():
    rng = random.Random(7)
    values = [None, 0, -1, 1.5, True, "", "x", [], {}, {"cols": "a"}]
    for _ in range(500):
        ev = copy.deepcopy(rng.choice([CMD, EDIT, ANN]))
        for _ in range(rng.randint(1, 3)):
            k = rng.choice(list(ev))
            if rng.random() < 0.3:
                ev.pop(k)
            else:
                ev[k] = rng.choice(values)
        assert isinstance(validate_event(ev), list)
