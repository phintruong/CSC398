"""Acceptance test, part B, for file-edit detection (KAN-30, design section 8).

Each scenario runs the real preexec/precmd steps (targets -> snapshot ->
edit the file the way the command would -> snapshot -> diff) in a temp dir.
The edit itself is done from Python so the test runs on any OS; the shell
hooks that call these steps are tested in test_capture.py.
B11 (suspended editor) is covered by the capture session tests.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from contracts import validate_event
from fileedit import MAX_BYTES, Snapshot, targets_for_command
from scrubber import PlaceholderMap, Scrubber


def run(cmd: str, cwd: Path, edit, **kw) -> list[dict]:
    targets = targets_for_command(cmd, str(cwd))
    before = Snapshot.take(targets)
    edit()
    after = Snapshot.take(targets)
    evs = before.diff(after, t=1.0, t_end=2.0, command=cmd, **kw)
    for i, ev in enumerate(evs):
        assert validate_event({**ev, "id": f"f{i}"}) == []
    return evs


@pytest.fixture
def d(tmp_path: Path) -> Path:
    (tmp_path / "notes.txt").write_text("one\ntwo\n")
    (tmp_path / "site.conf").write_text("server {\n  listen 80;\n}\n")
    return tmp_path


def test_b1_vim_add_line(d):
    evs = run("vim notes.txt", d, lambda: (d / "notes.txt").write_text("one\ntwo\nthree\n"))
    assert len(evs) == 1
    ev = evs[0]
    assert (ev["change"], ev["tool"], ev["is_config"]) == ("modified", "vim", False)
    assert "+three" in ev["diff"] and ev["diff"].startswith("--- a/")


def test_b2_vim_quit_without_saving(d):
    assert run("vim notes.txt", d, lambda: None) == []


def test_b3_sed_in_place_config(d):
    p = d / "site.conf"
    evs = run("sed -i 's/80/8080/' site.conf", d, lambda: p.write_text(p.read_text().replace("80", "8080")))
    assert len(evs) == 1 and evs[0]["is_config"] and evs[0]["tool"] == "sed"
    assert "-  listen 80;" in evs[0]["diff"] and "+  listen 8080;" in evs[0]["diff"]


def test_b4_append_to_dotfile(d):
    rc = d / ".bashrc"
    rc.write_text("# rc\n")
    evs = run("echo 'export X=1' >> .bashrc", d, lambda: rc.write_text("# rc\nexport X=1\n"))
    assert len(evs) == 1 and evs[0]["is_config"] and evs[0]["tool"] == "redirect"


def test_b5_redirect_creates_file(d):
    evs = run("ls > listing.txt", d, lambda: (d / "listing.txt").write_text("a\nb\n"))
    assert [e["change"] for e in evs] == ["created"]
    assert "+a" in evs[0]["diff"]


def test_b6_tee_two_files(d):
    def edit():
        for n in ("a.log", "b.log"):
            (d / n).write_text("hi\n")

    evs = run("echo hi | tee -a a.log b.log", d, edit)
    assert sorted(Path(e["path"]).name for e in evs) == ["a.log", "b.log"]


PATCH = """--- a/src/x.py
+++ b/src/x.py
@@ -1 +1 @@
-a = 1
+a = 2
--- a/src/y.py
+++ b/src/y.py
@@ -1 +1 @@
-b = 1
+b = 2
"""


@pytest.mark.parametrize("cmd,tool", [("patch -p1 < fix.diff", "patch"), ("git apply fix.diff", "git")])
def test_b7_b8_patch_two_files(d, cmd, tool):
    (d / "src").mkdir()
    (d / "src/x.py").write_text("a = 1\n")
    (d / "src/y.py").write_text("b = 1\n")
    (d / "fix.diff").write_text(PATCH)

    def edit():
        (d / "src/x.py").write_text("a = 2\n")
        (d / "src/y.py").write_text("b = 2\n")

    evs = run(cmd, d, edit)
    assert sorted(Path(e["path"]).name for e in evs) == ["x.py", "y.py"]
    assert {e["tool"] for e in evs} == {tool}


@pytest.mark.parametrize("cmd", ["cat site.conf", "grep x site.conf", "ls 2>/dev/null"])
def test_b9_read_only(d, cmd):
    assert targets_for_command(cmd, str(d)) == []


def test_b10_binary(d):
    b = d / "big.bin"
    b.write_bytes(b"\x00\x01" * 10)
    evs = run("vim big.bin", d, lambda: b.write_bytes(b"\x00\x02" * 10))
    assert len(evs) == 1 and evs[0]["binary"] and evs[0]["diff"] is None


def test_large_file_truncated(d):
    b = d / "huge.txt"
    b.write_text("x" * (MAX_BYTES + 10))
    evs = run("vim huge.txt", d, lambda: b.write_text("y" * (MAX_BYTES + 10)))
    assert len(evs) == 1 and evs[0]["truncated"] and evs[0]["diff"] is None


def test_b12_secret_in_dotenv_is_scrubbed_with_shared_map(d):
    secret = "Zq8vR2mLx7Tn4Kp9Wc3Y"  # random-looking, fake
    shared = PlaceholderMap()
    # the recording already saw this password typed at a prompt
    shared.get("PASSWORD", secret)
    cmd = f'echo "DB_PASSWORD={secret}" > .env'
    evs = run(cmd, d, lambda: (d / ".env").write_text(f"DB_PASSWORD={secret}\n"), scrubber=Scrubber(mapping=shared))
    assert len(evs) == 1
    blob = repr(evs)
    assert secret not in blob
    assert "<PASSWORD_1>" in evs[0]["diff"] and "<PASSWORD_1>" in evs[0]["command"]
    assert evs[0]["redaction"].startswith("scrub-")


def test_b13_cd_then_edit(d):
    sub = d / "sub"
    sub.mkdir()
    targets = targets_for_command(f"cd {sub.as_posix()} && nano x.txt", str(d))  # commands are POSIX shell
    assert targets == [(os.path.normpath(str(sub / "x.txt")), "nano")]


def test_deleted_file(d):
    evs = run("git checkout -- notes.txt", d, lambda: (d / "notes.txt").unlink())
    assert [e["change"] for e in evs] == ["deleted"]


def test_raw_mode_marks_pending(d):
    evs = run("vim notes.txt", d, lambda: (d / "notes.txt").write_text("x\n"), raw=True)
    assert evs[0]["redaction"] == "pending"


def test_snapshot_round_trips_through_json(d):
    s = Snapshot.take(targets_for_command("vim notes.txt", str(d)))
    assert Snapshot.from_json(s.to_json()).to_json() == s.to_json()
