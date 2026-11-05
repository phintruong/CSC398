# Benchmark and eval harness (1.1.7)

Jira: epic [KAN-11](https://alexsworkspace-43428909.atlassian.net/browse/KAN-11) · benchmark suite + description rubric [KAN-62](https://alexsworkspace-43428909.atlassian.net/browse/KAN-62)
Code: [`bench/`](../../bench) · Tests: [`tests/test_bench.py`](../../tests/test_bench.py)
Status: **harness done and running on the fixtures.** Two things are still missing: the mentors' labelled test set, and the Model 0/1 baselines from KAN-61. Once they exist, point `--gold` at the labelled set and `--pred` at the model output.

## What it measures

| Area | Metric | Notes |
|---|---|---|
| Model 0 (segmentation) | event-boundary precision / recall / F1 | Predicted command start times are matched one to one with gold within ±1 s. Two events merged into one shows up as a missed boundary. |
| Model 1 (tagging) | `tool` and `result` accuracy, `result` macro F1, `mistake` precision/recall/F1, goal word overlap, coverage | Gold labels have **corrections applied** (latest per field), so reviewers' fixes count. |
| Goal tree | parent-attachment accuracy | the share of events whose predicted parent is the same event as gold's parent |
| Descriptions | rubric (below) | deterministic, with an optional LLM judge that can only make a check stricter |

Session scores are averaged per session (macro), so one long session can't dominate.

### Description rubric

| Check | Passes when |
|---|---|
| **faithful** | every specific token in the summary (file names, flags, versions, numbers) appears in the event's command, output, diff, path or exit code. This catches invented details, e.g. "PEP 668" when the output never mentions it. |
| **specific** | not a generic phrase ("ran a command"), and it names a concrete token or the tool, or is at least 5 words |
| **concise** | 3–30 words |
| **explains_outcome** | for failures and mistakes, the summary says what went wrong |

A judge passed to `descriptions(..., judge=fn)` returns the same keys and can only turn a pass into a fail. Route it through `runtime.Guarded` like any other model call.

## Running it

```bash
python -m bench baseline --gold fixtures/data --out runs/baseline          # reference predictions (streaming + replay)
python -m bench run --gold fixtures/data --pred runs/baseline -o report.json [--rules rules.json]
```

`run` prints a summary and exits **1 when a pass rule fails**, so CI can gate on it. KAN-63 (quick checks per PR, weekly full run) only needs to call this from the workflow Jack is setting up in KAN-107.

**Pass rules** (`bench.runner.DEFAULT_RULES`) are placeholders until the team freezes them on the dev set (KAN-64): segmentation F1 ≥ 0.80, result macro F1 ≥ 0.60, mistake F1 ≥ 0.50, tree attachment ≥ 0.60, description overall ≥ 0.70. A rule whose metric doesn't apply is skipped (for example, segmentation when the predictions only contain annotations). Missing predictions always fail the run.

## Current numbers on the fixtures

The replay baseline matches the fixtures exactly, so segmentation and tagging are 1.0 by construction. The rubric scores the fixture summaries at **0.86**. It flags two unsupported claims ("PEP 668", "390% CPU") and a handful of vague summaries ("Config test passes."). That is useful feedback on the fixtures themselves.
