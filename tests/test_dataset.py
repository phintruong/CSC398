"""Flywheel dataset packaging (KAN-83)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from fixtures import load_sessions
from streaming import StreamingAnnotator
from streaming.annotator import build_prompt
from trainrec.core import export_sft
from trainrec.dataset import _user_prompt, build, split_of, task_groups

FIX = Path(__file__).resolve().parent.parent / "fixtures/data"
S = load_sessions()


def _read(p: Path) -> list[dict]:
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def test_split_is_stable_and_roughly_proportional():
    assert split_of("abc", 0.1, 0.1, "v1") == split_of("abc", 0.1, 0.1, "v1")
    splits = [split_of(f"s{i}", 0.1, 0.1, "v1") for i in range(2000)]
    assert 0.07 < splits.count("test") / 2000 < 0.13 and 0.07 < splits.count("dev") / 2000 < 0.13


def test_same_task_sessions_share_a_group():
    g = task_groups(list(S.values()))
    assert g["mock-nginx-port"] == g["mock-nginx-port-rerun"]
    assert len({g[k] for k in S}) == len(S) - 1


def test_build_outputs_and_no_leakage(tmp_path):
    m = build(FIX.glob("*.events.jsonl"), tmp_path, dev=0.3, test=0.3)
    where = {}
    for split, info in m["splits"].items():
        for sid in info["sessions"]:
            assert sid not in where, f"{sid} in two splits"
            where[sid] = split
    assert where["mock-nginx-port"] == where["mock-nginx-port-rerun"]
    total_sft = sum(len(_read(tmp_path / f"sft.{s}.jsonl")) for s in ("train", "dev", "test"))
    assert total_sft == sum(info["sft"] for info in m["splits"].values()) > 20
    for s in ("train", "dev", "test"):
        for row in _read(tmp_path / f"sft.{s}.jsonl"):
            assert [x["role"] for x in row["messages"]] == ["system", "user", "assistant"]
            json.loads(row["messages"][2]["content"])  # assistant answer is the JSON the annotator parses
            assert where[row["meta"]["session"]] == s
        for row in _read(tmp_path / f"dpo.{s}.jsonl"):
            assert row["chosen"] != row["rejected"] and row["prompt"][-1]["role"] == "user"


def test_adding_sessions_never_moves_old_ones(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    few = tmp_path / "few"
    few.mkdir()
    for name in ("mock-docker-build", "mock-git-conflict"):
        shutil.copy(FIX / f"{name}.events.jsonl", few)
    m1 = build(few.glob("*.events.jsonl"), a, 0.3, 0.3)
    m2 = build(FIX.glob("*.events.jsonl"), b, 0.3, 0.3)
    loc = lambda m: {s: k for k, v in m["splits"].items() for s in v["sessions"]}  # noqa: E731
    assert all(loc(m2)[s] == split for s, split in loc(m1).items())


def test_duplicates_dropped(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    shutil.copy(FIX / "mock-docker-build.events.jsonl", src / "a.events.jsonl")
    shutil.copy(FIX / "mock-docker-build.events.jsonl", src / "b.events.jsonl")
    m = build(src.glob("*.events.jsonl"), tmp_path / "out")
    assert m["duplicates_dropped"] > 0


def test_train_serve_prompt_parity():
    """The training prompt for an event is exactly what the streaming annotator sends live."""
    h, evs = S["mock-docker-build"]
    base = [e for e in evs if e["type"] not in ("annotation", "correction")]
    sent = {}

    class Capture:
        def complete(self, task, ids, prompt):
            sent[ids[0].split("#")[1]] = prompt
            target = ids[0].split("#")[1]
            ann = next(e for e in evs if e.get("target") == target) if any(e.get("target") == target for e in evs) else None
            if ann is None:
                raise KeyError(target)
            return json.dumps({k: ann.get(k) for k in ("goal", "tool", "result", "mistake", "summary")})

    sa = StreamingAnnotator(Capture(), h)
    for e in base:
        sa.feed(e)
    sa.flush()
    records = {r["input"]["target"]["id"]: r for r in export_sft(h, evs)}
    checked = 0
    for target, prompt in sent.items():
        if target in records:
            assert _user_prompt(records[target]) == prompt, target
            checked += 1
    assert checked >= 4
    assert build_prompt  # imported for readers: this is the serving-side builder
