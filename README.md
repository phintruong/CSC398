# CSC398: Copyleft AI / asciinema Learning Project

## Meeting notes
| # | Notes | Summary |
|---|---|---|
| 1 | [Kickoff](meetings/01-kickoff.md) | Introductions, project overview, pipeline, mentors' wish list |
| 2 | [Ideation follow-up](meetings/02-ideation-followup.md) | Repos, mission, suggested directions, known flaws, policy answers |

## Project summary
- **Mentors:** Julia and Arthur, from Copyleft AI (free-software developers).
- **Goal:** use AI to help people **learn** from recorded terminal work sessions, acting like a teaching assistant rather than an AI that does the work.
- **Data:** asciinema `.cast` terminal recordings (text with keystroke and output timestamps). Some also have audio of Julia explaining the session, the listener's questions and answers, and human labels.
- **Pipeline:** Model 0 (split the session into events) → Model 1 (annotate events into a tree) → Model 2 (documentation or Dockerfile; **not integrated**).

## Candidate directions
1. **Benchmark / evaluation** for the pipeline. This is the most-requested item.
2. **Q&A assistant** that uses the annotated session tree as context.
3. **Integrate a Model 2** (documentation or Dockerfile generation).
4. **Better annotation quality**, by retraining Model 1 or redesigning Model 0.
5. **Dataset work:** clean up and standardize labels, auto-annotate with a frontier model, transcribe the audio with Whisper, add terminal size.
6. **Feedback loop** for correcting model output and feeding it back into training (see Jack's old API).
7. **Fix event representation:** pass terminal size to the models, and handle chunks that contain several events.

## Mentor ground rules
- Any tools, models or compute are allowed. AI-written code is fine; cite it.
- They care about **architecture** more than code.
- Pivots are OK as long as the result is **better** at the end of the semester and uses the **same data source**.
- The priority is **learning** over mass usefulness.

## Timeline
- Individual idea pitches: **Sunday/Monday**
- Choose a direction and write the spec sheet, then present to the mentors: **next Thursday**

## What's in this repo

Everything reads and writes **event schema v1** ([contracts/events.py](contracts/events.py)): one `*.events.jsonl` per session. Line 1 is the `session` header, followed by `command`, `resize`, `file_edit`, `annotation` and `correction` events. Outputs cite events by evidence id, `session#event`. The only dependency is pytest, for the tests.

| Ticket(s) | What | Code | Docs |
|---|---|---|---|
| KAN-102 | Event schema v1, validator, evidence ids | [contracts/](contracts) | [contracts/events.py](contracts/events.py) |
| KAN-33, KAN-34 | Scrubber: secrets, IPs, hosts, users, paths become consistent placeholders | [scrubber/](scrubber) | [scrubbing.md](docs/design/scrubbing.md) |
| KAN-29, KAN-30 | File-edit detection: `file_edit` events with unified diffs, config flag | [fileedit/](fileedit) | [file-edit-detection.md](docs/design/file-edit-detection.md) |
| KAN-26, KAN-35 | Live capture (bash/zsh hooks) and `.cast` importer, scrubbed at capture with one placeholder map | [capture/](capture) | [capture.md](docs/design/capture.md) |
| KAN-36 | Privacy test: planted secrets never reach storage, logs or prompts | [tests/test_privacy_pipeline.py](tests/test_privacy_pipeline.py) | [scrubbing.md §5](docs/design/scrubbing.md) |
| KAN-106 | Mock sessions + recorded model replies (`ReplayModel`) | [fixtures/](fixtures) | [fixtures/README.md](fixtures/README.md) |
| KAN-108 | Contract tests (incl. broken recordings, fuzzing) | [tests/test_contracts.py](tests/test_contracts.py) | |
| KAN-104 | Runtime interface for every model call, plus prompt guard and text-free logging | [runtime/](runtime) | [streaming-annotation.md §1](docs/design/streaming-annotation.md) |
| KAN-41, KAN-42, KAN-43 | Streaming annotation from past context only, live goal tree | [streaming/](streaming) | [streaming-annotation.md](docs/design/streaming-annotation.md) |
| KAN-77, KAN-78 | Incident post-mortems (timeline, root cause, fix, lessons; all cited) | [postmortem/](postmortem) | [postmortem.md](docs/design/postmortem.md) |
| KAN-103, KAN-81, KAN-82 | Correction format; one training-record format; SFT + preference export | [trainrec/](trainrec) | [training-records.md](docs/design/training-records.md) |
| KAN-46 | Embed + align repeated runs, canonical procedures | [procedures/](procedures) | [procedures.md](docs/design/procedures.md) |
| KAN-62 | Benchmark harness + description rubric (CI exit code) | [bench/](bench) | [benchmark.md](docs/design/benchmark.md) |
| KAN-69, KAN-71 | Retrieval eval format, first 20 queries, recall@k/MRR, BM25 baseline | [retrieval_eval/](retrieval_eval) | [retrieval_eval/README.md](retrieval_eval/README.md) |
| KAN-111, KAN-115 | Local dashboard: Sessions home, Privacy & cloud settings | [dashboard/](dashboard) | |

```bash
pip install pytest && pytest                      # ~300 tests
python -m capture import old.cast -o old.events.jsonl --source S --consent C --license L
eval "$(python -m capture start --dir runs/s1 --source S --consent C --license L)"   # ... work ...
python -m capture stop --dir runs/s1
python -m postmortem fixtures/data/mock-nginx-port.events.jsonl
python -m procedures merge fixtures/data/mock-nginx-port*.events.jsonl
python -m trainrec export fixtures/data -o records.jsonl
python -m bench baseline --gold fixtures/data --out runs/pred && python -m bench run --gold fixtures/data --pred runs/pred
python -m retrieval_eval
python -m dashboard --sessions fixtures/data      # http://127.0.0.1:8398
```

**Still blocked on things outside this repo:** the real Model 0/1 (baselines KAN-61, streaming quality KAN-44), the mentors' labelled test set and recordings (KAN-28, KAN-70), and the search index from Group 6 (KAN-71 against real search). Live streaming into tagging (KAN-27) needs a long-running capture process; see [capture.md](docs/design/capture.md).
