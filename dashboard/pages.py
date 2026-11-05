"""HTML for the Sessions home (KAN-111) and Privacy & cloud settings (KAN-115) screens.

Pure functions: data in, HTML out (everything escaped), so they're easy to test.
"""

from __future__ import annotations

import datetime as dt
from html import escape
from pathlib import Path
from urllib.parse import quote

from contracts import loads_session, validate_session

from .settings import TASKS

CSS = """
:root{--bg:#fafaf9;--fg:#1c1917;--muted:#78716c;--line:#e7e5e4;--card:#fff;--ok:#15803d;--warn:#b45309;--bad:#b91c1c;--accent:#1d4ed8}
@media (prefers-color-scheme:dark){:root{--bg:#1c1917;--fg:#f5f5f4;--muted:#a8a29e;--line:#44403c;--card:#292524;--ok:#4ade80;--warn:#fbbf24;--bad:#f87171;--accent:#93c5fd}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,sans-serif}
header{display:flex;gap:24px;align-items:baseline;padding:16px 24px;border-bottom:1px solid var(--line)}
header b{font-size:17px}header a{color:var(--muted);text-decoration:none}header a.on{color:var(--fg);font-weight:600}
main{max-width:1100px;margin:0 auto;padding:24px 16px}h1{font-size:22px;margin:0 0 4px}p.sub{color:var(--muted);margin:0 0 20px}
table{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);border-radius:8px;overflow:hidden}
th,td{text-align:left;padding:10px 12px;border-bottom:1px solid var(--line);vertical-align:top}th{font-size:12px;color:var(--muted);font-weight:600}
td.num{font-variant-numeric:tabular-nums;text-align:right}tr:last-child td{border-bottom:0}
.chip{display:inline-block;font-size:12px;padding:1px 8px;border-radius:999px;border:1px solid currentColor;margin:0 4px 2px 0;white-space:nowrap}
.ok{color:var(--ok)}.warn{color:var(--warn)}.bad{color:var(--bad)}.muted{color:var(--muted)}a{color:var(--accent)}
code{font-size:13px}section{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:16px 20px;margin-bottom:16px}
section h2{font-size:16px;margin:0 0 8px}label{display:block;margin:6px 0}textarea{width:100%;min-height:90px;font:13px ui-monospace,monospace;background:var(--bg);color:var(--fg);border:1px solid var(--line);border-radius:6px;padding:8px}
button{font:inherit;padding:8px 16px;border-radius:6px;border:0;background:var(--accent);color:var(--bg);cursor:pointer}
.note{padding:10px 14px;border-radius:6px;border:1px solid var(--ok);color:var(--ok);margin-bottom:16px}
.wrap{overflow-x:auto}pre{white-space:pre-wrap;font-size:13px;margin:4px 0 0}
"""


def page(title: str, active: str, body: str) -> str:
    nav = "".join(
        f'<a href="{href}" class="{"on" if key == active else ""}">{label}</a>'
        for key, href, label in (("home", "/", "Sessions"), ("privacy", "/privacy", "Privacy &amp; cloud"))
    )
    return (f"<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<title>{escape(title)}</title><style>{CSS}</style></head><body><header><b>Session learning</b>{nav}</header>"
            f"<main>{body}</main></body></html>")


# -- Sessions home (KAN-111) ------------------------------------------------------


def session_summary(path: Path) -> dict:
    try:
        h, evs = loads_session(path.read_text(encoding="utf-8"), strict=False)
    except (OSError, ValueError) as e:
        return {"file": path.name, "broken": type(e).__name__}
    cmds = [e for e in evs if e.get("type") == "command"]
    end = max((e.get("t_end") or e.get("t") or 0 for e in cmds), default=0)
    from postmortem import postmortems

    try:
        incidents = postmortems(h, evs)
    except Exception:  # a malformed session shouldn't take the whole page down
        incidents = []
    return {
        "file": path.name, "id": h.get("id", path.stem), "started": h.get("started"), "origin": h.get("origin"),
        "commands": len(cmds), "edits": sum(e.get("type") == "file_edit" for e in evs),
        "annotations": sum(e.get("type") == "annotation" for e in evs),
        "corrections": sum(e.get("type") == "correction" for e in evs),
        "duration": end, "redaction": (h.get("redaction") or {}).get("version"),
        "consent": (h.get("provenance") or {}).get("consent"), "license": (h.get("provenance") or {}).get("license"),
        "incidents": len(incidents), "unresolved": sum(r["status"] == "unresolved" for r in incidents),
        "valid": not validate_session(h, evs), "skipped": h.get("_skipped_lines", 0),
    }


