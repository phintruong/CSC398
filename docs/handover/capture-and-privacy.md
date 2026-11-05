# Handover: capture, file edits and scrubbing (Group 1)

Covers [KAN-2](https://alexsworkspace-43428909.atlassian.net/browse/KAN-2) live capture, [KAN-3](https://alexsworkspace-43428909.atlassian.net/browse/KAN-3) file-edit context and [KAN-4](https://alexsworkspace-43428909.atlassian.net/browse/KAN-4) scrubbing. Handover tickets: KAN-28 (capture) and KAN-32 (file edits).
Audience: someone who didn't build this and has to run, fix or extend it.

## 1. Setup (5 minutes)

```bash
git clone https://github.com/phintruong/CSC398 && cd CSC398
python3 --version            # 3.10+
pip install pytest && pytest # ~325 tests, all should pass; nothing else to install
```

* **No runtime dependencies.** Everything uses the Python standard library. Optional external tools: `asciinema` (to record output alongside the hooks, used by `python -m capture record`), `bash` or `zsh`, and `patch`.
* **Line endings:** `.gitattributes` forces LF for the hook scripts, `.cast` and `.jsonl` files. If a hook fails on Linux with `$'\r': command not found`, the checkout ignored it. Run `git add --renormalize .`.

## 2. Daily use

| Task | Command |
|---|---|
| Record a new session (hooks + asciinema) | `python -m capture record --dir runs/s1 --source <machine> --consent recorded-by-owner --license CC-BY-SA-4.0` |
| Hooks only, in the current shell | `eval "$(python -m capture start --dir runs/s1 --source … --consent … --license …)"`, then work, then `python -m capture stop --dir runs/s1` |
| Convert an old recording | `python -m capture import old.cast -o old.events.jsonl --cast-out old.clean.cast --source … --consent … --license …` |
| Scrub a cast without converting it | `python -m scrubber in.cast -o out.cast --source … --consent … --license …` |
| Check files against the schema | `python -m capture validate runs/*/session.events.jsonl` |
| Always scrub customer or project names | the dashboard's Privacy page, or `~/.config/csc398/privacy.json` → `extra_terms` (also `--term NAME` per run) |

**What ends up on disk:** `session.events.jsonl` (schema v1) and `session.cast` (scrubbed). Both carry `provenance` and `redaction`. Nothing else stays behind after `stop`.

## 3. How it fits together

```
shell hooks (bash DEBUG trap / zsh preexec+precmd)
   └─ python -m capture hook pre|post ──► .spool/state.json  (0700, raw, deleted at stop)
asciinema rec --stdin ─────────────────► .spool/raw.cast   (raw, deleted at stop)
python -m capture stop
   └─ one PlaceholderMap: scrub cast → commands/cwds → outputs (from cast) → file_edit diffs
      └─ session.events.jsonl + session.cast
```

| Module | Responsibility | Where to start reading |
|---|---|---|
| `scrubber/` | find secrets/PII, assign `<KIND_n>` placeholders, rewrite casts | `scrubber/core.py` (`Scrubber`), `scrubber/detect.py` (detectors) |
| `fileedit/` | which files a command edits; before/after snapshots; diffs | `fileedit/detect.py` (`targets_for_command`), `fileedit/snapshot.py` |
| `capture/` | session state across hook calls; stop-time scrubbing; importer | `capture/session.py`, `capture/importer.py` |
| `contracts/` | the schema everything writes | `contracts/events.py` |

## 4. Troubleshooting

| Symptom | Likely cause and fix |
|---|---|
| Hook prints nothing and no events are recorded | `CAPTURE_DIR`/`CAPTURE_PY` aren't set in that shell (use `eval "$(… start …)"`, not `start` alone), or `PYTHONPATH` doesn't include the repo. Run `"$CAPTURE_PY" -m capture hook post --dir "$CAPTURE_DIR"` by hand; errors print their type only. |
| `a capture is already running` | A previous session crashed. Run `python -m capture stop --dir <dir>` to scrub and finish it. **Don't delete `.spool` by hand without stopping**, because you'd lose the session. Deleting it is safe if you don't need the data. |
| Every command is recorded twice, or empty Enter creates events | Another tool also uses the bash `DEBUG` trap (for example an older `bash-preexec`). Load ours last, or switch to bash-preexec's hook arrays. |
| Imported cast has 0 commands | The prompt shape isn't recognised. Pass `--prompt '^(?P<p>your-prompt-regex )'`. |
| A secret survived scrubbing | **Add it to the planted set first** (`tests/planted_set.py`), watch the test fail, then fix the detector, and bump `REDACTION_VERSION` in `scrubber/core.py`. Recordings with an older version should be re-scrubbed. |
| `file_edit` missing for an edit | See the supported tools in `docs/design/file-edit-detection.md` §3. Programs that write files themselves (`make`, IDEs) and remote edits over ssh aren't detected in v1. |

## 5. Tests that guard this area

| Test | Guards |
|---|---|
| `tests/test_planted_secrets.py` | scrubber recall 52/52, no over-scrubbing (A1–A7) |
| `tests/test_privacy_pipeline.py` | planted secrets never reach stored files or logs, through import **and** live capture |
| `tests/test_file_edit_acceptance.py`, `tests/test_file_edit_snapshots.py` | command parsing (Part A), end-to-end edit scenarios (Part B) |
| `tests/test_capture.py` | importer, session flow, suspended editors, a **real interactive bash run** of the hooks |
| `tests/test_contracts.py` | output stays valid on broken recordings |
| `tests/test_golden.py` | the whole pipeline on a golden recording |

## 6. Open issues (what's not done)

1. **Real recordings (KAN-28, KAN-32):** everything has been tested on synthetic recordings only. The integration checks on the mentors' real DocStream recordings still need running, and they need the recordings and permissions (KAN-101). Expect prompt-shape misses in the importer; see the `--prompt` row above.
2. **zsh hooks are untested**, because no zsh was available. Run `tests/test_capture.py::test_bash_hooks_end_to_end`'s steps by hand in zsh.
3. **Raw spool on disk between hooks.** A crash leaves unscrubbed data in `.spool/` (mode 0700) until the next `stop`. The fix is a small long-running capture process that keeps state in memory. That same process would also let events stream into tagging live (KAN-27).
4. **Remote edits** (`ssh` then vim on the server) aren't captured. The options are in `docs/design/file-edit-detection.md` §9.
5. **Full-screen programs:** secrets shown only on a redrawn screen (vim, less) can slip past the stream-based scrubber. Sessions that include these shouldn't be used for training until a render-then-scan pass exists (`scrubbing.md` §6).
6. **Team decisions still pending:** the list in `scrubbing.md` §7 (private IP ranges, the domain allowlist, consistency across recordings, the consent and license vocabulary).
