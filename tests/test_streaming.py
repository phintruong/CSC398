"""Streaming annotation + runtime interface (docs/design/streaming-annotation.md section 3)."""

from __future__ import annotations

import json

import pytest

from contracts import validate_session
from fixtures import ReplayModel, load_sessions
from runtime import Guarded, Logged, Model, UnscrubbedPrompt
from streaming import GoalTree, StreamingAnnotator, annotate_session, parse_labels

from planted_set import build_cases

SESSIONS = load_sessions()


def _texts(ev):
    return [v for k, v in ev.items() if k in ("command", "output", "diff") and isinstance(v, str) and len(v) > 12]


class Spy:
    """Fails if a prompt contains text that only appears in events after the target."""

    def __init__(self, events):
        self.steps = sorted((e for e in events if e["type"] in ("command", "file_edit")), key=lambda e: e["t"])
        self.calls = 0

    def complete(self, task, evidence_ids, prompt):
        self.calls += 1
        target = evidence_ids[0].split("#")[1]
        idx = next(i for i, e in enumerate(self.steps) if e["id"] == target)
        seen = json.dumps(self.steps[: idx + 1])
        for later in self.steps[idx + 1 :]:
            for text in _texts(later):
                if text not in seen:
                    assert json.dumps(text)[1:-1] not in prompt, f"future text leaked into prompt for {target}"
        return json.dumps({"goal": "g", "result": "success", "summary": "s"})


@pytest.mark.parametrize("sid", sorted(SESSIONS))
def test_s1_no_future_access(sid):
    h, evs = SESSIONS[sid]
    spy = Spy(evs)
    annotate_session(spy, h, evs)
    assert spy.calls > 0


def _tree_by_target(anns):
    by_id = {a["id"]: a for a in anns}
    return {a["target"]: by_id[a["parent"]]["target"] if a["parent"] else None for a in anns}


@pytest.mark.parametrize("sid", sorted(SESSIONS))
def test_s2_matches_fixture_labels_and_tree(sid):
    h, evs = SESSIONS[sid]
    out, sa = annotate_session(ReplayModel(), h, evs)
    fixture = [e for e in evs if e["type"] == "annotation"]
    assert {a["target"]: a["goal"] for a in out} == {a["target"]: a["goal"] for a in fixture}
    assert {a["target"]: a["mistake"] for a in out} == {a["target"]: a["mistake"] for a in fixture}
    assert _tree_by_target(out) == _tree_by_target(fixture)
    assert len(sa.tree.roots) == 1


def test_s3_edits_annotated_instead_of_editor_command():
    h, evs = SESSIONS["mock-docker-build"]
    sa = StreamingAnnotator(ReplayModel(), h)
    cmd = next(e for e in evs if e["type"] == "command" and e["command"].startswith("vim"))
    edit = next(e for e in evs if e["type"] == "file_edit")
    assert sa.feed(cmd) == []  # held
    (ann,) = sa.feed(edit)
    assert ann["target"] == edit["id"]
    ls = next(e for e in evs if e["type"] == "command" and e["command"] == "ls")
    assert sa.feed(ls) == [] and [a["target"] for a in sa.flush()] == [ls["id"]]


def test_s4_goal_tree_rules():
    t = GoalTree()
    assert t.add("a1", "c1", "deploy", False) is None
    assert t.add("a2", "c2", "build", False) == "a1"
    assert t.add("a3", "c3", "fix dns", True) is None  # new_goal: new root
    assert t.add("a4", "c4", "x", False) == "a3"
    assert t.add("a5", "c5", "deploy", False) == "a1"  # back to the first task
    assert t.add("a6", "c6", "y", False) == "a1"
    assert [r["id"] for r in t.to_dict()] == ["a1", "a3"]


def test_s5_bad_replies_and_misses():
    assert parse_labels("not json at all")["result"] == "unknown"
    got = parse_labels('Sure! {"goal": "g", "result": "great", "mistake": "yes", "summary": 3} hope that helps')
    assert got == {"goal": "g", "tool": None, "result": "unknown", "mistake": None, "summary": None, "new_goal": False}
    h, evs = SESSIONS["mock-python-venv"]

    class Flaky:
        n = 0

        def complete(self, task, ids, prompt):
            self.n += 1
            if self.n % 2:
                raise KeyError("miss")
            return "garbage"

    out, sa = annotate_session(Flaky(), h, evs)
    assert sa.failures > 0 and out and all(a["result"] == "unknown" for a in out)


def test_s6_guard_and_log():
    secret = next(p.value for c in build_cases() for p in c.planted if p.kind == "TOKEN")
    log = []
    m = Logged(Guarded(ReplayModel()), log)
    with pytest.raises(UnscrubbedPrompt) as e:
        m.complete("annotate", ["x#c1"], f"export GITHUB_TOKEN={secret}")
    assert secret not in str(e.value) and "TOKEN" in str(e.value)
    h, evs = SESSIONS["mock-docker-build"]
    out, _ = annotate_session(m, h, evs)
    assert out
    blob = json.dumps(log)
    assert "docker build" not in blob and "Build fails" not in blob  # no prompt or reply text
    assert {"task", "evidence", "prompt_chars", "reply_chars", "ok", "ms"} == set(log[-1])
    assert isinstance(ReplayModel(), Model)


def test_s7_output_is_schema_valid():
    h, evs = SESSIONS["mock-nginx-port"]
    base = [e for e in evs if e["type"] not in ("annotation", "correction")]
    out, _ = annotate_session(ReplayModel(), h, base)
    assert validate_session(h, base + out) == []


def test_refuses_unscrubbed():
    h, _ = SESSIONS["mock-nginx-port"]
    with pytest.raises(ValueError):
        StreamingAnnotator(ReplayModel(), {**h, "redaction": {}})


def test_guard_reads_json_escapes():
    """Escaped newlines aren't secrets; secrets inside escaped JSON still are."""
    secret = next(p.value for c in build_cases() for p in c.planted if p.kind == "TOKEN")
    m = Guarded(ReplayModel())
    false_positive = json.dumps({"output": "Enter password: \nWelcome to the MySQL monitor."})
    with pytest.raises(KeyError):  # passes the guard, then misses the replay (no recorded reply): fine
        m.complete("annotate", ["x#c1"], false_positive)
    hidden = json.dumps({"output": json.dumps({"access_token": secret})})  # double-escaped
    with pytest.raises(UnscrubbedPrompt):
        m.complete("annotate", ["x#c1"], hidden)