def _date(iso: str | None) -> str:
    if not iso:
        return "—"
    try:
        return dt.datetime.fromisoformat(iso).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return iso


def _dur(sec: float) -> str:
    sec = int(sec or 0)
    return f"{sec // 3600}h {sec % 3600 // 60}m" if sec >= 3600 else f"{sec // 60}m {sec % 60}s"


def status_chips(s: dict) -> str:
    chips = []
    chips.append(f'<span class="chip ok">scrubbed {escape(s["redaction"])}</span>' if s["redaction"]
                 else '<span class="chip bad">not scrubbed</span>')
    if s["annotations"]:
        chips.append(f'<span class="chip ok">annotated</span>')
    else:
        chips.append('<span class="chip muted">not annotated</span>')
    if s["corrections"]:
        chips.append(f'<span class="chip ok">{s["corrections"]} reviewed</span>')
    if s["incidents"]:
        cls = "bad" if s["unresolved"] else "warn"
        label = f'{s["incidents"]} incident{"s" if s["incidents"] > 1 else ""}' + (f', {s["unresolved"]} open' if s["unresolved"] else "")
        chips.append(f'<span class="chip {cls}">{label}</span>')
    if not s["valid"] or s["skipped"]:
        chips.append('<span class="chip bad">format problems</span>')
    return "".join(chips)


def render_home(directory: Path, sort: str = "date") -> str:
    files = sorted(Path(directory).glob("*.events.jsonl"))
    rows = [session_summary(p) for p in files]
    good = [r for r in rows if "broken" not in r]
    key = {"date": lambda r: r["started"] or "", "commands": lambda r: r["commands"], "incidents": lambda r: r["incidents"]}
    good.sort(key=key.get(sort, key["date"]), reverse=True)
    if not rows:
        body = (f"<h1>Sessions</h1><p class=sub>No sessions in <code>{escape(str(directory))}</code> yet.</p>"
                "<section><h2>Record one</h2><pre>eval \"$(python -m capture start --dir runs/s1 --source laptop "
                "--consent recorded-by-owner --license CC-BY-SA-4.0)\"\n# ... work ...\npython -m capture stop --dir runs/s1</pre></section>")
        return page("Sessions", "home", body)
    sort_links = " · ".join(f'<a href="/?sort={k}">{k}</a>' if k != sort else f"<b>{k}</b>" for k in ("date", "commands", "incidents"))
    trs = []
    for r in good:
        sid = escape(r["id"])
        trs.append(
            f'<tr><td><a href="/session/{quote(r["file"])}">{sid}</a><div class=muted>{escape(r["origin"] or "")}'
            f' · {escape(r["consent"] or "no consent recorded")} · {escape(r["license"] or "no license")}</div></td>'
            f"<td>{_date(r['started'])}</td><td class=num>{_dur(r['duration'])}</td><td class=num>{r['commands']}</td>"
            f"<td class=num>{r['edits']}</td><td>{status_chips(r)}</td></tr>")
    for r in rows:
        if "broken" in r:
            trs.append(f'<tr><td>{escape(r["file"])}</td><td colspan=5><span class="chip bad">unreadable: {escape(r["broken"])}</span></td></tr>')
    body = (f"<h1>Sessions</h1><p class=sub>{len(rows)} recorded session(s) in <code>{escape(str(directory))}</code>. Sort by {sort_links}.</p>"
            "<div class=wrap><table><tr><th>Session</th><th>Date</th><th style='text-align:right'>Length</th>"
            "<th style='text-align:right'>Commands</th><th style='text-align:right'>Edits</th><th>Status</th></tr>"
            + "".join(trs) + "</table></div>")
    return page("Sessions", "home", body)


