"""Acceptance test for capture-time scrubbing (KAN-33 / KAN-34, epic KAN-4).

Pass criteria (see docs/design/scrubbing.md, section 5):
  A1  no planted value appears anywhere in the scrubbed recording
      (events, header, markers), in raw or ANSI-stripped form;
  A2  no 8-character fragment of a planted secret survives (no partial leaks);
  A3  every occurrence of a planted value becomes the same placeholder,
      and the placeholder kind is the expected one;
  A4  every "keep" string is still present (we don't over-scrub);
  A5  the metadata written into the file (redaction counts) contains no originals;
  A6  event count and timestamps are unchanged.
"""

from __future__ import annotations

import json

import pytest

from scrubber import read_cast, scrub_recording
from scrubber.detect import SECRET_KINDS, strip_controls

from planted_set import build_cases

CASES = build_cases()


def _streams(rec) -> dict[str, str]:
    """Joined output / input streams plus header and markers, raw and clean."""
    joined = {
        code: "".join(ev[2] for ev in rec.events if ev[1] == code and isinstance(ev[2], str))
        for code in ("o", "i", "m")
    }
    joined["header"] = json.dumps(rec.header, ensure_ascii=False)
    for k in list(joined):
        joined[k + ":clean"] = strip_controls(joined[k])[0]
    return joined


@pytest.fixture(scope="module", params=CASES, ids=[c.name for c in CASES])
def scrubbed(request):
    case = request.param
    original = read_cast(case.cast)
    rec, scrubber = scrub_recording(original, extra_terms=case.extra_terms, source="planted-set",
                                    consent="synthetic", license="CC0-1.0")
    return case, original, rec, scrubber


def test_a1_planted_values_absent(scrubbed):
    case, _, rec, _ = scrubbed
    streams = _streams(rec)
    leaks = [(p.value, where) for p in case.planted for where, text in streams.items() if p.value in text]
    assert not leaks, f"planted values leaked: {leaks}"


def test_a2_no_partial_leaks(scrubbed):
    case, _, rec, _ = scrubbed
    text = "\n".join(_streams(rec).values())
    for p in case.planted:
        if p.kind not in SECRET_KINDS:
            continue
        frags = {p.value[i : i + 8] for i in range(len(p.value) - 7)}
        leaked = sorted(f for f in frags if f in text)
        assert not leaked, f"{p.kind} fragment(s) leaked: {leaked[:3]}"


def _standalone_count(text: str, value: str, others: list[str]) -> int:
    """Occurrences of value that are not part of a longer planted value
    (e.g. 'db-prod-01' inside 'db-prod-01.corp.internal')."""
    covered = set()
    for o in others:
        if len(o) > len(value) and value in o:
            i = text.find(o)
            while i != -1:
                covered.update(range(i, i + len(o)))
                i = text.find(o, i + 1)
    n, i = 0, text.find(value)
    while i != -1:
        n += i not in covered
        i = text.find(value, i + 1)
    return n


def test_a3_consistent_placeholders(scrubbed):
    case, original, rec, scrubber = scrubbed
    before, after = _streams(original), _streams(rec)
    values = [p.value for p in case.planted]
    for p in case.planted:
        if p.kind == "PRIVATE_KEY":
            # the whole key body is one value; check every line maps into it
            phs = {ph for v, ph in scrubber.map._by_value.items() if p.value in v}
            assert len(phs) == 1 and next(iter(phs)).startswith("<PRIVATE_KEY_")
            continue
        ph = scrubber.map.lookup(p.value)
        assert ph is not None, f"{p.kind} {p.value[:4]}... was never replaced"
        assert ph.startswith(f"<{p.kind}_"), f"expected {p.kind}, got {ph}"
        if p.kind == "NAME":
            continue  # matched case-insensitively, counts can differ
        for code in ("o:clean", "i:clean"):
            n_before = _standalone_count(before[code], p.value, values)
            assert after[code].count(ph) >= n_before, f"{ph} in {code}: {after[code].count(ph)} < {n_before}"


def test_a4_keep_list_survives(scrubbed):
    case, _, rec, _ = scrubbed
    text = _streams(rec)["o:clean"]
    missing = [k for k in case.keep if k not in text]
    assert not missing, f"over-scrubbed, missing: {missing}"


def test_a5_metadata_has_no_originals(scrubbed):
    case, _, rec, _ = scrubbed
    meta = json.dumps({"redaction": rec.header["redaction"], "provenance": rec.header.get("provenance")})
    assert not [p.value for p in case.planted if p.value in meta]
    assert rec.header["redaction"]["version"]
    assert rec.header["provenance"] == {"source": "planted-set", "consent": "synthetic", "license": "CC0-1.0"}


def test_a6_timing_preserved(scrubbed):
    _, original, rec, _ = scrubbed
    assert [(e[0], e[1]) for e in rec.events] == [(e[0], e[1]) for e in original.events]


def test_hard_negatives_untouched():
    case = next(c for c in CASES if c.name == "hard_negatives")
    rec, scrubber = scrub_recording(read_cast(case.cast))
    assert scrubber.map.counts() == {}, f"false positives: {scrubber.map.counts()}"
    assert [e[2] for e in rec.events] == [e[2] for e in read_cast(case.cast).events]


def test_recall_report(capsys):
    """Not a gate on its own: prints recall over the whole set, for the M1 write-up."""
    planted = found = 0
    for case in CASES:
        rec, _ = scrub_recording(read_cast(case.cast), extra_terms=case.extra_terms)
        text = "\n".join(_streams(rec).values())
        planted += len(case.planted)
        found += sum(p.value not in text for p in case.planted)
    with capsys.disabled():
        print(f"\nplanted-secret recall: {found}/{planted} = {found / planted:.1%} over {len(CASES)} recordings")
    assert found == planted
