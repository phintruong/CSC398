"""Acceptance test, part A, for file-edit detection (KAN-29, epic KAN-3).

Written ahead of the implementation. The whole module is skipped until a
``fileedit`` module exists; after that, these cases are the gate.
Part B (end-to-end shell scenarios) is in docs/design/file-edit-detection.md.
"""

from __future__ import annotations

import os

import pytest

fileedit = pytest.importorskip("fileedit", reason="fileedit not implemented yet (KAN-3)")

CWD = "/home/dev/proj"
HOME = os.path.expanduser("~")


def _abs(p: str, cwd: str = CWD) -> str:
    p = os.path.expanduser(p)
    return os.path.normpath(p if os.path.isabs(p) else os.path.join(cwd, p))


# (command, [(path, tool), ...]); paths relative to CWD unless absolute
TARGET_CASES = [
    # editors
    ("vim app.py", [("app.py", "vim")]),
    ("vim +42 app.py", [("app.py", "vim")]),
    ("nvim -O a.py b.py", [("a.py", "nvim"), ("b.py", "nvim")]),
    ("nano -w notes.txt", [("notes.txt", "nano")]),
    ("sudo vim /etc/nginx/nginx.conf", [("/etc/nginx/nginx.conf", "vim")]),
    ("sudoedit /etc/hosts", [("/etc/hosts", "sudoedit")]),
    ("EDITOR=vim FOO=1 vim a.txt", [("a.txt", "vim")]),
    ("vim", []),
    # sed / perl in place
    ("sed -i 's/a/b/' config.yaml", [("config.yaml", "sed")]),
    ("sed -i.bak -e 's/x/y/' -e 's/1/2/' a.txt b.txt", [("a.txt", "sed"), ("b.txt", "sed")]),
    ("sed --in-place 's/a/b/' x.conf", [("x.conf", "sed")]),
    ("sed 's/a/b/' in.txt", []),
    ("perl -pi -e 's/a/b/' x.txt", [("x.txt", "perl")]),
    # redirects
    ("echo hi > out.txt", [("out.txt", "redirect")]),
    ("echo 'export X=1' >> ~/.bashrc", [("~/.bashrc", "redirect")]),
    ("grep -r foo . > results.txt", [("results.txt", "redirect")]),
    ("make 2> build.log", [("build.log", "redirect")]),
    ("cat <<EOF > app.conf", [("app.conf", "redirect")]),
    ("ls 2>/dev/null", []),
    ("cmd > /dev/null 2>&1", []),
    # tee
    ("echo x | tee -a log.txt other.txt", [("log.txt", "tee"), ("other.txt", "tee")]),
    ("echo '10.0.0.5 db' | sudo tee -a /etc/hosts", [("/etc/hosts", "tee")]),
    # git discards
    ("git checkout -- src/main.py", [("src/main.py", "git")]),
    ("git restore README.md", [("README.md", "git")]),
    # compound commands follow cd
    ("cd /etc && sudo nano hosts", [("/etc/hosts", "nano")]),
    ("cd sub; vim x.py", [("sub/x.py", "vim")]),
    # read-only commands
    ("cat app.py", []),
    ("less /var/log/syslog", []),
    ("grep -n listen /etc/nginx/nginx.conf", []),
    ("python3 script.py", []),
    ("git diff", []),
]


@pytest.mark.parametrize("command,expected", TARGET_CASES, ids=[c for c, _ in TARGET_CASES])
def test_targets_for_command(command, expected):
    got = fileedit.targets_for_command(command, CWD)
    assert sorted(got) == sorted((_abs(p), tool) for p, tool in expected)


CONFIG_CASES = [
    ("/etc/nginx/sites-available/default", True),
    ("/etc/hosts", True),
    (f"{HOME}/.bashrc", True),
    (f"{HOME}/.ssh/config", True),
    (f"{HOME}/.config/fish/config.fish", True),
    ("/srv/app/.env", True),
    ("/srv/app/docker-compose.yml", True),
    ("/srv/app/Dockerfile", True),
    ("/srv/app/settings.toml", True),
    ("/etc/systemd/system/app.service", True),
    ("/etc/sudoers.d/90-deploy", True),
    ("/srv/app/main.py", False),
    ("/srv/app/README.md", False),
    ("/srv/app/src/index.ts", False),
]


@pytest.mark.parametrize("path,expected", CONFIG_CASES, ids=[p for p, _ in CONFIG_CASES])
def test_is_config(path, expected):
    assert fileedit.is_config(path) is expected
