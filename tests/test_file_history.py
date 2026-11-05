"""File history across sessions (KAN-31)."""

from __future__ import annotations

from contracts import parse_evidence_id
from fileedit.history import edited_files, file_history, normalize_path
from fixtures import load_sessions

S = load_sessions()


def test_normalize_path():
    assert normalize_path("/home/<USER_3>//app/.env/") == "/home/<USER>/app/.env"
    assert normalize_path("~/app/.env") == "/home/<USER>/app/.env"
    assert normalize_path(r"C:\Users\<USER_1>\x") == "C:/Users/<USER>/x"


def test_history_across_sessions_oldest_first_with_why():
    h = file_history(S, "/etc/nginx/sites-available/default")
    assert [x["evidence"] for x in h] == ["mock-nginx-port#f5", "mock-nginx-port#f10", "mock-nginx-port-rerun#f5"]
    assert h[0]["mistake"] is True and "semicolon" in h[0]["why"]  # corrected annotation text
    assert h[1]["mistake"] is False and h[0]["is_config"]
    assert all(x["diff"].startswith("--- a/") for x in h)
    for x in h:
        sid, eid = parse_evidence_id(x["evidence"])
        assert eid in {e["id"] for e in S[sid][1]}


def test_relative_query_and_limit():
    assert [x["evidence"] for x in file_history(S, "Dockerfile")] == ["mock-docker-build#f7"]
    assert [x["evidence"] for x in file_history(S, "src/config.js")] == ["mock-git-conflict#f7"]
    assert len(file_history(S, "sites-available/default", limit=1)) == 1
    assert file_history(S, "/nope") == []


def test_placeholder_numbering_doesnt_split_history():
    h, evs = S["mock-docker-build"]
    renumbered = [dict(e, path=e["path"].replace("<USER_1>", "<USER_7>")) if e["type"] == "file_edit" else e for e in evs]
    sessions = {"a": (h, evs), "b": ({**h, "id": "b"}, renumbered)}
    edit = next(e for e in evs if e["type"] == "file_edit")
    assert len(file_history(sessions, edit["path"])) == 2


def test_edited_files_index():
    idx = edited_files(S)
    top = idx[0]
    assert top["path"] == "/etc/nginx/sites-available/default" and top["edits"] == 3 and len(top["sessions"]) == 2
