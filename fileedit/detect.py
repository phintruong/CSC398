"""Which files will a command line edit? (design section 3) and is a path a config file? (section 6)"""

from __future__ import annotations

import fnmatch
import os
import re
import shlex
from pathlib import Path

EDITORS = frozenset({"vim", "vi", "nvim", "nano", "emacs", "micro", "ed", "sudoedit", "pico", "joe", "kak", "hx"})
# vim options that take a value (so the value is not a file); other editors' options are flags
_VIM_FAMILY = frozenset({"vim", "vi", "nvim"})
_EDITOR_ARG_OPTS = frozenset({"-c", "--cmd", "-S", "-u", "-i", "-T", "-w", "-W", "-s", "-t", "-q", "-r", "--servername"})

_SEPARATORS = frozenset({";", "&&", "||", "|", "&", "|&", ";;", "(", ")", "{", "}"})
_OUT_REDIRECTS = frozenset({">", ">>", "&>", "&>>", ">|"})
_IN_REDIRECTS = frozenset({"<", "<<", "<<<", "<<-"})
_WRAPPERS = frozenset({"env", "command", "nohup", "time", "nice", "builtin", "exec", "stdbuf", "doas"})
_SUDO_ARG_OPTS = frozenset({"-u", "-g", "-h", "-p", "-C", "-D", "-r", "-t", "-U"})
_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def _tokens(command: str) -> list[str] | None:
    lex = shlex.shlex(command, posix=True, punctuation_chars=";&|<>()")
    lex.whitespace_split = True
    lex.commenters = ""
    try:
        return list(lex)
    except ValueError:  # unbalanced quotes: a half-typed or multi-line command
        return None


def _abspath(p: str, cwd: str) -> str:
    p = os.path.expanduser(p)
    return os.path.normpath(p if os.path.isabs(p) else os.path.join(cwd, p))


def _ignored_target(p: str) -> bool:
    return p.startswith("/dev/") or p.startswith("&") or p.isdigit() or p == ""


def _split_segments(tokens: list[str]) -> list[tuple[list[str], list[str], str | None]]:
    """Split into simple commands. Return (words, output_targets, stdin_file) per command."""
    segs = []
    words: list[str] = []
    outs: list[str] = []
    stdin: str | None = None
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok in _SEPARATORS:
            segs.append((words, outs, stdin))
            words, outs, stdin = [], [], None
        elif tok in _OUT_REDIRECTS:
            if i + 1 < len(tokens) and not _ignored_target(tokens[i + 1]):
                outs.append(tokens[i + 1])
            i += 1
        elif tok in (">&", "<&", "<>"):
            i += 1  # 2>&1 and friends
        elif tok in _IN_REDIRECTS:
            if tok == "<" and i + 1 < len(tokens):
                stdin = tokens[i + 1]
            i += 1
        else:
            words.append(tok)
        i += 1
    segs.append((words, outs, stdin))
    return segs


def _strip_prefixes(words: list[str]) -> list[str]:
    """Drop VAR=value, sudo (with its options), env and similar wrappers."""
    w = list(words)
    while w:
        head = w[0]
        if _ASSIGN.match(head):
            w.pop(0)
        elif head == "sudo" or head == "doas":
            w.pop(0)
            while w and w[0].startswith("-"):
                opt = w.pop(0)
                if opt in _SUDO_ARG_OPTS and w:
                    w.pop(0)
                if opt == "--":
                    break
        elif head in _WRAPPERS:
            w.pop(0)
            while w and w[0].startswith("-"):
                w.pop(0)
        else:
            break
    return w


def _editor_files(args: list[str], name: str) -> list[str]:
    arg_opts = _EDITOR_ARG_OPTS if name in _VIM_FAMILY else frozenset()
    files, skip = [], False
    for a in args:
        if skip:
            skip = False
        elif a in arg_opts:
            skip = True
        elif a.startswith("+") or (a.startswith("-") and a != "-"):
            continue
        elif a != "-":
            files.append(a)
    return files


def _sed_files(args: list[str]) -> list[str]:
    # -i, -i.bak, -Ei, -ni ...: a short option cluster containing i
    in_place = any(a.startswith("--in-place") or re.fullmatch(r"-[A-Za-z]*i.*", a) and not a.startswith("--") for a in args)
    if not in_place:
        return []
    files: list[str] = []
    have_script = False
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("-e", "--expression", "-f", "--file", "-l", "--line-length"):
            have_script = have_script or a in ("-e", "--expression", "-f", "--file")
            i += 2
            continue
        if a.startswith(("--expression=", "--file=")):
            have_script = True
        elif a.startswith("-") and a != "-":
            pass
        elif not have_script:
            have_script = True  # first non-option is the script
        else:
            files.append(a)
        i += 1
    return files


def _perl_files(args: list[str]) -> list[str]:
    if not any(re.fullmatch(r"-[A-Za-z]*i.*", a) for a in args):
        return []
    files: list[str] = []
    have_script = False
    i = 0
    while i < len(args):
        a = args[i]
        if a.startswith("-") and a != "-":
            cluster = a[1:].split(".")[0]
            # -e takes the next argument (also when it ends a cluster like -pie)
            if cluster.endswith("e") or cluster.endswith("E"):
                have_script = True
                i += 2
                continue
        elif not have_script:
            have_script = True
        else:
            files.append(a)
        i += 1
    return files


