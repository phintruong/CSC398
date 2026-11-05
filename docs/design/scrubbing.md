# Design: scrubbing at capture time (1.2.1)

Jira: epic [KAN-4](https://alexsworkspace-43428909.atlassian.net/browse/KAN-4) · design and acceptance test [KAN-33](https://alexsworkspace-43428909.atlassian.net/browse/KAN-33) · implementation [KAN-34](https://alexsworkspace-43428909.atlassian.net/browse/KAN-34)
Status: **draft for team sign-off** (section 7 lists the decisions we need to agree on)
Code: [`scrubber/`](../../scrubber) · Tests: [`tests/test_planted_secrets.py`](../../tests/test_planted_secrets.py), [`tests/test_scrubber.py`](../../tests/test_scrubber.py)

## 1. Goal

Remove secrets and personal identifiers from a recording **before anything is stored, indexed, logged or sent to a model**. Replacements must keep the structure the models need: the same secret always becomes the same placeholder, so "set the password, then use it to log in" still reads as one story.

The scrubber runs in two places:

1. **Capture step.** It scrubs each `.cast` file as soon as recording stops (`python -m scrubber in.cast -o out.cast`, or `scrub_recording()` from the 1.1.9 capture code). Only the scrubbed file is kept.
2. **Diff step.** The file-edit diffs from #50 go through `Scrubber.scrub_text()`, using the same placeholder map as their recording (see [file-edit-detection.md](file-edit-detection.md)).

Until this is in the capture path, we only use synthetic or already-sanitized recordings.

## 2. What gets scrubbed

| Category | How it is detected | Placeholder | Example |
|---|---|---|---|
| **Typed passwords** | The output ends in a password-style prompt (`password:`, `passphrase for key`, `[sudo] password for`, `PIN:`, `token:`…), so the next line of *input* up to Enter is secret. Backspaces are applied. | `<PASSWORD_n>` | `[sudo] password for <USER_1>: ` + `<PASSWORD_1>` |
| **Typed usernames** | Same as above, after a `Username…:` / `login:` prompt. | `<USER_n>` | git credential prompt |
| **Tokens and API keys** | Known formats: AWS key IDs, GitHub (`ghp_`, `github_pat_`), GitLab, Slack, Google, Stripe, OpenAI/Anthropic-style `sk-…`, npm, Hugging Face, JWTs, `Authorization: Bearer/Basic`. | `<TOKEN_n>` | `export GITHUB_TOKEN=<TOKEN_1>` |
| **Secrets in assignments and flags** | Any `key=value` / `key: value` / `--flag value` where the key contains password, passwd, pwd, secret, token, api_key, access_key, private_key, client_secret, credential. Also `mysql -pVALUE`. Values like `$VAR`, `true` or `***` are skipped. | `<PASSWORD_n>` if the key says pass/pwd, otherwise `<SECRET_n>` | `"access_token": "<SECRET_1>"` |
| **Private keys** | The body of `-----BEGIN … PRIVATE KEY-----` blocks. The BEGIN/END lines are kept so the reader knows what it was. | `<PRIVATE_KEY_n>` | |
| **Connection strings** | `scheme://user:password@host` with the user, password and host each replaced. | `<USER_n>`, `<PASSWORD_n>`, `<HOST_n>`/`<IP_n>` | `postgres://<USER_1>:<PASSWORD_1>@<HOST_1>:5432/orders` |
| **High-randomness strings** | Tokens of 20+ characters that mix upper case, lower case and digits, with Shannon entropy ≥ 3.9 bits/char (≥ 4.2 when 32+ chars long). This catches secrets in formats we don't recognise. | `<SECRET_n>` | |
| **IP addresses** | IPv4 and IPv6, validated with Python's `ipaddress`. | `<IP_n>` | `ping <IP_1>` |
| **Hostnames** | FQDNs ending in a common or internal TLD (`.com .io .net … .internal .lan .corp .local .svc`), plus the host in `user@host` (shell prompts, ssh, e-mail). | `<HOST_n>` | `ssh <USER_1>@<HOST_1>` |
| **Usernames** | `user@host`, the user part of e-mail addresses and connection strings, and home directories (`/home/x`, `/Users/x`, `C:\Users\x`). | `<USER_n>` | `/home/<USER_1>/clients/…` |
| **Paths** | Paths are kept, because they are what a learner needs to see (`/etc/nginx/sites-available/default`). The personal parts inside them (the username, and customer or project names) are scrubbed. | as above | |
| **Extra terms** | Names passed with `--term` (customer names, project code names) are replaced wherever they appear, ignoring case. | `<NAME_n>` | `/clients/<NAME_1>` |

**Learn once, replace everywhere.** Once a value is found anywhere in a recording (for example a password at a prompt, or a username in the prompt), every other literal occurrence of it is replaced too. That covers the output and input streams, the title and the markers. It catches `echo $PASSWORD` output, the window title escape code, and usernames echoed back by the terminal. Values shorter than 3 characters are not spread this way.

### Deliberately kept (and why)

| Kept | Reason |
|---|---|
| `127.0.0.0/8`, `::1`, `0.0.0.0`, `::`, netmasks like `255.255.255.0` | Generic. A learner needs them to understand networking commands. |
| Public infrastructure domains (`github.com`, `ubuntu.com`, `docker.io`, `pypi.org`, `example.com`, … full list in `detect.py`) | They say what tool was used and identify nobody. |
| Generic system users (`root`, `git`, `ubuntu`, `postgres`, `www-data`, …) | Same reason. |
| Pure hex strings (git SHAs, image digests, container IDs, UUIDs) | Very common in sessions and needed to follow them. Hex secrets usually sit behind a keyword (`token=…`), and the keyword rule catches those. |
| File paths with no personal parts | They are what the session teaches. |

## 3. Placeholders and consistency

* The format is `<KIND_n>`, numbered per kind in order of first appearance: `<PASSWORD_1>`, `<IP_2>`.
* The same original value gets the same placeholder everywhere in the recording (output, input, title, markers, and later its diffs).
* The value→placeholder map exists **only in memory** while one recording is processed. It is never written, logged or returned by the CLI.
* Numbering is **per recording**. The same IP in two recordings may get different numbers (see open question 7.3).
* A secret split across events (keystrokes arrive one per event; output chunks break anywhere) is detected on the joined stream. The placeholder goes into the event where the secret started, and the rest is removed from the following events. **Event count and timestamps never change**, so playback timing, which the mentors care about, is preserved.

## 4. Metadata written into each recording

The scrubbed `.cast` header gets:

```json
"provenance": {"source": "julia-laptop", "consent": "recorded-by-owner", "license": "CC-BY-SA-4.0"},
"redaction": {"version": "scrub-0.2.0", "replaced": {"IP": 3, "PASSWORD": 1, "USER": 2}}
```

`replaced` holds counts of distinct values only. A recording with no `redaction` block has **not** been scrubbed and must not be stored or used. Bump `REDACTION_VERSION` whenever detection changes, so we know which recordings to re-scrub.

## 5. Acceptance test: planted-secret test set

[`tests/planted_set.py`](../../tests/planted_set.py) builds **10 synthetic recordings** that look like real sessions: coloured prompts with window titles, keystroke-by-keystroke input with echo, no-echo password prompts, and output split across events. **52 fake values** (secrets and identifiers) are planted in them. One recording, `hard_negatives`, contains only things that must **not** be scrubbed. The secrets are random values generated at test time from a fixed seed, so nothing secret-shaped is committed to the repo.

| Recording | What is planted |
|---|---|
| `sudo_password` | sudo password (no echo), prompt user/host |
| `ssh_and_mysql` | ssh password, mysql prompt password, inline `mysqldump -p…`, remote user/host, IP |
| `exported_tokens` | GitHub, AWS key ID + secret, OpenAI-style key; typed, echoed, then printed split over 4–7 events |
| `connection_strings` | Postgres and Redis URLs typed and inside a `.env` |
| `private_key` | OpenSSH private key printed with `cat`, split across events |
| `http_auth` | Bearer JWT in curl, `access_token` in JSON, e-mail address, internal API host |
| `network` | IPv4, IPv6 link-local, broadcast, internal k8s node hostname |
| `git_https_prompts` | git credential username (echoed) and password (hidden) |
| `home_paths_and_terms` | usernames in Linux, macOS and Windows home paths; a customer name passed as an extra term |
| `hard_negatives` | git SHAs, sha256 digest, UUID, kernel and package versions, pod names, `0.0.0.0:80`, `localhost`, GitHub URL, `PasswordAuthentication no`, `DB_PASSWORD=$(…)`, `/etc/passwd` lines |

**Pass criteria** (`pytest`, all must hold for every recording):

| | Criterion |
|---|---|
| A1 | No planted value appears anywhere in the output: events, header or markers, raw or with ANSI codes stripped. This covers "never shows up in storage, prompts, logs or search", since everything downstream reads from this file. |
| A2 | No 8-character fragment of a planted secret survives (no partial leaks). |
| A3 | Each planted value maps to one placeholder of the expected kind, and that placeholder appears at least as many times as the value did. |
| A4 | Every "keep" string is still there (no over-scrubbing of what learners need). |
| A5 | The written metadata contains no original values, and provenance is recorded. |
| A6 | Event count, codes and timestamps are unchanged. |
| A7 | `hard_negatives` comes out byte-for-byte unchanged, with zero replacements. |

**Current result:** all pass. Recall is 52/52 (100%) with 0 false positives on the hard-negative set. The same set was also regenerated with 300 different seeds (3,000 recordings): 0 leaks and 0 over-scrubbed strings.

```
pip install pytest
pytest                      # unit and acceptance tests
python tests/planted_set.py /tmp/planted   # write the set to disk to inspect it or test another tool
```

When we find a new kind of leak, we add it to the planted set first and then fix the detector.

## 6. Known limitations

1. **Input is only scrubbed if it was recorded.** asciinema records keystrokes only with `--stdin` (v2) or input capture on (v3). Without it, passwords typed with echo off never reach the file, so there is nothing to scrub. Live capture (1.1.9) should record input, because typed passwords are a top risk.
2. **Full-screen programs** (vim, htop, less) redraw the screen with cursor movement, so a secret visible on screen may never sit as one continuous string in the stream. The entropy and keyword rules miss it. This matters less while vim sessions are excluded from training (meeting 2), but it needs a terminal-emulator pass (render the screen, then scan) before those sessions are used.
3. **Secrets with no structure.** A plain password passed as an argument with no keyword and no prompt (`./deploy summer2024`) is not detected. Typing it at a prompt is caught.
4. **Hostnames with uncommon TLDs**, and single-label hostnames that appear only outside `user@host`, are not detected unless they were already learned from a prompt.
5. **Four-part version numbers** (`4.15.0.142`) are scrubbed as IPs. This is a safe-side false positive.
6. **Base64 data printed in output** (certificates, `kubectl get secret -o yaml`) is replaced as `<SECRET_n>`. That is correct for secrets but also hides public certificates.

## 7. Decisions for the team to agree (KAN-33 "done when: agreed")

1. **Private IP ranges** (10/8, 172.16/12, 192.168/16): these are scrubbed now. They reveal network layout, but they also teach subnetting. Proposal: keep scrubbing them, since structure such as `/24` is kept.
2. **Public-domain allowlist**: is the list in `detect.py` right? Should the mentors' own domains (wire.com, copyleft.ai) be kept?
3. **Consistency across recordings**: right now it is per recording. If the Q&A and benchmark work needs "the same host across sessions", we switch to a keyed HMAC (`<HOST_7f3a>`), with the key held only on the capture machine.
4. **Extra terms**: who keeps the customer/project name list for Julia's recordings, and where does it live (outside the repo)?
5. **Provenance values**: agree on the allowed values for `consent` and `license`.
