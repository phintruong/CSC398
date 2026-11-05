# Live capture and importer: implementation notes (1.1.9)

Jira: epic [KAN-2](https://alexsworkspace-43428909.atlassian.net/browse/KAN-2) · implementation [KAN-26](https://alexsworkspace-43428909.atlassian.net/browse/KAN-26) · scrubbing inside capture [KAN-35](https://alexsworkspace-43428909.atlassian.net/browse/KAN-35)
The capture design and acceptance test are KAN-25 (alex). This page describes what the code does now so the design can be checked against it. Anything that differs from KAN-25 should change here, not there.
Code: [`capture/`](../../capture) · Output format: [`contracts/events.py`](../../contracts/events.py) (event schema v1, KAN-102) · Tests: [`tests/test_capture.py`](../../tests/test_capture.py)

## Two ways in, one format out

| Path | Use it for | Gives you |
|---|---|---|
| **Live hooks** (`python -m capture start` / `stop`, or `record`) | new sessions | exact command line, cwd, exit code, duration, terminal size and resizes, `file_edit` diffs; plus output when a `.cast` is recorded alongside |
| **Importer** (`python -m capture import old.cast`) | old DocStream recordings | commands recovered from the screen (prompt detection), output, terminal size and resizes; exit code is `null` |

Both write `<name>.events.jsonl`: a `session` header line, then `command`, `resize` and `file_edit` events. `python -m capture validate FILE…` checks a file against schema v1.

## Live capture

```bash
eval "$(python -m capture start --dir runs/s1 --source julia-laptop --consent recorded-by-owner --license CC-BY-SA-4.0)"
# optional, for output: asciinema rec --stdin "$CAPTURE_DIR/.spool/raw.cast"
# ... work ...
python -m capture stop --dir runs/s1          # -> runs/s1/session.events.jsonl (+ session.cast)

# or all in one (needs asciinema): records with --stdin, hooks on, stops at exit
python -m capture record --dir runs/s1 --source … --consent … --license …
```

* **bash:** `DEBUG` trap = preexec (deduplicated by history number, so pressing Enter on an empty line records nothing), `PROMPT_COMMAND` = precmd. **zsh:** `add-zsh-hook preexec/precmd`.
* Each hook runs `python -m capture hook pre|post` (about 50–100 ms each; a long-running helper process is the obvious speed-up later). A hook never fails the shell. Errors print only the exception type and the exit status is always 0.
* **Output** isn't visible to shell hooks, so it comes from the `.cast`. At `stop`, each command gets the rendered, ANSI-stripped output printed between its preexec and precmd, trimmed to the first 1000 + last 3000 characters (`output_truncated: true`). The hook clock is matched to the cast clock using the cast's `timestamp`.
* **Terminal size:** the size at `start` goes in the header and each command carries the size at its start. Resizes are taken from the cast's `"r"` events when present, otherwise from `$COLUMNS`/`$LINES` changes seen by precmd.
* **Suspended editors (B11):** exit status 148 (Ctrl-Z) with a new job in `jobs -p` keeps the snapshot pending. The `file_edit` is produced at the first precmd after that job is gone (`t_end` after `fg`).

### Where unscrubbed data lives (KAN-35)

Hooks are separate processes, so raw state (commands, file snapshots, the raw cast) sits in `<dir>/.spool/` (mode 0700) between calls. Only `stop` reads it. `stop` scrubs **everything with one placeholder map**: the cast first, so passwords typed at prompts are learned, then commands, cwds, outputs and diffs. It writes the scrubbed files and **deletes the spool**. Nothing outside the spool is written before scrubbing. `session.cast` and `session.events.jsonl` carry `redaction` and `provenance`.

**Open point:** a crash leaves the spool behind until the next `stop`. If the team wants zero unscrubbed bytes on disk, the next step is a small capture daemon that keeps state in memory (this also makes streaming to tagging, KAN-27, easier).

## Importer

1. Load leniently: bad JSON lines, wrong-shaped events and out-of-order times are skipped or clamped and counted in `header.import.skipped_lines`. Only a missing header is fatal.
2. Convert asciicast v3 intervals to absolute times.
3. **Scrub first** with `scrub_recording`. Segmentation only ever sees scrubbed text.
4. Render the output (escape codes dropped, backspace and `\r` applied) and find prompt lines. Accepted shapes are `user@host:path$ `, `[user@host dir]$ `, `path$ `, `➜  dir`, and bare `$ `/`# `/`% `, each with an optional `(venv) ` prefix. `--prompt REGEX` handles anything unusual. The text after the prompt is the command, and output runs to the next prompt.

**Known limits:** prompts that don't match any of these shapes give no commands, so use `--prompt`. Full-screen programs (vim, htop) produce messy output text. Multi-line commands (`\` continuations, heredocs) appear as the first line only.
