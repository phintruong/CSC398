# Shared fixtures (KAN-106)

Mock sessions and recorded model replies that every group can test against without real recordings or a model.

| File | What's in it |
|---|---|
| `data/mock-nginx-port.events.jsonl` | Move nginx to port 8080. A typo (missing `;`) fails `nginx -t`, then gets fixed. Has a config `file_edit` with diffs, mistakes, and a human `correction`. |
| `data/mock-nginx-port-rerun.events.jsonl` | The same task done cleanly in another session, for merging repeated runs (1.1.10). |
| `data/mock-python-venv.events.jsonl` | `pip install` blocked by PEP 668, recovered with a venv. |
| `data/mock-git-conflict.events.jsonl` | `git pull` conflict resolved in vim. |
| `data/mock-docker-build.events.jsonl` | `docker build` fails on a file-name typo in the Dockerfile, then gets fixed. |
| `data/mock-resize-htop.events.jsonl` | Terminal resizes around a full-screen program. |
| `data/model_replies.jsonl` | Recorded `annotate` / `summarize` / `answer` replies, keyed by task + evidence ids. |

All sessions follow event schema v1 (`contracts/events.py`). Identifiers are already placeholders (`<USER_1>`, `<HOST_1>`), as if scrubbed.

```python
from fixtures import load_sessions, ReplayModel
sessions = load_sessions()                 # {session_id: (header, events)}
model = ReplayModel()
model.complete("annotate", ["mock-docker-build#c2"])   # recorded JSON reply, KeyError if none
```

To change a fixture, edit `fixtures/build.py`, then run `python -m fixtures build`. `tests/test_fixtures.py` fails if the committed files are stale.
