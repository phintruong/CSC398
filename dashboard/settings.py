"""Privacy & cloud settings (KAN-115): one local JSON file every component reads.

Location: $CSC398_SETTINGS, else ~/.config/csc398/privacy.json. It never
leaves the machine and is never committed. Defaults are the safe choice:
nothing goes to the cloud.

    {"extra_terms": ["AcmeCorp"],          # always scrubbed (customer / project names)
     "cloud": {"allowed": false,           # master switch for the router (KAN-9 / KAN-54)
               "tasks": [],                # which tasks may use a cloud model when allowed
               "require_approval": true}}  # ask before each cloud call
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

TASKS = ("annotate", "summarize", "answer", "postmortem")
DEFAULTS: dict[str, Any] = {
    "extra_terms": [],
    "cloud": {"allowed": False, "tasks": [], "require_approval": True},
}


def path() -> Path:
    env = os.environ.get("CSC398_SETTINGS")
    return Path(env) if env else Path.home() / ".config" / "csc398" / "privacy.json"


def normalize(raw: Any) -> dict:
    """Coerce anything into a valid settings dict (unknown keys dropped, bad values -> defaults)."""
    s = json.loads(json.dumps(DEFAULTS))
    if not isinstance(raw, dict):
        return s
    terms = raw.get("extra_terms")
    if isinstance(terms, list):
        seen = []
        for t in terms:
            if isinstance(t, str) and t.strip() and t.strip() not in seen:
                seen.append(t.strip())
        s["extra_terms"] = seen
    cloud = raw.get("cloud")
    if isinstance(cloud, dict):
        s["cloud"]["allowed"] = cloud.get("allowed") is True
        s["cloud"]["tasks"] = [t for t in TASKS if t in (cloud.get("tasks") or [])]
        s["cloud"]["require_approval"] = cloud.get("require_approval") is not False
    return s


def load(p: Path | None = None) -> dict:
    p = p or path()
    try:
        return normalize(json.loads(p.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return normalize(None)


def save(settings: dict, p: Path | None = None) -> Path:
    p = p or path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(normalize(settings), indent=2) + "\n", encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, p)
    return p


def cloud_allowed(task: str, settings: dict | None = None) -> bool:
    """What the router asks before sending anything off the machine."""
    s = settings or load()
    return s["cloud"]["allowed"] and task in s["cloud"]["tasks"]
