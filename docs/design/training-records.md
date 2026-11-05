# Design: corrections and training records (KAN-103, KAN-81, KAN-82)

Jira: correction + training-record format [KAN-103](https://alexsworkspace-43428909.atlassian.net/browse/KAN-103) · trajectory export design [KAN-81](https://alexsworkspace-43428909.atlassian.net/browse/KAN-81) · export [KAN-82](https://alexsworkspace-43428909.atlassian.net/browse/KAN-82)
Code: [`trainrec/`](../../trainrec) · Tests: [`tests/test_trainrec.py`](../../tests/test_trainrec.py) · Input: event schema v1 ([`contracts/events.py`](../../contracts/events.py))
Status: **draft for team sign-off**

## 1. Corrections

A person fixing a model output never edits the annotation. They append a `correction` event to the session:

```json
{"type": "correction", "id": "x19", "target": "a6", "field": "summary",
 "old": "Changed the port but dropped the semicolon.",
 "new": "Changed the port to 8080 but deleted the semicolon at the end of the listen line.",
 "author": "reviewer-1", "t_created": "2026-11-01T12:00:00+00:00"}
```

* `target` is an annotation id and `field` is one annotation field (`goal`, `tool`, `result`, `mistake`, `summary`, `parent`).
* **The latest correction per (target, field) wins**, ordered by `t_created`, then file order. Older corrections stay in the file as history.
* Readers apply corrections with `postmortem.core.apply_corrections`, which every consumer shares. The originals stay intact, and that's what makes preference pairs possible.
* `author` is a reviewer handle, not a real name. The Session review screen (KAN-112) writes these events, and so does the CLI below.
* **Accepting** a label without changing it writes a `correction` with `field: "reviewed", new: true`, so "a person checked this" is recorded rather than assumed. After corrections are applied, an annotation carries `reviewed: true` and `changed_fields` (the fields the person actually changed). Training records label it `human-corrected` (something changed), `human-reviewed` (accepted as is) or the model name.

```bash
python -m review list    session.events.jsonl --unreviewed --mistakes
python -m review correct session.events.jsonl a6 --author rev-1 --set summary="..." --set mistake=true
python -m review accept  session.events.jsonl a7 a8 --author rev-1
```

This also works on a capture daemon's `live.events.jsonl` while the session is running (KAN-39). Input is validated (`result` values, booleans, an existing `parent`), and a bad correction writes nothing.

## 2. One training-record format (`trainrec-v1`)

The same envelope serves SFT and preference (DPO) data, so one loader handles both:

```json
{"schema": "trainrec-v1", "kind": "sft" | "preference", "task": "annotate", "id": "<stable hash>",
 "input": {"session_term": {"cols": 120, "rows": 32},
           "context": [ {"id": "c2", "type": "command", "command": "...", "cwd": "...", "exit_code": 0,
                         "output": "...", "label": {"goal": "...", ...}}, ... ],
           "target":  {"id": "f5", "type": "file_edit", "path": "...", "diff": "...", ...}},
 "output":   {"goal": ..., "tool": ..., "result": ..., "mistake": ..., "summary": ...},      // sft
 "chosen":   {...}, "rejected": {...},                                                       // preference
 "provenance": {"session": "...", "evidence": ["<session>#c2", ..., "<session>#f5"], "annotation": "a6",
                "redaction": "scrub-0.2.0", "consent": "...", "license": "...", "source": "...",
                "label_source": "human-corrected" | "<model name>", "correction_authors": ["reviewer-1"]}}
```

* **Input** is the target event plus up to 8 events **strictly before it**, each with its earlier (corrected) label as goal context. Nothing after the target is ever included, so there is no future leakage. That matches the streaming setting (KAN-41), where Model 1 only sees the past.
* Outputs are trimmed to 1000 characters and diffs to 2000, keeping both the head and the tail.
* **`id`** is a hash of the content, so re-exporting gives the same ids and duplicates can be dropped.
* **Provenance** carries everything needed to filter or delete records later: the session and evidence ids (for "remove everything from session X"), the redaction version (to re-export after a scrubber upgrade), consent and license, and whether the label is human-corrected.

## 3. Export rules

| Rule | Why |
|---|---|
| A session without a `redaction` block is refused (`NotScrubbed`) | nothing unscrubbed goes into training data (KAN-35) |
| Consent must be in `{recorded-by-owner, consented, synthetic}` and the license must not be `none`, `all-rights-reserved`, `proprietary` or `unknown` (configurable) | only data people agreed to share |
| SFT: one record per annotation on a command or edit, with labels after corrections | |
| Preference: only annotations with a correction that changed something. `chosen` = corrected labels, `rejected` = the model's original. | human corrections are the preference signal |

```bash
python -m trainrec export fixtures/data -o records.jsonl [--kind sft|pref|both] [--context 8]
```

## 4. Acceptance test

| # | Check |
|---|---|
| T1 | Every exported record passes `validate_record`. |
| T2 | No record's input contains an event at or after its target, or any text that appears only after the target. |
| T3 | Corrections: the SFT output uses the corrected value. The preference record has chosen = corrected and rejected = original. A correction to the same value gives no preference record. |
| T4 | Unscrubbed sessions raise an error. Disallowed consent or license is skipped by the CLI with the reason. |
| T5 | Re-exporting gives identical ids and records. |
| T6 | Every provenance evidence id exists in its session. |

## 5. Open questions

1. Do the mentors' correction API records (Jack's old API, KAN-65) map onto `correction` events one to one, or do we need an importer?
2. Should SFT records exclude labels from `mock-model-*` and other unreviewed model output, training only on human-reviewed labels?
3. Is 8 events of context right for Model 1's context window?

## 6. Packaging for the flywheel (KAN-83 → 2.1.27)

```bash
python -m trainrec dataset fixtures/data -o datasets/v1 [--dev 0.1 --test 0.1 --salt v1]
```

Writes `sft.{train,dev,test}.jsonl` (chat `messages`: system / user / assistant), `dpo.{train,dev,test}.jsonl` (`prompt` / `chosen` / `rejected`, the shape TRL's DPO trainer expects) and `manifest.json`.

* **Splits are by task, not by record.** Sessions whose runs are the same task (same-task score ≥ 0.4, [procedures.md](procedures.md)) are grouped, and a group's split comes from a hash of the salt and the group key. A near-copy of a training session can't end up in test, and adding new sessions never moves old ones.
* **Train/serve parity:** the user prompt is byte-identical to what the streaming annotator sends live (`streaming.build_prompt`), and a test checks this.
* Records are deduplicated by id. Every row's `meta` carries the session, evidence ids, redaction version, license and label source, so a session can be removed later.
* The manifest lists the sessions per split, the task groups, the redaction versions and the policy skips. It holds no content.

What the training side (KAN-66 onward) still has to do: run training on these files. That needs GPU time and approved teacher models (KAN-65).
