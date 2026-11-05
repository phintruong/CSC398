# Design: file-edit detection (#50)

Jira: epic [KAN-3](https://alexsworkspace-43428909.atlassian.net/browse/KAN-3) · design and acceptance test [KAN-29](https://alexsworkspace-43428909.atlassian.net/browse/KAN-29)
Status: **draft for team sign-off** (section 9 lists the open questions)
Depends on: 1.1.9 live capture · Feeds: tagging, Dockerfile/automation templates (Group 5), `get_file_history` (Group 6) · Privacy: every diff goes through the [scrubber](scrubbing.md)

## 1. Goal

When someone edits a file from the terminal, the asciinema stream shows keystrokes and screen redraws, not *what changed*. For a vim session the stream is mostly cursor movement. We want a `file_edit` event that holds the **unified diff** of the file, from before the command started to after it finished, linked to the time in the recording. Config files are flagged, because they are what the Dockerfile and automation templates need.

## 2. Where it runs

Detection runs **inside the live-capture shell (1.1.9)**, using shell hooks:

| Shell | Before a command | After a command |
|---|---|---|
| bash | `bash-preexec` (or a `DEBUG` trap) | `PROMPT_COMMAND` |
| zsh | `preexec` | `precmd` |

Each hook calls a small helper (`fileedit` module) with the command line, the cwd and the current recording time offset. Everything happens on the machine that is being recorded. File contents never leave it unscrubbed.

## 3. What counts as an edit

The `preexec` hook parses the command line with `shlex`. It handles `sudo`, `sudoedit`, `env`/`VAR=value` prefixes, and compound commands split on `&& || ; |`, following `cd` inside the compound. That gives a list of **target files**:

| Tool | Targets | Notes |
|---|---|---|
| `vim vi nvim nano emacs micro ed sudoedit` | every non-option argument | `+42` and `-R` style options are skipped. With no file argument there are no targets. |
| `sed -i` / `sed --in-place`, `perl -i` | file arguments after the script / `-e` expressions | Without `-i` nothing is edited. |
| Redirects `>`, `>>`, `&>`, `1>`, `2>` | the redirect target | `/dev/*` and `&N` are ignored. Covers `cat <<EOF > file`. |
| `tee [-a]` | every file argument | including `sudo tee /etc/hosts` |
| `patch [-pN] [-i file \| < file]` | files named in the patch (`+++ b/…` lines, with `-pN` stripping) | |
| `git apply`, `git am` | files named in the patch | |
| `git checkout -- f`, `git restore f` | the named paths | These discard edits, which is also a change worth recording. |

**Not in v1:** `cp`, `mv`, `rm`, `touch` (these are file operations, not edits; they could become `file_create`/`file_delete` events later), changes made by programs the command runs (`make`, `npm install`), and edits from an IDE outside the terminal. An optional fallback, off by default, compares the mtimes of files directly in the cwd for unrecognised commands.

**Remote sessions are the big gap.** Most of Julia's work happens over `ssh`, and the hooks don't run on the remote host. v1 records local edits only. Options for v2 are in section 9.

## 4. Snapshots and diffs

1. **preexec:** for each target, resolve the real path (following symlinks, relative to the cwd at that point), then read it if it exists, is a regular file, is ≤ 1 MiB and is text (no NUL byte in the first 8 KiB). Keep it in memory, or in a `0700` temp dir if it's large. A missing file is recorded as "did not exist".
2. **precmd:** read each target again and compare.
   * Unchanged: no event. (Opening a file in vim and quitting with `:q!` produces nothing.)
   * Changed: produce a unified diff (`difflib.unified_diff`, 3 lines of context, `a/<path>` → `b/<path>`).
   * Created or deleted: `change: "created"` or `"deleted"`, with the diff against empty.
   * Binary or > 1 MiB: an event with `binary: true` or `truncated: true` and no full diff.
3. **Suspended editors** (`Ctrl-Z`): `precmd` fires while vim is still open. Targets whose job is still alive (`jobs -p`) stay pending and are compared at each following `precmd` until the job exits.
4. **Diffs are scrubbed** with `Scrubber.scrub_text()` before they are written. The scrubber shares the recording's `PlaceholderMap`, so `<HOST_1>` in a diff means the same host as `<HOST_1>` in the cast.

## 5. Event format

The events go in a sidecar file next to the recording, `<name>.edits.jsonl`, one JSON object per line. Time uses the same clock as the cast. A sidecar keeps the `.cast` file valid for asciinema players and the existing pipeline. Model 0/1 can merge the two streams by time.

```json
{"type": "file_edit", "t": 12.84, "t_end": 47.10,
 "path": "/etc/nginx/sites-available/default", "tool": "vim",
 "command": "sudo vim /etc/nginx/sites-available/default",
 "change": "modified", "is_config": true,
 "diff": "--- a/etc/nginx/sites-available/default\n+++ b/etc/nginx/sites-available/default\n@@ -36,7 +36,7 @@\n-\tlisten 80;\n+\tlisten 8080;\n",
 "binary": false, "truncated": false, "redaction": "scrub-0.2.0"}
```

`change` is one of `created | modified | deleted`. Paths inside home directories are scrubbed the same way as in the cast (`/home/<USER_1>/…`).

## 6. Flagging config files

`is_config: true` when **any** of these holds:

* The path is under `/etc/`, `/usr/local/etc/`, `/opt/*/conf*`, `~/.config/` or `~/.ssh/`.
* The name is a dotfile (`.bashrc`, `.env`, `.gitconfig`, `.npmrc`).
* The extension is `.conf .cfg .ini .yaml .yml .toml .json .env .properties .service .timer .socket .rules`.
* The name is `Dockerfile`, `docker-compose.*`, `Makefile`, `nginx.conf`, `crontab`, `hosts`, `fstab`, `sshd_config`, or a `*.d/` drop-in.

## 7. Module API (what the tests call)

```python
fileedit.targets_for_command(command: str, cwd: str) -> list[tuple[str, str]]   # [(absolute_path, tool)]
fileedit.is_config(path: str) -> bool
fileedit.Snapshot.take(paths) -> Snapshot             # preexec
fileedit.Snapshot.diff(after: Snapshot, *, scrubber, t, t_end, command) -> list[dict]   # precmd
```

`targets_for_command` and `is_config` are pure functions, so most of the acceptance test runs without a shell.

## 8. Acceptance test

**Part A, command parsing and config flags** (pure; [`tests/test_file_edit_acceptance.py`](../../tests/test_file_edit_acceptance.py)). These tests are written now and are **skipped until the `fileedit` module exists**. Then they become the gate. They cover editors with options, `sudo` and env prefixes, `sed -i` vs plain `sed`, every redirect form including `/dev/null` and heredocs, `tee -a`, compound commands with `cd`, read-only commands that must produce nothing, and the config rules above.

**Part B, end to end** (run through the capture shell in a temp dir, expect these events in `.edits.jsonl`):

| # | Session | Expected |
|---|---|---|
| B1 | `vim notes.txt`, add a line, `:wq` | 1 event, `modified`, diff shows the added line, tool `vim` |
| B2 | `vim notes.txt`, `:q!` | no event |
| B3 | `sed -i 's/80/8080/' site.conf` | 1 event, `is_config: true` |
| B4 | `echo 'export X=1' >> ~/.bashrc` | 1 event, `is_config: true`, tool `redirect` |
| B5 | `ls > listing.txt` (new file) | 1 event, `created` |
| B6 | `echo hi \| tee -a a.log b.log` | 2 events |
| B7 | `patch -p1 < fix.diff` touching 2 files | 2 events, tool `patch` |
| B8 | `git apply fix.diff` | events for each file in the patch |
| B9 | `cat site.conf`, `grep x site.conf`, `ls 2>/dev/null` | no events |
| B10 | `vim big.bin` (binary) | 1 event, `binary: true`, no diff |
| B11 | `vim app.py`, `Ctrl-Z`, `ls`, `fg`, edit, `:wq` | exactly 1 event, `t_end` after the `fg` |
| B12 | `echo "DB_PASSWORD=<planted>" > .env` | 1 event; the planted value is **absent** from the diff and replaced by the same placeholder as in the cast (ties to [scrubbing A1](scrubbing.md#5-acceptance-test-planted-secret-test-set)) |
| B13 | `cd /tmp && nano x.txt` | event path `/tmp/x.txt` |

**Pass** = all of Part A, plus B1–B13 producing exactly the listed events (no extra events in any scenario).

## 9. Open questions for the team

1. **Remote edits:** do we (a) install the hook on the hosts Julia ssh's into, (b) infer edits from the stream (find `cat file` before and after, or rebuild the vim buffer with a terminal emulator), or (c) leave it out of scope for this term? Proposal: (c) for M1, and look at (b) with the Group 6 people later.
2. **Size limit:** is 1 MiB right? Generated files and lockfiles can be bigger and aren't interesting.
3. **Sidecar vs inline:** the sidecar keeps casts playable. Does Model 0/1 ingestion prefer the events inline, as custom asciicast `"x"` events?
4. **cp/mv/rm:** should they be in v1 as file events without diffs?
