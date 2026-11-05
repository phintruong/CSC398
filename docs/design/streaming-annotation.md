# Design: streaming annotation and the runtime interface (1.1.4, KAN-104)

Jira: epic [KAN-6](https://alexsworkspace-43428909.atlassian.net/browse/KAN-6) · design + acceptance test [KAN-41](https://alexsworkspace-43428909.atlassian.net/browse/KAN-41) · annotate on close [KAN-42](https://alexsworkspace-43428909.atlassian.net/browse/KAN-42) · live goal tree [KAN-43](https://alexsworkspace-43428909.atlassian.net/browse/KAN-43) · runtime interface [KAN-104](https://alexsworkspace-43428909.atlassian.net/browse/KAN-104)
Code: [`streaming/`](../../streaming), [`runtime/`](../../runtime) · Tests: [`tests/test_streaming.py`](../../tests/test_streaming.py)
Status: **draft for team sign-off**. The code runs on recorded replies. Plugging in the real Model 1 and measuring quality against batch annotation is KAN-44.

## 1. Runtime interface (KAN-104)

Every model call in the pipeline uses one method:

```python
model.complete(task: str, evidence_ids: list[str], prompt: str) -> str
```

| Argument | Meaning |
|---|---|
| `task` | `annotate`, `summarize`, `answer`, … |
| `evidence_ids` | the events the call is about (`session#event`). Used for replay or caching, logs and citations. |
| `prompt` | the exact text sent, **already scrubbed** |

Two wrappers every caller uses:

* **`Guarded(model)`** runs the scrubber over the prompt. If anything would be replaced, the call raises `UnscrubbedPrompt` instead of sending it, and the error names only the kinds found. This is defence in depth for the "prompts" part of the privacy test (KAN-36).
* **`Logged(model)`** logs the task, evidence ids, character counts, success and latency. It **never logs prompt or reply text**.

Backends: `fixtures.ReplayModel` in tests. Later: the quantized Model 1 behind FastAPI (KAN-50) and the local/cloud router (KAN-9), which implements the same method.

## 2. Streaming annotation

```python
sa = StreamingAnnotator(Guarded(model), header)
for ev in events_as_they_close:          # from live capture (or an imported session)
    for ann in sa.feed(ev): emit(ann)    # annotation events (schema v1), parent = live goal-tree node
sa.flush()                               # at each precmd / at the end
```

* **Past context only.** The prompt holds the last 8 events already seen, with their labels, plus the event being annotated. The annotator never sees events that haven't arrived. The input format is the same as the training records (`trainrec.compact`), so training and serving match.
* **When an event closes:** in live capture, a command and the file edits it caused all arrive at the same precmd. A command is held until the next event. If that is one of its own edits, the edit is annotated (the diff says more than `vim app.py`) and the command becomes context. `flush()` releases a held command.
* **Live goal tree:** the first annotation becomes a root. Each later annotation goes under the current root, unless the model says `new_goal: true` (start a new root) or the goal matches an earlier root (back to an old task, so that root becomes current again).
* **Failures don't stop the stream.** With no reply, nothing is emitted and `failures` is counted. An unparseable reply becomes `result: unknown` with null fields.
* **Unscrubbed sessions are refused.**

## 3. Acceptance test

| # | Check |
|---|---|
| S1 | **No future access:** a spy model fails if a prompt contains text that appears only in later events. Run over every fixture session. |
| S2 | Streaming the fixtures with recorded replies gives the same labels and the **same goal tree** as the batch-annotated fixture. |
| S3 | Edits are annotated instead of the editor command that made them. A plain command is annotated after `flush()`. |
| S4 | New-goal handling: `new_goal` starts a root, and returning to an earlier goal reattaches to that root. |
| S5 | A garbage reply gives `unknown`, a replay miss is skipped and counted, and the stream continues. |
| S6 | `Guarded` refuses a prompt with a planted secret, with no value in the error. `Logged` stores no prompt or reply text. |
| S7 | Output annotations validate against schema v1 together with the session's events. |
| S8 (KAN-44, needs Model 1) | Streaming tag F1 is within the agreed margin of batch annotation on the held-out set. **Not done:** blocked on the real Model 1. |

## 4. Not covered yet

* **Live wiring (KAN-27):** capture scrubs at `stop` with one placeholder map, so events can't be streamed out mid-session yet without a long-running capture process that holds the map in memory. This is the same open point as in [capture.md](capture.md).
* Re-annotating earlier events when later context changes their meaning. That is a batch pass, by design.
