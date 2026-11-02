"""Detectors for secrets and PII in terminal text.

Every detector takes *clean* text (ANSI escape sequences and control
characters already removed, see ``strip_controls``) and returns a list of
``Finding`` objects with offsets into that clean text. ``core.Scrubber``
maps the offsets back to the raw text and decides which findings win
when they overlap.

Priorities: lower numbers win an overlap. Secrets come first, then
identifiers, then the high-randomness guess, which is the noisiest.
"""

from __future__ import annotations

import ipaddress
import math
import re
from collections import Counter
from dataclasses import dataclass

# Kinds. The placeholder text is "<KIND_n>".
PASSWORD = "PASSWORD"
SECRET = "SECRET"
TOKEN = "TOKEN"
PRIVATE_KEY = "PRIVATE_KEY"
USER = "USER"
HOST = "HOST"
IP = "IP"
NAME = "NAME"

SECRET_KINDS = frozenset({PASSWORD, SECRET, TOKEN, PRIVATE_KEY})


@dataclass(frozen=True)
class Finding:
    start: int
    end: int
    kind: str
    value: str
    priority: int = 5


def _f(m: re.Match, group: int, kind: str, priority: int) -> Finding:
    return Finding(m.start(group), m.end(group), kind, m.group(group), priority)


# ---------------------------------------------------------------------------
# Control-sequence stripping
# ---------------------------------------------------------------------------

_CONTROL = re.compile(
    r"\x1b\[[0-?]*[ -/]*[@-~]"  # CSI: colours, cursor movement
    r"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)?"  # OSC: window title etc.
    r"|\x1b[@-Z\\-_]"  # two-byte escapes
    r"|[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]"  # other control chars (keep \t \n \r)
)


def strip_controls(raw: str) -> tuple[str, list[int]]:
    """Return (clean, index) where index[i] is the raw offset of clean[i]."""
    clean: list[str] = []
    index: list[int] = []
    pos = 0
    for m in _CONTROL.finditer(raw):
        clean.append(raw[pos : m.start()])
        index.extend(range(pos, m.start()))
        pos = m.end()
    clean.append(raw[pos:])
    index.extend(range(pos, len(raw)))
    return "".join(clean), index


# ---------------------------------------------------------------------------
# Allowlists: things that look like identifiers but are public or generic,
# and that a learner needs to see.
# ---------------------------------------------------------------------------

ALLOWED_USERS = frozenset(
    {
        "root", "git", "ubuntu", "debian", "centos", "fedora", "admin", "user",
        "nobody", "www-data", "postgres", "mysql", "redis", "daemon", "bin",
        "sys", "ec2-user", "runner", "docker", "jenkins", "shared", "public",
        "default", "default user", "all users",
    }
)

ALLOWED_HOST_SUFFIXES = (
    "localhost", "localdomain", "example.com", "example.org", "example.net",
    "github.com", "githubusercontent.com", "gitlab.com", "bitbucket.org",
    "ubuntu.com", "debian.org", "python.org", "pypi.org", "npmjs.org",
    "npmjs.com", "docker.io", "docker.com", "k8s.io", "kubernetes.io",
    "quay.io", "ghcr.io", "gcr.io", "golang.org", "go.dev", "rust-lang.org",
    "crates.io", "alpinelinux.org", "fedoraproject.org", "centos.org",
    "nixos.org", "haskell.org", "google.com", "googleapis.com",
    "cloudflare.com", "letsencrypt.org", "kernel.org", "gnu.org",
    "asciinema.org", "microsoft.com", "apple.com",
)


def host_allowed(host: str) -> bool:
    h = host.lower().rstrip(".")
    return any(h == s or h.endswith("." + s) for s in ALLOWED_HOST_SUFFIXES)


def ip_allowed(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if ip.is_loopback or ip.is_unspecified:
        return True
    if isinstance(ip, ipaddress.IPv4Address):
        n = int(ip)
        inverted = ~n & 0xFFFFFFFF
        if n and (inverted & (inverted + 1)) == 0:  # netmask or broadcast-all
            return True
    return False


def _parse_ip(text: str):
    try:
        return ipaddress.ip_address(text.split("%", 1)[0])
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# 1. Known secret formats
# ---------------------------------------------------------------------------

_TOKEN_RES = [
    re.compile(p)
    for p in (
        r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b",  # AWS access key id
        r"\bgh[pousr]_[A-Za-z0-9]{36,255}\b",  # GitHub
        r"\bgithub_pat_[A-Za-z0-9_]{22,255}\b",
        r"\bglpat-[A-Za-z0-9_-]{20,}",  # GitLab
        r"\bxox[abposr]-[A-Za-z0-9-]{10,}",  # Slack
        r"\bAIza[0-9A-Za-z_-]{35}",  # Google API key
        r"\b[sr]k_(?:live|test)_[0-9A-Za-z]{16,}",  # Stripe
        r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_-]{20,}",  # OpenAI / Anthropic style
        r"\bnpm_[A-Za-z0-9]{36}\b",
        r"\bhf_[A-Za-z0-9]{30,}\b",  # Hugging Face
        r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}",  # JWT
    )
]

