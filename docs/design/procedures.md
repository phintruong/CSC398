# Merging repeated runs into canonical procedures (1.1.10): implementation notes

Jira: epic [KAN-7](https://alexsworkspace-43428909.atlassian.net/browse/KAN-7) · embed + align [KAN-46](https://alexsworkspace-43428909.atlassian.net/browse/KAN-46)
Design and hand-labelled same-task pairs: KAN-45 (Richelle). This page describes what the code does so it can be checked against that design. The precision target (KAN-48) needs those labelled pairs.
Code: [`procedures/`](../../procedures) · Tests: [`tests/test_procedures.py`](../../tests/test_procedures.py)

## Pipeline

1. **Steps:** the commands and file edits of a session, with corrected annotations. An editor command is folded into its edit. Noise (`cd`, `clear`, `exit`) is dropped.
2. **Embedding:** each step has two texts. The *action* is the normalized command (no `sudo`, `<HOST_1>` becomes `<HOST>`, numbers become `N`) or `edit <path>`. The *description* is the annotation's goal and summary. Each text is embedded and the two unit vectors are concatenated with weights √0.6 and √0.4, so cosine similarity = **0.6 · action + 0.4 · description**. That way the fix edit still matches the same edit in a clean run, even though their descriptions differ. The default embedder is a dependency-free hashed bag of words with character trigrams. Any `embed(texts)` (for example a sentence-transformer) can replace it.
3. **Alignment:** Needleman–Wunsch global alignment (match = similarity − 0.3, gap = −0.4). Aligned pairs below 0.35 similarity are reported as two gaps.
4. **Same task?** `same_task_score` = summed matched similarity / length of the longer run. On the fixtures, the two nginx runs score 0.69 and every other pair scores ≤ 0.05. The CLI uses a 0.4 threshold, to be tuned on KAN-45's labelled pairs.
5. **Canonical procedure:** the reference is the run with the fewest failures. Every other run is aligned to it **after dropping its failed steps**, so a fix lines up with the clean step and the mistake doesn't. Each reference step that is good (not failed, not just looking around) and appears in at least `min_support` of runs is kept, with evidence ids from every run where it appears and its support level.

```bash
python -m procedures same-task fixtures/data/*.events.jsonl
python -m procedures merge fixtures/data/mock-nginx-port*.events.jsonl
```

The output (`n`, `action`, `command` or `path` + `diff`, `why`, `support`, `evidence`) is what the Dockerfile and automation templates (KAN-47, Group 5) consume.

## Known limits

* With two runs, a step that only one run needed has 50% support and is kept by default. Raise `--min-support` to keep only steps every run did.
* Steps done in a different order across runs align only partially, because global alignment keeps order.
* The bag-of-words embedder only knows surface words. `apt install nginx` and `dnf install nginx` will match, but synonyms in summaries won't. Swapping in a real embedder is the first thing to try if KAN-48 precision is short.
