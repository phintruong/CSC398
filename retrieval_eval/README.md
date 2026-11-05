# Retrieval eval set (1.2.16)

Jira: epic [KAN-13](https://alexsworkspace-43428909.atlassian.net/browse/KAN-13) · format + first 20 queries [KAN-69](https://alexsworkspace-43428909.atlassian.net/browse/KAN-69) · recall@k and MRR [KAN-71](https://alexsworkspace-43428909.atlassian.net/browse/KAN-71)
**Status:** the format is a **proposal for Group 6** (Nathan) to agree. The 20 queries run against the mock sessions in `fixtures/data`. The 100+ queries across real sessions and documents (KAN-70) need the real indexed data.

## Query format (one JSON object per line)

```json
{"id": "q001", "kind": "how_did_i_fix", "query": "how did I fix the nginx config test failure?",
 "expected": ["mock-nginx-port#f10"], "also_relevant": ["mock-nginx-port#c12"]}
```

| Field | Meaning |
|---|---|
| `kind` | `how_did_i_fix`, `find_command`, `file_history`, `explain_error`, `document` |
| `expected` | the record ids a good search **must** return: evidence ids `session#event` for sessions, `doc:<path>#<chunk>` for the document index |
| `also_relevant` | ids that are fine to return (they count for MRR, not for recall) |

## Metrics

* **recall@k** (k = 1, 3, 5, 10): the share of `expected` ids in the top k, averaged over queries.
* **MRR:** 1 / rank of the first `expected` or `also_relevant` hit, averaged.
* Results are broken down by `kind`.

## Running it

```bash
python -m retrieval_eval                                   # BM25 baseline over fixtures/data
python -m retrieval_eval --searcher mymodule:search_events --min-mrr 0.8   # a real searcher; exit 1 below the bar (CI, KAN-72)
```

A searcher is any `search(query, k) -> list[record_id]`.

**Baseline (BM25 over command, output, path, diff, goal and summary):** MRR 0.89 and recall@10 0.95 on the 20 queries. `file_history` (MRR 0.61) and `how_did_i_fix` (0.80) are the weak kinds. That's expected, because the editor command and its edit compete, and "fix" questions rarely share words with the fix itself. A real search has to beat this.