_AUTH_HEADER = re.compile(
    r"(?i)\b(?:authorization:[ \t]*(?:bearer|basic|token)|bearer)[ \t]+([A-Za-z0-9._~+/-]{8,}=*)"
)


def detect_known_tokens(text: str) -> list[Finding]:
    out = [Finding(m.start(), m.end(), TOKEN, m.group(), 0) for r in _TOKEN_RES for m in r.finditer(text)]
    out += [_f(m, 1, TOKEN, 0) for m in _AUTH_HEADER.finditer(text)]
    return out


_PEM_BEGIN = re.compile(r"-----BEGIN ([A-Z0-9 ]*)PRIVATE KEY-----")
_B64_LINES = re.compile(r"(?:[ \t]*\r?\n)?(?:[ \t]*[A-Za-z0-9+/=:\- ]+[ \t]*\r*\n)+")


def detect_private_keys(text: str) -> list[Finding]:
    """Replace the body of a PEM private key; keep the BEGIN/END lines."""
    out = []
    for m in _PEM_BEGIN.finditer(text):
        end_marker = f"-----END {m.group(1)}PRIVATE KEY-----"
        end = text.find(end_marker, m.end(), m.end() + 20000)
        if end != -1:
            body_start, body_end = m.end(), end
        else:  # truncated on screen: take the base64 lines that follow
            b = _B64_LINES.match(text, m.end())
            if not b:
                continue
            body_start, body_end = b.start(), b.end()
        # keep surrounding line breaks so the block keeps its shape
        while body_start < body_end and text[body_start] in "\r\n":
            body_start += 1
        while body_end > body_start and text[body_end - 1] in "\r\n \t":
            body_end -= 1
        if body_end > body_start:
            out.append(Finding(body_start, body_end, PRIVATE_KEY, text[body_start:body_end], 0))
    return out


# ---------------------------------------------------------------------------
# 2. key=value, flags and connection strings
# ---------------------------------------------------------------------------

_KEYWORD = (
    r"(?:pass(?:word|wd|phrase)?|pwd|secret|token|api[_-]?key|apikey|access[_-]?key"
    r"|private[_-]?key|client[_-]?secret|credentials?)"
)
_KEY_VALUE = re.compile(
    r"(?i)(?<![A-Za-z0-9])([A-Za-z0-9_.-]*" + _KEYWORD + r"[A-Za-z0-9_.-]*)[\"']?[ \t]*[=:][ \t]*[\"']?([^\s\"'&]{4,})"
)
_FLAG_VALUE = re.compile(
    r"(?i)(?<![\w-])--?([a-z-]*(?:password|passwd|token|secret|api-key))[= ]([^\s\"'-][^\s\"']{3,})"
)
_MYSQL_P = re.compile(r"(?<=\s)-p([^\s]{4,})")
_MYSQL_LINE = re.compile(r"\b(?:mysql|mariadb|mysqldump|mysqladmin)\b")
_NOT_A_VALUE = re.compile(r"(?i)^(?:true|false|null|none|yes|no|required|optional|\*+|x+)$")


def _value_kind(key: str) -> str:
    k = key.lower()
    return PASSWORD if ("pass" in k or "pwd" in k) else SECRET


def _real_value(v: str) -> bool:
    return not (v[0] in "$<{%*(" or _NOT_A_VALUE.match(v))


def detect_assignments(text: str) -> list[Finding]:
    out = []
    for m in _KEY_VALUE.finditer(text):
        if _real_value(m.group(2)):
            out.append(_f(m, 2, _value_kind(m.group(1)), 1))
    for m in _FLAG_VALUE.finditer(text):
        if _real_value(m.group(2)):
            out.append(_f(m, 2, _value_kind(m.group(1)), 1))
    for line in re.finditer(r"[^\n]+", text):
        if _MYSQL_LINE.search(line.group()):
            for m in _MYSQL_P.finditer(line.group()):
                s = line.start() + m.start(1)
                out.append(Finding(s, s + len(m.group(1)), PASSWORD, m.group(1), 1))
    return out


_CONN = re.compile(
    r"\b[a-zA-Z][a-zA-Z0-9+.-]{1,20}://([^\s:/@'\"]*)(?::([^\s@/'\"]+))?@([A-Za-z0-9._-]+|\[[0-9a-fA-F:.]+\])"
)


def _host_finding(start: int, host: str, priority: int) -> Finding | None:
    bare = host.strip("[]")
    ip = _parse_ip(bare)
    if ip is not None:
        if ip_allowed(ip):
            return None
        off = start + (1 if host.startswith("[") else 0)
        return Finding(off, off + len(bare), IP, bare, priority)
    if host_allowed(host):
        return None
    return Finding(start, start + len(host), HOST, host, priority)


