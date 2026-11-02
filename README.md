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

## Capture & privacy work (Group 1)
| Ticket | What | Where |
|---|---|---|
| KAN-33 | Scrubbing design + planted-secret acceptance test | [docs/design/scrubbing.md](docs/design/scrubbing.md), [tests/planted_set.py](tests/planted_set.py) |
| KAN-34 | Scrubber: secrets, IPs, hosts, users, paths → consistent placeholders | [scrubber/](scrubber) |
| KAN-29 | File-edit detection design + acceptance test | [docs/design/file-edit-detection.md](docs/design/file-edit-detection.md), [tests/test_file_edit_acceptance.py](tests/test_file_edit_acceptance.py) |

```bash
pip install pytest && pytest                     # unit + acceptance tests (planted-secret recall: 52/52)
python -m scrubber in.cast -o out.cast --source S --consent C --license L [--term CustomerName]
```
