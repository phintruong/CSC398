# Meeting 1: Kickoff with mentors

**Attendees:** Julia and Arthur (mentors), Jack, Nathan, Rachelle, Alex, plus one more team member
**Note:** The source transcript is cut off near the end ("8 KB left"), so the last part of the meeting is missing.

---

## 1. Introductions

### Mentors
- **Julia** leads a small team of free-software developers. The team's goal is to make sure free software survives the shift to AI-written software.
  - Day job: runs an operations engineering team at **Wire**, an end-to-end encrypted workplace chat product ("Slack's UI with Signal's security"). The product is fully open source.
  - Background: maintains **ImplicitCAD** (a 3D CAD tool she took over from its original author), invented a way to print aluminum using microwaves, and spent about 15 years in distributed systems (Linux PMI, a process migration tool, at the University of Arkansas).
  - Related projects: their own LLM engine, a llama.cpp port to Xeon Phi, and FPGAs.
- **Arthur** created **Smoothie**, an open-source controller board for 3D printers, lasers and CNC machines. He has about 20 years of programming and AI consulting and has focused on transformers for the last 3–4 years.
- Julia and Arthur also mentor a second student group working on 3D-printing and CAD reasoning datasets.
- Both mentors are in Europe, where the meeting time is about 1 AM. They would prefer an earlier slot if one opens up.

### Team
| Name | Background |
|---|---|
| Jack Le | 3rd year CS. Has gone to 22+ hackathons. Works on applied AI, RAG and agent systems; had an internship optimizing RAG pipeline cost and speed. Prefers high-level work. Wants to build one bigger, longer-lasting project. |
| Nathan | 3rd year CS specialist. Does full stack work and did RL research at Algoverse (multi-agent RL where each limb is its own agent vs. single-agent PPO). Owns a Bambu P1S 3D printer. |
| Rachelle | 3rd year CS specialist. Has built AI projects at hackathons; was in a research group building an NLP pragmatic-reasoning dataset. Plays badminton. |
| Alex | 3rd year CS. Likes trying new technologies and building for fun. |
| (one member) | 3rd year CS with an industry focus: interned at KPMG ("PPMG" in the transcript) and is now interning at an ERP developer for small businesses. Has not built a RAG system yet. Goes to hackathons with Jack. |
| (one member) | CS + Math double major at U of T. Worked on a RAG-adjacent startup. |

---

## 2. What the project is

**Big picture:** They are building an AI tool that helps people **understand and learn from** what someone did, not one that does the task for them. Big AI labs have far more money and people, so the mentors chose a deliberately different direction.

**Motivation (Julia's day job):** Wire deployments happen in each customer's own environment: their servers, networks and procedures. Each deployment includes Kubernetes, Postgres, MinIO, Cassandra, Elasticsearch, Redis and more. They need an accurate log of every step and a quick way to find what went wrong.

**Input data: asciinema recordings (`.cast` files)**
- asciinema is a text-based terminal recorder. It is **not video**.
- It records keystrokes (input) separately from what appears on screen (output), with timestamps for both. That makes it an essentially exact record of the session.
- Julia has recorded **all of her work for about 2.5 years**, including routine system updates.
- The timing data matters, because a sysadmin reads meaning into when output pauses, speeds up or stops.
- Text was chosen over video on purpose: no compute wasted encoding and decoding images, and a model learns from the same text a human engineer learned from.

**Pipeline (a web app that runs a chain of models):**
- **Model 0 (segmentation):** decides where each event starts and ends, then passes the events on.
- **Model 1 (annotation):** labels each event ("what was happening here?") using earlier events as context. The output is a **tree of events**, e.g. "navigate to line 201", "run the Go app".
- **Model 2 (output):** built in earlier semesters but **never integrated**. Past versions produced one of:
  - documentation a coworker can use to reproduce the work, or
  - a **Dockerfile** that rebuilds the final system state.

**Models and hardware**
- Models 0 and 1 are fine-tuned **Qwen** models: 8B was tried first, then 7B. They swap to a newer base model each semester.
- Lesson from a past team: a *thinking* model must be fine-tuned on reasoning traces too. Training only on input/output pairs produces garbage.
- The demo runs on CPU only. It takes about 1 minute per chunk, and a single file can have about 500 chunks.
- Their own hardware: **Xeon Phi** cards (Intel's 2013 supercomputing co-processors, bought for about $30 each, 16 GB each). They ported llama.cpp to these cards and hand-optimized it in early AVX-512 assembly.

**Intended use:** a coworker gets a summary instead of watching the whole screen recording, clicks the interesting part, and sees what was being done and why. Knowledge stays with the team, not just with one person.

---

## 3. Q&A highlights

- **Scope vs. the course project description:** the course description is about 2 years old. Every team has built on this asciinema direction, but the mentors don't require a specific direction. "Please make it better, and you get to decide what better is."
- **Tools:** any models, languages or compute are fine, including Colab or paid APIs. The mentors will adapt the result to their own infrastructure later.
- **New build or reuse?** Either. It's the team's choice.

## 4. Mentors' wish list (known gaps)
1. **Better annotation quality.** It's currently "fair."
2. **Model 2 integrated** into the system (documentation or Dockerfile output).
3. **Retrain or improve Models 0 and 1** for efficiency and precision.
4. **Clean up the dataset.** The labeled data is messy and too small.
5. **Use a frontier model to auto-annotate** more recordings and grow the dataset.
6. **A benchmark.** Arthur's top request: "We have no idea how well the stuff performs. We just eyeball it."
7. A way to **argue with or correct** model output and feed the corrections back into the training data.

**Priority answer (partial, the transcript ends here):** first annotation quality, then the missing Model 2. The latest team got the UI working end to end, but the Docker and documentation versions were never combined with it.