def detect_connection_strings(text: str) -> list[Finding]:
    out = []
    for m in _CONN.finditer(text):
        user, pw, host = m.group(1), m.group(2), m.group(3)
        if user and user.lower() not in ALLOWED_USERS:
            out.append(_f(m, 1, USER, 2))
        if pw:
            out.append(_f(m, 2, PASSWORD, 0))
        h = _host_finding(m.start(3), host, 2)
        if h:
            out.append(h)
    return out


# ---------------------------------------------------------------------------
# 3. Network identifiers: IPs, hostnames, user@host, home directories
# ---------------------------------------------------------------------------

_IPV4 = re.compile(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?!\w|\.\d)")
_IPV6 = re.compile(r"(?<![\w:])(?:[0-9A-Fa-f]{0,4}:){2,7}[0-9A-Fa-f]{0,4}(?:%[\w.]+)?(?![\w:])")


def detect_ips(text: str) -> list[Finding]:
    out = []
    for m in _IPV4.finditer(text):
        ip = _parse_ip(m.group())
        if ip is not None and not ip_allowed(ip):
            out.append(Finding(m.start(), m.end(), IP, m.group(), 2))
    for m in _IPV6.finditer(text):
        s = m.group()
        if "::" not in s and s.count(":") != 7:
            continue  # times (12:30:45) and MAC addresses
        ip = _parse_ip(s)
        if ip is not None and not ip_allowed(ip):
            out.append(Finding(m.start(), m.end(), IP, s, 2))
    return out


_TLDS = (
    "com|net|org|io|ca|dev|app|cloud|ai|co|us|uk|de|eu|fr|nl|info|biz|me|tech"
    "|local|internal|lan|corp|intra|home|localdomain|cluster|svc"
)
_FQDN = re.compile(
    r"(?i)(?<![\w@.-])((?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+(?:" + _TLDS + r"))(?![\w-]|\.[a-z0-9])"
)


def detect_hostnames(text: str) -> list[Finding]:
    return [
        Finding(m.start(), m.end(), HOST, m.group(), 3)
        for m in _FQDN.finditer(text)
        if not host_allowed(m.group())
    ]


_USER_AT_HOST = re.compile(
    r"(?:^|(?<=[\s\[(:'\"<,]))([A-Za-z_][A-Za-z0-9_.-]{0,31})@([A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?)",
    re.M,
)


def detect_user_at_host(text: str) -> list[Finding]:
    """Shell prompts (alice@devbox:~$), ssh targets and e-mail addresses."""
    out = []
    for m in _USER_AT_HOST.finditer(text):
        user, host = m.group(1), m.group(2)
        if host.lower() in ("sha256", "sha512") or re.fullmatch(r"[\d.]+", host) and _parse_ip(host) is None:
            continue  # image@sha256:..., package@1.2.3
        if user.lower() not in ALLOWED_USERS:
            out.append(_f(m, 1, USER, 2))
        h = _host_finding(m.start(2), host, 2)
        if h:
            out.append(h)
    return out


_HOME = re.compile(r"(?:/home/|/Users/|(?i:[a-z]:\\Users\\))([A-Za-z0-9_][A-Za-z0-9_. -]*?)(?=[/\\\s:'\"]|$)", re.M)


def detect_home_dirs(text: str) -> list[Finding]:
    return [_f(m, 1, USER, 2) for m in _HOME.finditer(text) if m.group(1).lower() not in ALLOWED_USERS]


# ---------------------------------------------------------------------------
# 4. High-randomness strings (catches secrets with no known format)
# ---------------------------------------------------------------------------

_CANDIDATE = re.compile(r"(?<![A-Za-z0-9+/_.-])[A-Za-z0-9+/_.-]{20,}={0,2}(?![A-Za-z0-9+/_.=-])")
_HEXISH = re.compile(r"[0-9a-fA-F-]+")


def shannon_entropy(s: str) -> float:
    n = len(s)
    return -sum(c / n * math.log2(c / n) for c in Counter(s).values())


def looks_random(tok: str) -> bool:
    if _HEXISH.fullmatch(tok):
        return False  # git SHAs, image digests, UUIDs: common and not secret
    if not (re.search(r"[a-z]", tok) and re.search(r"[A-Z]", tok) and re.search(r"\d", tok)):
        return False
    if tok[0] in "/.~" or ("/" in tok and max(len(p) for p in tok.split("/")) < 16):
        return False  # file paths
    threshold = 3.9 if len(tok) < 32 else 4.2
    return shannon_entropy(tok.rstrip("=")) >= threshold


def detect_high_entropy(text: str) -> list[Finding]:
    return [Finding(m.start(), m.end(), SECRET, m.group(), 4) for m in _CANDIDATE.finditer(text) if looks_random(m.group())]


DETECTORS = (
    detect_private_keys,
    detect_known_tokens,
    detect_connection_strings,
    detect_assignments,
    detect_user_at_host,
    detect_home_dirs,
    detect_ips,
    detect_hostnames,
    detect_high_entropy,
)


def detect_all(clean: str) -> list[Finding]:
    found: list[Finding] = []
    for d in DETECTORS:
        found.extend(d(clean))
    return found
