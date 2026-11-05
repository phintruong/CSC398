# Automation scripts and Dockerfiles from recorded sessions

Jira: canonical procedures feed generation [KAN-47](https://alexsworkspace-43428909.atlassian.net/browse/KAN-47) · automation scripts from `file_edit` + procedures [KAN-75](https://alexsworkspace-43428909.atlassian.net/browse/KAN-75)
Code: [`generate/`](../../generate) · Tests: [`tests/test_generate.py`](../../tests/test_generate.py)
Related, not done here: recovering the old Model 2 / Dockerizer (KAN-73), session → Dockerfile with that model on approved data (KAN-74), and the full sandbox build suite (KAN-76).

## What it does

```bash
python -m generate script     fixtures/data/mock-nginx-port*.events.jsonl -o fix-nginx.sh
python -m generate dockerfile fixtures/data/mock-python-venv.events.jsonl --base python:3.12-slim
```

1. The sessions are merged into a canonical procedure ([procedures.md](procedures.md)). Failed steps and diagnostics are already gone.
2. The procedure is rendered with **deterministic templates, no model**. Every line comes from a step that succeeded in a recording, and every step is commented with its evidence ids.

## Safety rules for generated code

| Rule | How |
|---|---|
| Stop on the first error | `set -euo pipefail` (script) · `SHELL ["/bin/bash", "-euo", "pipefail", "-c"]` (Dockerfile) |
| See before you run | `DRY_RUN=1 bash script.sh` prints every step, including patches and `cd`, and changes nothing |
| Never guess scrubbed values | `<HOST_1>` becomes `${HOST_1}`. The script refuses to start until it is set, and the Dockerfile declares an `ARG`. |
| Interactive programs aren't automated | `vim`, `less`, `htop`, `ssh`, … become `# interactive, not automated: …` |
| Edits are patches, not overwrites | `patch --forward` with the recorded unified diff. It **fails** if the file has drifted from what was recorded, instead of clobbering it. `$` and backticks in the file stay literal. |
| Steps share state like a terminal | Script steps run with `eval` in one shell, so `source .venv/bin/activate`, `export` and `cd` carry forward. In a Dockerfile, venv activation becomes `ENV VIRTUAL_ENV/PATH` and `export` becomes `ENV`. |
| Containers aren't machines | `systemctl`, `service`, `docker`, … are commented out in Dockerfiles. Checks against `localhost` are left as comments. `sudo` is dropped. |
| Refuse unscrubbed input | The CLI exits 1 for a session with no `redaction` block. |

## Tests

* Scripts from every fixture pass `bash -n`.
* **A real run in a sandbox directory:** a generated script patches a config file (with `$5` and backticks in it), exports a variable, `cd`s into a new directory and writes a file. All of these effects are checked.
* `DRY_RUN=1` changes nothing. A missing variable stops the script with a clear message. A drifted file makes `patch` fail.
* Dockerfile rules: venv → `ENV`, no `RUN source`, no docker-in-docker, systemctl commented out, `ARG` for placeholders.

**Not done:** building the generated Dockerfiles in a sandbox (KAN-76). That needs base images from the network and a Docker daemon in CI.
