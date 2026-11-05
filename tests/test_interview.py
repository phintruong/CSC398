"""The "why?" interviewer on fixtures (KAN-58)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from contracts import loads_session, validate_session
from fixtures import load_sessions
from interview import answer, questions
from postmortem import check_wording, postmortems, render_markdown
from planted_set import build_cases

S = load_sessions()
FIX = Path(__file__).resolve().parent.parent / "fixtures/data"


def test_questions_target_mistakes_and_discoveries():
    h, evs = S["mock-docker-build"]
    qs = questions(h, evs)
    assert [(q["kind"], q["target"]) for q in qs] == [("mistake", "c2"), ("discovery", "c4"), ("discovery", "f7")]
    assert all(q["question"].endswith("?") for q in qs)
    assert qs[1]["question"] == "After `docker build -t api:dev .` failed, you ran `ls`. What were you looking for?"


def test_nothing_to_ask_without_a_mistake():
    h, evs = S["mock-resize-htop"]
    assert questions(h, evs) == []


def test_max_questions():
    h, evs = S["mock-git-conflict"]
    assert len(questions(h, evs, max_questions=1)) == 1


@pytest.fixture
def nginx(tmp_path):
    p = tmp_path / "s.events.jsonl"
    shutil.copy(FIX / "mock-nginx-port.events.jsonl", p)
    return p


def test_answer_is_scrubbed_stored_and_not_asked_again(nginx):
    h, evs = loads_session(nginx.read_text(encoding="utf-8"))
    q = questions(h, evs)[0]
    secret = next(p.value for c in build_cases() for p in c.planted if p.kind == "TOKEN")
    ev = answer(nginx, q["target"], q["question"],
                f"I pasted the config from a ticket with token {secret} and lost the semicolon.", "phuc",
                now="2026-11-05T21:00:00+00:00")
    text = nginx.read_text(encoding="utf-8")
    assert secret not in text and "<TOKEN_1>" in ev["answer"]
    h, evs = loads_session(text)
    assert validate_session(h, evs) == []
    assert q["target"] not in {x["target"] for x in questions(h, evs)}  # already explained


def test_placeholder_numbering_continues_after_the_recording():
    from scrubber import PlaceholderMap, Scrubber

    s = Scrubber(mapping=PlaceholderMap.continuing({"HOST": 2}))
    assert "<HOST_3>" in s.scrub_text("ssh deploy@db-9.corp.internal")


def test_postmortem_quotes_the_answer_with_citation(nginx):
    h, evs = loads_session(nginx.read_text(encoding="utf-8"))
    q = questions(h, evs)[0]
    answer(nginx, q["target"], q["question"], "I retyped the line by hand and forgot the semicolon.", "phuc")
    h, evs = loads_session(nginx.read_text(encoding="utf-8"))
    (r,) = postmortems(h, evs)
    assert r["explanations"][0]["text"].startswith("I retyped")
    md = render_markdown(r)
    assert "In the author's words" in md and "[mock-nginx-port#e1]" in md
    assert check_wording(md, r) is None  # the quote is cited, so the report still passes the citation guard


@pytest.mark.parametrize("kw,msg", [
    ({"target": "c999"}, "not a command"),
    ({"author": "Jane Public"}, "handle"),
    ({"text": "   "}, "empty"),
    ({"source": "telepathy"}, "typed or voice"),
])
def test_bad_answers_rejected(nginx, kw, msg):
    args = {"target": "f5", "question": "Why?", "text": "Because.", "author": "phuc", **kw}
    before = nginx.read_text(encoding="utf-8")
    with pytest.raises(ValueError, match=msg):
        answer(nginx, **args)
    assert nginx.read_text(encoding="utf-8") == before


def test_model_rephrasing_is_guarded():
    h, evs = S["mock-docker-build"]

    class Nice:
        def complete(self, task, ids, prompt):
            return "What did you expect the docker build to do, and what surprised you?"

    class Rambling:
        def complete(self, task, ids, prompt):
            return "Great job! Also, why did you use docker? And what about kubernetes?"

    assert questions(h, evs, model=Nice())[0]["question"].startswith("What did you expect")
    base = questions(h, evs)[0]["question"]
    assert questions(h, evs, model=Rambling())[0]["question"] == base
