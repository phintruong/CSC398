"""Sessions home (KAN-111) and Privacy & cloud settings (KAN-115)."""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from dashboard import settings
from dashboard.pages import render_home, render_privacy, session_summary, settings_from_form
from dashboard.server import make_handler

REPO = Path(__file__).resolve().parent.parent
FIX = REPO / "fixtures/data"


def test_settings_defaults_are_safe(tmp_path):
    s = settings.load(tmp_path / "missing.json")
    assert s == {"extra_terms": [], "cloud": {"allowed": False, "tasks": [], "require_approval": True}}
    assert not settings.cloud_allowed("annotate", s)


def test_settings_normalize_garbage(tmp_path):
    p = tmp_path / "s.json"
    p.write_text('{"extra_terms": ["Acme", "", 3, "Acme", " Initech "], "cloud": {"allowed": "yes", "tasks": ["annotate", "rm -rf"]}, "x": 1}')
    s = settings.load(p)
    assert s["extra_terms"] == ["Acme", "Initech"]
    assert s["cloud"] == {"allowed": False, "tasks": ["annotate"], "require_approval": True}
    p.write_text("not json")
    assert settings.load(p)["cloud"]["allowed"] is False


def test_cloud_allowed_needs_switch_and_task(tmp_path):
    s = settings.normalize({"cloud": {"allowed": True, "tasks": ["summarize"]}})
    assert settings.cloud_allowed("summarize", s) and not settings.cloud_allowed("annotate", s)


def test_home_lists_fixtures_with_status():
    html = render_home(FIX)
    for sid in ("mock-nginx-port", "mock-docker-build", "mock-resize-htop"):
        assert sid in html
    assert "1 incident" in html and "1 reviewed" in html and "scrubbed synthetic" in html


def test_home_flags_unscrubbed_and_broken(tmp_path):
    (tmp_path / "raw.events.jsonl").write_text(
        '{"type":"session","schema":"events-v1","id":"<b>raw</b>","origin":"live","provenance":{},"redaction":{}}\n')
    (tmp_path / "junk.events.jsonl").write_text("not json\n")
    html = render_home(tmp_path)
    assert "not scrubbed" in html and "unreadable" in html
    assert "<b>raw</b>" not in html and "&lt;b&gt;raw&lt;/b&gt;" in html  # escaped


def test_home_empty_state(tmp_path):
    assert "No sessions" in render_home(tmp_path)


def test_session_summary_counts():
    s = session_summary(FIX / "mock-nginx-port.events.jsonl")
    assert (s["commands"], s["edits"], s["corrections"], s["incidents"], s["unresolved"]) == (8, 2, 1, 1, 0)


def test_privacy_form_round_trip():
    form = {"extra_terms": ["Acme Corp\nInitech\n\n"], "cloud_allowed": ["on"], "task": ["answer", "bogus"]}
    s = settings.normalize(settings_from_form(form))
    assert s == {"extra_terms": ["Acme Corp", "Initech"], "cloud": {"allowed": True, "tasks": ["answer"], "require_approval": False}}
    html = render_privacy(s, Path("x.json"))
    assert "cloud allowed for: answer" in html and "Acme Corp" in html


@pytest.fixture
def server(tmp_path):
    spath = tmp_path / "privacy.json"
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(FIX, spath))
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", spath
    srv.shutdown()


def _req(url, data=None, headers=None):
    req = urllib.request.Request(url, data=data, headers=headers or {})
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        r = opener.open(req)
        return r.status, r.read().decode(), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(), dict(e.headers)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def test_server_pages_and_save(server):
    base, spath = server
    code, html, headers = _req(base + "/")
    assert code == 200 and "mock-nginx-port" in html and "default-src 'none'" in headers["Content-Security-Policy"]
    assert _req(base + "/session/mock-nginx-port.events.jsonl")[0] == 200
    code, _, headers = _req(base + "/privacy", b"extra_terms=Acme&cloud_allowed=on&task=annotate",
                            {"Content-Type": "application/x-www-form-urlencoded"})
    assert code == 303 and headers["Location"] == "/privacy?saved=1"
    saved = json.loads(spath.read_text())
    assert saved["extra_terms"] == ["Acme"] and saved["cloud"]["tasks"] == ["annotate"]
    assert "Saved." in _req(base + "/privacy?saved=1")[1]


def test_server_rejects_cross_site_post_and_traversal(server):
    base, spath = server
    code, _, _ = _req(base + "/privacy", b"cloud_allowed=on&task=answer", {"Origin": "https://evil.example"})
    assert code == 403 and not spath.exists()
    for bad in ("/session/..%2F..%2Fpyproject.toml", "/session/../README.md", "/session/nope.events.jsonl", "/nope"):
        assert _req(base + bad)[0] == 404


def test_capture_uses_settings_terms(tmp_path, monkeypatch):
    import subprocess
    import sys

    sfile = tmp_path / "settings.json"
    settings.save({"extra_terms": ["Initech"]}, sfile)
    cast = tmp_path / "in.cast"
    cast.write_text('{"version": 2, "width": 80, "height": 24}\n[0.1, "o", "$ "]\n[0.2, "o", "ls /srv/initech\\r\\n"]\n'
                    '[0.3, "o", "x\\r\\n"]\n[0.4, "o", "$ "]\n')
    env = {**__import__("os").environ, "PYTHONPATH": str(REPO), "CSC398_SETTINGS": str(sfile)}
    out = tmp_path / "o.events.jsonl"
    r = subprocess.run([sys.executable, "-m", "capture", "import", str(cast), "-o", str(out), "--source", "t",
                        "--consent", "synthetic", "--license", "none"], capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    text = out.read_text()
    assert "initech" not in text.lower() and "<NAME_1>" in text
