# Handover: annotation, post-mortems, training data and generated scripts

Covers the work downstream of capture. Handover tickets: [KAN-80](https://alexsworkspace-43428909.atlassian.net/browse/KAN-80) (post-mortems) and [KAN-84](https://alexsworkspace-43428909.atlassian.net/browse/KAN-84) (training-data factory). Capture and scrubbing have their own notes in [capture-and-privacy.md](capture-and-privacy.md).

## 1. The flow

```
session.events.jsonl (scrubbed, schema v1)
  ├─ streaming/      annotate each event from past context ──► annotation events (goal tree)
  ├─ review/         human accepts/corrects tags          ──► correction events
  ├─ interview/      "why?" about mistakes and fixes      ──► explanation events
  ├─ postmortem/     incident chains → timeline, root cause, fix, lessons, quotes   (all cited)
  ├─ procedures/     repeated runs → canonical procedure  ──► generate/ script + Dockerfile
  ├─ trainrec/       SFT + preference records → task-grouped train/dev/test datasets
  └─ bench/, retrieval_eval/   scores against gold / queries
```

Every model call goes through `runtime` (`complete(task, evidence_ids, prompt)`), wrapped in `Guarded` (refuses unscrubbed prompts) and `Logged` (no text in logs). Until Model 1 exists, `runtime.rules.RuleAnnotator` is the no-model stand-in. `fixtures.ReplayModel` replays recorded replies.

## 2. Commands

| Task | Command |
|---|---|
| Incidents in a session | `python -m postmortem S.events.jsonl [-o pm.md] [--json]` |
| Review tags | `python -m review list S --unreviewed --mistakes` · `review correct S a6 --author h --set summary="…"` · `review accept S a7 --author h` |
| Ask "why?" | `python -m interview questions S` · `interview answer S --target f5 --question "…" --text "…" --author h` |
| Training records | `python -m trainrec export DIR -o records.jsonl` · `trainrec dataset DIR -o datasets/v1` |
| Procedure / scripts | `python -m procedures merge A B` · `python -m generate script\|dockerfile A B -o out` |
| Scores | `python -m bench baseline --gold G --out P --model rules\|replay` · `bench run --gold G --pred P` · `python -m retrieval_eval` |

## 3. Integration checks (`tests/test_analysis_integration.py`, `tests/test_golden.py`)

* Post-mortems and training export run on **every imported planted-secret recording** (rule-annotated). Every citation must exist, the citation guard must pass, and no planted value may appear in a report or training record.
* **A live daemon session** (fail → look → edit → pass) gives a resolved incident whose fix is the edit, and turns into a dataset.
* **Damaged session files** (garbage lines, a truncated last line) still work through both CLIs.
* The **golden pipeline** compares every stage's output with committed expected files.

### Bugs these checks caught (now fixed)

1. **Scrubbing wasn't idempotent.** `-p<PASSWORD_1>` and a PEM body of `<PRIVATE_KEY_1>` were detected again as secrets. The detectors now ignore values that are only placeholders, and `test_scrubbing_is_idempotent` checks every planted recording.
2. **The prompt guard read JSON escapes literally.** In a prompt, `password: \nWelcome` looked like a password. The guard now scans the JSON-decoded text, so a secret inside escaped JSON is still caught.
3. A guard refusal used to **stop the live annotation stream**. Now that event is skipped and counted (`StreamingAnnotator.refused`).

## 4. Extending

* **New event types:** add them to `contracts/events.py` (`EVENT_FIELDS`), then to `tests/test_contracts.py` and the golden test. Unknown types are rejected on purpose.
* **Plugging in Model 1:** implement `complete(task, evidence_ids, prompt) -> str` that returns the annotation JSON, wrap it in `Guarded`, and pass it to `StreamingAnnotator` or `capture daemon --annotate`. Then run `bench` against `--model rules`. Pass rules must be set relative to that floor (`benchmark.md`).
* **Changing a fixture or golden output:** `python -m fixtures build` / `GOLDEN_UPDATE=1 pytest tests/test_golden.py`, then **review the diff**.

## 5. Open issues

1. Everything has been run on synthetic or mock sessions only. Real recordings need KAN-101.
2. Post-mortems in the Artifact generator screen (KAN-79) belong to Jack's screen (KAN-113). The JSON output (`--json`) is ready for it.
3. Training (2.1.27) needs GPU time and the KAN-65 approvals. The datasets are ready (`trainrec dataset`).
4. Formats still to be agreed by the team: training records (KAN-103), the retrieval eval format (KAN-69), and the benchmark pass rules (KAN-64).