def render_session(path: Path) -> str:
    """Minimal read-only view: the full Session review screen is KAN-112."""
    h, evs = loads_session(path.read_text(encoding="utf-8"), strict=False)
    from postmortem import postmortems, render_markdown

    rows = []
    for e in evs:
        if e.get("type") == "command":
            code = e.get("exit_code")
            cls = "bad" if code not in (0, None, 130) else "muted"
            out = f"<pre>{escape((e.get('output') or '')[:600])}</pre>" if e.get("output") else ""
            rows.append(f"<tr><td class=num>{e['t']:.1f}s</td><td><code>{escape(e['command'])}</code>{out}</td>"
                        f"<td class={cls}>{'' if code is None else code}</td></tr>")
        elif e.get("type") == "file_edit":
            rows.append(f"<tr><td class=num>{e['t']:.1f}s</td><td>{escape(e['change'])} <code>{escape(e['path'])}</code>"
                        f"<pre>{escape((e.get('diff') or '(binary or too large)')[:1500])}</pre></td><td></td></tr>")
    pms = "".join(f"<section><pre>{escape(render_markdown(r))}</pre></section>" for r in postmortems(h, evs))
    body = (f"<p><a href='/'>← Sessions</a></p><h1>{escape(h.get('id', path.name))}</h1>"
            f"<p class=sub>{_date(h.get('started'))} · read-only view. Tag review and correction belong to the Session review screen.</p>"
            + (f"<h2>Incidents</h2>{pms}" if pms else "")
            + "<div class=wrap><table><tr><th>Time</th><th>Event</th><th>Exit</th></tr>" + "".join(rows) + "</table></div>")
    return page(h.get("id", "Session"), "home", body)


# -- Privacy & cloud settings (KAN-115) ------------------------------------------------

SCRUB_RULES = [
    ("Typed passwords", "anything typed after a password, passphrase, PIN or token prompt"),
    ("Tokens and API keys", "GitHub, GitLab, AWS, Slack, Google, Stripe, OpenAI-style, npm, Hugging Face, JWTs, Bearer headers"),
    ("Secrets in assignments", "values of password=, token:, --api-key and similar"),
    ("Private keys", "the body of any PEM private key"),
    ("Connection strings", "user, password and host in scheme://user:pass@host"),
    ("High-randomness strings", "20+ mixed-case alphanumeric strings that look random"),
    ("IP addresses and hostnames", "except localhost, 0.0.0.0, netmasks and public infrastructure domains"),
    ("Usernames", "in prompts, e-mail addresses and home directories"),
    ("Your extra terms", "the names listed below, wherever they appear"),
]


def render_privacy(settings: dict, settings_path: Path, saved: bool = False) -> str:
    from scrubber import REDACTION_VERSION

    c = settings["cloud"]
    rules = "".join(f"<tr><td>{escape(a)}</td><td class=muted>{escape(b)}</td></tr>" for a, b in SCRUB_RULES)
    tasks = "".join(
        f"<label><input type=checkbox name=task value={t} {'checked' if t in c['tasks'] else ''}> {t}</label>" for t in TASKS)
    state = ('<span class="chip ok">local only</span>' if not c["allowed"] or not c["tasks"]
             else f'<span class="chip warn">cloud allowed for: {escape(", ".join(c["tasks"]))}</span>')
    body = (
        ("<div class=note>Saved.</div>" if saved else "")
        + f"<h1>Privacy &amp; cloud</h1><p class=sub>Stored only on this machine in <code>{escape(str(settings_path))}</code>. {state}</p>"
        + "<form method=post action=/privacy>"
        + f"<section><h2>Scrubbing</h2><p class=muted>Runs at capture time, before anything is stored or sent to a model. Version <code>{escape(REDACTION_VERSION)}</code>.</p>"
        + f"<div class=wrap><table>{rules}</table></div>"
        + "<h2 style='margin-top:16px'>Extra terms to always scrub</h2><p class=muted>Customer or project names, one per line. They're replaced with &lt;NAME_n&gt; in every new recording.</p>"
        + f"<textarea name=extra_terms autocomplete=off spellcheck=false>{escape(chr(10).join(settings['extra_terms']))}</textarea>"
        + "</section>"
        + "<section><h2>What may leave this machine</h2><p class=muted>Models run locally unless you allow a cloud model here. Prompts are scrubbed either way, and any prompt that still contains a detectable secret is refused.</p>"
        + f"<label><input type=checkbox name=cloud_allowed {'checked' if c['allowed'] else ''}> Allow cloud models</label>"
        + f"<p class=muted style='margin:8px 0 0'>For these tasks:</p>{tasks}"
        + f"<label><input type=checkbox name=require_approval {'checked' if c['require_approval'] else ''}> Ask me before each cloud call</label></section>"
        + "<button type=submit>Save</button></form>"
    )
    return page("Privacy & cloud", "privacy", body)


def settings_from_form(form: dict[str, list[str]]) -> dict:
    terms = [t for line in form.get("extra_terms", [""]) for t in line.splitlines()]
    return {
        "extra_terms": terms,
        "cloud": {"allowed": "cloud_allowed" in form, "tasks": form.get("task", []),
                  "require_approval": "require_approval" in form},
    }