def _patch_paths(patch_text: str, strip: int | None) -> list[str]:
    paths: list[str] = []
    minus: str | None = None
    for line in patch_text.splitlines():
        if line.startswith("--- "):
            minus = line[4:].split("\t")[0].strip()
        elif line.startswith("+++ "):
            plus = line[4:].split("\t")[0].strip()
            name = plus if plus != "/dev/null" else (minus or "")
            if not name or name == "/dev/null":
                continue
            parts = name.split("/")
            if strip is None:
                name = parts[-1]
            else:
                name = "/".join(parts[strip:]) if len(parts) > strip else parts[-1]
            if name and name not in paths:
                paths.append(name)
    return paths


def _read_patch(path: str | None, cwd: str) -> str:
    if not path:
        return ""
    try:
        return Path(_abspath(path, cwd)).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _patch_files(args: list[str], stdin: str | None, cwd: str) -> list[str]:
    strip: int | None = None
    patch_file = stdin
    explicit: list[str] = []
    i = 0
    while i < len(args):
        a = args[i]
        if a.startswith("-p") and a[2:].isdigit():
            strip = int(a[2:])
        elif a == "-p" and i + 1 < len(args):
            strip = int(args[i + 1]) if args[i + 1].isdigit() else strip
            i += 1
        elif a.startswith("--strip="):
            strip = int(a.split("=", 1)[1])
        elif a in ("-i", "--input") and i + 1 < len(args):
            patch_file = args[i + 1]
            i += 1
        elif a.startswith("--input="):
            patch_file = a.split("=", 1)[1]
        elif not a.startswith("-"):
            explicit.append(a)  # patch [options] [originalfile [patchfile]]
        i += 1
    if explicit:
        if len(explicit) >= 2 and patch_file is None:
            patch_file = explicit[1]
        return [explicit[0]]
    return _patch_paths(_read_patch(patch_file, cwd), strip)


def _git_files(args: list[str], stdin: str | None, cwd: str) -> list[str]:
    if not args:
        return []
    sub, rest = args[0], args[1:]
    if sub in ("apply", "am"):
        strip = 1
        patches = []
        for a in rest:
            if a.startswith("-p") and a[2:].isdigit():
                strip = int(a[2:])
            elif a in ("--check", "--stat", "--numstat", "--summary"):
                return []  # read-only
            elif not a.startswith("-"):
                patches.append(a)
        text = "".join(_read_patch(p, cwd) for p in patches) or _read_patch(stdin, cwd)
        return _patch_paths(text, strip)
    if sub == "checkout" and "--" in rest:
        return [a for a in rest[rest.index("--") + 1 :]]
    if sub == "restore":
        out, after_dd = [], False
        skip = False
        for a in rest:
            if skip:
                skip = False
            elif a == "--":
                after_dd = True
            elif not after_dd and a in ("-s", "--source"):
                skip = True
            elif after_dd or not a.startswith("-"):
                out.append(a)
        return out
    return []


def targets_for_command(command: str, cwd: str) -> list[tuple[str, str]]:
    """[(absolute_path, tool)] for files the command may edit. Pure: never touches
    the disk, except to read a patch file to see which files it names."""
    tokens = _tokens(command)
    if not tokens:
        return []
    result: list[tuple[str, str]] = []

    def add(paths: list[str], tool: str) -> None:
        for p in paths:
            if _ignored_target(p):
                continue
            item = (_abspath(p, cwd), tool)
            if item not in result:
                result.append(item)

    for raw_words, outs, stdin in _split_segments(tokens):
        add(outs, "redirect")
        words = _strip_prefixes(raw_words)
        if not words:
            continue
        name = os.path.basename(words[0])
        args = words[1:]
        if name == "cd":
            target = next((a for a in args if not a.startswith("-")), "~")
            if target != "-":
                cwd = _abspath(target, cwd)
        elif name in EDITORS:
            add(_editor_files(args, name), name)
        elif name == "sed":
            add(_sed_files(args), "sed")
        elif name == "perl":
            add(_perl_files(args), "perl")
        elif name == "tee":
            add([a for a in args if not a.startswith("-")], "tee")
        elif name == "patch":
            add(_patch_files(args, stdin, cwd), "patch")
        elif name == "git":
            add(_git_files(args, stdin, cwd), "git")
    return result


# -- config flag (design section 6) -------------------------------------------

CONFIG_EXTS = frozenset(
    {".conf", ".cfg", ".ini", ".yaml", ".yml", ".toml", ".json", ".env", ".properties",
     ".service", ".timer", ".socket", ".rules"}
)
CONFIG_NAMES = frozenset({"Dockerfile", "Makefile", "nginx.conf", "crontab", "hosts", "fstab", "sshd_config", "ssh_config"})
CONFIG_GLOBS = ("docker-compose.*", "compose.*", "Dockerfile.*", "*.dockerfile")


def _norm(p: str) -> str:
    return p.replace("\\", "/")


def is_config(path: str) -> bool:
    p = _norm(path)
    home = _norm(os.path.expanduser("~")).rstrip("/")
    prefixes = ("/etc/", "/usr/local/etc/", home + "/.config/", home + "/.ssh/")
    if p.startswith(prefixes) or re.match(r"^/opt/[^/]+/conf", p):
        return True
    parts = p.rstrip("/").split("/")
    name = parts[-1]
    if name.startswith(".") and name not in (".", ".."):
        return True
    if os.path.splitext(name)[1].lower() in CONFIG_EXTS:
        return True
    if name in CONFIG_NAMES or any(fnmatch.fnmatch(name, g) for g in CONFIG_GLOBS):
        return True
    return len(parts) >= 2 and parts[-2].endswith(".d")
