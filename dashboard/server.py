"""python -m dashboard [--sessions DIR] [--port 8398]

Local web dashboard: Sessions home (KAN-111) and Privacy & cloud settings
(KAN-115). Binds to 127.0.0.1 only. Standard library, no build step.
"""

from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from . import settings as settings_mod
from .pages import page, render_home, render_privacy, render_session, settings_from_form

MAX_FORM = 64 * 1024


def make_handler(sessions_dir: Path, settings_path: Path):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, html: str, headers: dict | None = None) -> None:
            data = html.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'")
            self.send_header("X-Content-Type-Options", "nosniff")
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:  # noqa: N802
            u = urlparse(self.path)
            q = parse_qs(u.query)
            if u.path == "/":
                return self._send(200, render_home(sessions_dir, (q.get("sort") or ["date"])[0]))
            if u.path == "/privacy":
                return self._send(200, render_privacy(settings_mod.load(settings_path), settings_path, "saved" in q))
            if u.path.startswith("/session/"):
                name = unquote(u.path[len("/session/") :])
                f = (sessions_dir / name).resolve()
                if f.parent != sessions_dir.resolve() or not f.name.endswith(".events.jsonl") or not f.exists():
                    return self._send(404, page("Not found", "home", "<h1>Not found</h1>"))
                return self._send(200, render_session(f))
            self._send(404, page("Not found", "home", "<h1>Not found</h1>"))

        def do_POST(self) -> None:  # noqa: N802
            if urlparse(self.path).path != "/privacy":
                return self._send(404, page("Not found", "home", "<h1>Not found</h1>"))
            origin = self.headers.get("Origin")
            host = self.headers.get("Host", "")
            if origin and urlparse(origin).netloc != host:  # another site posting to localhost
                return self._send(403, page("Forbidden", "privacy", "<h1>Forbidden</h1>"))
            n = int(self.headers.get("Content-Length") or 0)
            if n > MAX_FORM:
                return self._send(413, page("Too large", "privacy", "<h1>Form too large</h1>"))
            form = parse_qs(self.rfile.read(n).decode("utf-8", "replace"), keep_blank_values=True)
            settings_mod.save(settings_from_form(form), settings_path)
            self._send(303, "", {"Location": "/privacy?saved=1"})

        def log_message(self, fmt: str, *args) -> None:  # keep request paths out of logs
            pass

    return Handler


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m dashboard")
    p.add_argument("--sessions", type=Path, default=Path("fixtures/data"))
    p.add_argument("--settings", type=Path, default=None)
    p.add_argument("--port", type=int, default=8398)
    a = p.parse_args(argv)
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), make_handler(a.sessions, a.settings or settings_mod.path()))
    print(f"http://127.0.0.1:{a.port}/  (sessions: {a.sessions})")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0
