# The "why?" interviewer (2.1.28)

Jira: epic [KAN-10](https://alexsworkspace-43428909.atlassian.net/browse/KAN-10) · questions on fixtures [KAN-58](https://alexsworkspace-43428909.atlassian.net/browse/KAN-58)
Code: [`interview/`](../../interview) · Tests: [`tests/test_interview.py`](../../tests/test_interview.py)
Not done here: consented clips (KAN-57), Whisper transcription (KAN-59).

## Why

A recording shows *what* someone typed. A learner gets most from *why*: what they expected when it broke, and how they worked out the fix. Only the person who did the work can say that, so we ask them, briefly, about a few moments.

## Which moments

For each incident chain (the same chains as the post-mortems):

| Kind | Step | Question template |
|---|---|---|
| mistake | the root cause (a failed command or a mistaken edit) | "When you ran `X`, what did you expect to happen, and why do you think it failed?" / "When you made the edit to `f`, what were you trying to change, and what did you miss?" |
| discovery | the first diagnostic after the failure (`ls`, `git status`, …) | "After `X` failed, you ran `Y`. What were you looking for?" |
| discovery | the first state-changing fix | "How did you figure out that `Z` was the fix?" |

The interviewer asks at most 5 questions per session (`--max`), most important first. Moments that already have an answer aren't asked again. A session with no failures gets no questions; asking about every step would be noise.

An optional model can rephrase a question through the runtime interface. The rephrasing is kept only if it is still a single question of 30 words or fewer, otherwise the template is used.

## Answers

```bash
python -m interview questions session.events.jsonl
python -m interview answer session.events.jsonl --target f5 --question "..." --text "..." --author phuc
```

An answer is an `explanation` event (schema v1): `target`, `question`, `answer`, `source` (`typed` now, `voice` for Whisper later, with `confidence`), `author` (a handle) and `t_created`.

* **Scrubbed before it's stored.** The numbering continues after the recording's existing placeholders (`PlaceholderMap.continuing`), so a new host becomes `<HOST_3>`, not a second meaning of `<HOST_1>`.
* **Post-mortems quote it** under "In the author's words", cited (`[session#e1]`). It passes the same citation guard as everything else.

## Next (KAN-59)

Record the answer as audio, transcribe it locally with Whisper, then call `interview.answer(..., source="voice", confidence=…)`. Everything downstream already handles it.
