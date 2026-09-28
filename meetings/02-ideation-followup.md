# Meeting 2: Week 2 ideation follow-up

**Context:** The team was still choosing a direction and considering a pivot closer to the original course project description. The team asked to see the old code.

---

## 1. Resources shared
- **GitHub repos** for the current project (at least two). Arthur posted them in chat.
- **Winter 2026 repo:** its README has a **diagram of all the models** (0, 1, 2). Read this first.
- **Jack's API definitions:** an older API for storing and retrieving model output and **arguing with it**, i.e. giving feedback like "good, but this example would have been better" that goes back into the training data. It was built before the annotator and **never integrated**, so there are now two separate lines of work.

## 2. Pipeline design, clarified
- The design goal was to make each model's job **as simple as possible**, so that weaker or cheaper models (open models, or ones trained from scratch on their data instead of fine-tuned) could replace them later.
- **Model 0's real job:** keep repeating a count of lines seen, and reset to 0 when it decides the event is complete. The output is just numbers.
- The **Dockerfile Model 2** came from a student team, not from the mentors.

## 3. The bigger mission
The mentors are from **Copyleft AI** (a "federation," previously a "foundation").

- **Problem: data provenance and licensing.** Free-software developers need to know the license of everything that goes into their code. Mainstream LLMs were trained on everything online with no regard for licenses. As a result:
  - Copyright on AI-generated code is unclear, and may belong to no one.
  - Pasting LLM code into a free-software project may "steal" someone's code, possibly your own.
  - So free-software developers effectively **can't use current AI**.
- **Their approach, step by step:**
  1. **Compute:** cheap early accelerators ($30 each, about 1/3 the performance of an RTX 4090).
  2. **Training data:** Julia records all her work with asciinema.
  3. **Labeling sessions:** Julia explains a recording to someone while both audio streams are recorded. That person then labels the recording. **This labeled data is the class dataset.**
  4. This covers each problem from **4 angles**: (1) the original recording, (2) Julia's explanation, (3) the listener's questions and answers, (4) the labels.
- **Goal:** AI as a **teaching assistant**, helping people get better at technology instead of doing the work for them.

## 4. Directions the mentors suggested
1. **Benchmark** (Arthur): "We sort of should have had a benchmark from the start."
2. **Q&A model in the interface** (Julia's new idea): the annotated event tree is excellent context. When a learner reaches a line they don't understand ("what does this command or option mean?"), they can ask a model that has the **whole annotated session** as context and gets an authoritative, context-aware answer.
3. **Revive or integrate a Model 2** (documentation or Dockerfile). Neither is integrated, so there is no final output.
4. **Better Model 1.** Annotations are "good but not great."
5. **Dataset consistency.** Many different people labeled the data, and each interpreted the annotation standard a bit differently.

## 5. Known flaws (architecture-level)
- **UI (lower priority, "you're here for AI work"):** it shows only the graph, not the terminal, so users can't follow along. A quick win: show the original terminal playback with the current event highlighted.
- **Terminal size is dropped.** Every asciinema file records the window size, but it isn't passed to the models. Models can't tell where the bottom of the screen is (status lines, line numbers), so full-screen sessions like editing code in vim **can't be used as training data** because they would confuse the model. Someone needs to redesign how this data is represented and passed through the system. **The mentors say this one is fixable.**
- **Multiple meanings in one event.** Output from a remote machine doesn't stop at logical boundaries. One chunk can hold a command's output *and* the next prompt. Model 0 can only say an event belongs to the previous or next event, not that it contains several. **Currently unfixable** with the existing design.

## 6. Policy answers
- **Using AI to write code:** fully allowed ("carte blanche"). Use whatever tools you know best and cite them as the syllabus requires. The mentors expect to reimplement things themselves later.
- **What they value:** the **architecture**, not the code ("the code is ephemeral").
- **Changing earlier models (e.g. redesigning Model 0 or rewriting prompts):** fine, as long as the whole system works at the end of the semester.
- **Pivoting partly toward the original project description:** fine, as long as the result is **better** than what existed at the start of the semester. Improving one part while leaving another worse still counts as net better.
- **Stay on the same data source** (asciinema files plus audio, Q&A and annotations). The audio has **not been transcribed** (e.g. with Whisper) yet.
- **Learning vs. broad usefulness:** Julia leans clearly toward **helping people learn**. Big AI companies already work on making AI do everything; few are working on making humans better.
  - Personal context: a C3 neck injury about 20 years ago means Julia can't type for long. Julia switched from C to Haskell to "do more by doing less." With AI, we've learned to do more by doing more, but not to do more by doing less.

## 7. Team next steps (after the mentors left)
- [ ] Put the transcripts and documentation into an AI tool to generate ideas.
- [ ] **Each member presents their best idea by Sunday or Monday.**
- [ ] Choose a direction and write a **spec sheet**.
- [ ] Present the spec to the mentors **next Thursday**.
- [ ] Use lecture time to work on it.
