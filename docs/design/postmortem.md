# Design: incident post-mortem generator (1.2.11)

Jira: epic [KAN-15](https://alexsworkspace-43428909.atlassian.net/browse/KAN-15) · design + acceptance test [KAN-77](https://alexsworkspace-43428909.atlassian.net/browse/KAN-77) · implementation [KAN-78](https://alexsworkspace-43428909.atlassian.net/browse/KAN-78)
Code: [`postmortem/`](../../postmortem) · Tests: [`tests/test_postmortem.py`](../../tests/test_postmortem.py) · Input: event schema v1 ([`contracts/events.py`](../../contracts/events.py))
Status: **draft for team sign-off**

## 1. Goal

When something breaks in a session and then gets fixed, a learner should get a short write-up: what failed, why, what fixed it, and what to watch for. **Every statement points at the events it came from** (evidence ids such as `mock-nginx-port#f5`), so the report can link back to the Session review screen and nothing in it is invented.

## 2. What a post-mortem contains

| Section | Built from | Cites |
|---|---|---|
| Title | the goal-tree root goal of the first failing step | |
| Status, duration | the resolution step (or *unresolved*) | |
| Summary | first failure + resolution | both |
| Timeline | every step in the chain: time offset, command or edit, ok/failed, annotation summary | each step |
| Root cause | the first failing step. A mistaken **edit** is the cause, and the command that exposed it (`nginx -t`) supplies the error lines. The edit's diff is shown. | cause + exposing command |
| Fix | the non-mistaken file edits plus the state-changing commands after the cause, ending with the resolution. Diagnostics (`ls`, `git status`, `cat`) stay in the timeline but are left out of the fix. | each step |
| Lessons | the summary of each mistake step. For a broken config edit: "test config changes with `<the command that caught it>` first". | mistake steps |

## 3. Finding chains

A **step** is a command or a file edit. A command that only opened an editor is folded into its `file_edit`. Corrections are applied first: the latest `correction` per (annotation, field) overrides the model's value. Corrections never edit in place.

* **Failed:** the annotation says `mistake: true` or `result: failure`. With no annotation, a command with a non-zero exit code counts as failed, except 130 (Ctrl-C).
* **A chain starts** at a failed step that isn't already in a chain.
* **It follows related steps:** same goal, same command head (`nginx -t` retried), or the same goal-tree root. The chain stops after 12 unrelated steps in a row, on the assumption that the user moved on.
* **It ends** at the first successful command whose goal or head matches something that failed in the chain (*resolved*). If there is no such command but every step after the last failure succeeded, the last of them is taken as the resolution (*resolved (inferred)*, as when a `git pull` conflict ends in `git commit`). Otherwise the chain is *unresolved*.

## 4. Wording model (optional)

The generated text is deterministic. A `wording_model(report, markdown) -> markdown` hook can rephrase it for readability, and its output is **accepted only if**:

1. every paragraph and bullet outside code blocks cites at least one `[session#event]`;
2. every cited id is in the report's evidence set (no new facts, no other sessions).

Otherwise, or if the model raises an error, the deterministic text is used, and `render()` reports why (`deterministic (model rejected: …)`).

## 5. Privacy

The generator reads only session files, which are scrubbed at capture (KAN-35). The CLI refuses a session whose header has no `redaction` block.

## 6. Acceptance test

| # | Input | Expected |
|---|---|---|
| P1 | `mock-nginx-port` (typo edit → `nginx -t` fails → fix → passes) | 1 incident, *resolved*. Root cause = the edit, with the diff and the `[emerg]` line. Fix = the second edit. Lesson about testing the config. |
| P2 | `mock-python-venv` | 1 incident. Fix = venv, activate, pip, with no diagnostics. |
| P3 | `mock-git-conflict` | 1 incident, *resolved (inferred)*. Conflict lines in the root cause. |
| P4 | `mock-docker-build` | 1 incident. `ls` is in the timeline but not in the fix. |
| P5 | `mock-nginx-port-rerun`, `mock-resize-htop` | no incidents |
| P6 | two independent failures in one session | 2 incidents |
| P7 | failure never fixed | 1 incident, *unresolved*, "No fix recorded" |
| P8 | a correction overrides an annotation summary | the corrected text appears and the original doesn't |
| P9 | every cited id | exists in the session |
| P10 | a wording model that adds an uncited sentence or cites an unknown id | rejected, deterministic text used |
| P11 | no annotations at all (imported session, exit codes only) | incidents found from exit codes |

## 7. Open questions

1. Should the "Post-mortem" output type in the Artifact generator screen (KAN-79) show one report per incident, or one per session?
2. Is 12 unrelated steps the right gap before a chain is treated as abandoned?
3. Once Model 1 tags exist, should `result: partial` count as a failure?
