"""Planted-secret test set for the scrubber (KAN-33).

Each case is a synthetic asciinema recording with fake secrets planted in
it, plus the list of planted values and a "keep" list of things that must
survive scrubbing (so we measure over-scrubbing too).

The secrets are generated at run time from a fixed seed instead of being
committed, so nothing secret-shaped sits in the repo and GitHub push
protection has nothing to flag. Every value is random and fake.

Write the set to disk to inspect it or run another tool on it:

    python tests/planted_set.py out_dir/
"""

from __future__ import annotations

import json
import random
import string
import sys
from dataclasses import dataclass, field
from pathlib import Path

SEED = 398

B62 = string.ascii_letters + string.digits
B64 = B62 + "+/"
PW_CHARS = B62 + "!#%^*"


@dataclass
class Planted:
    value: str
    kind: str


@dataclass
class Case:
    name: str
    description: str
    cast: str
    planted: list[Planted]
    keep: list[str] = field(default_factory=list)
    extra_terms: list[str] = field(default_factory=list)


class CastBuilder:
    """Builds a recording the way a real shell session looks: typed input
    arrives one key per "i" event, echo comes back as "o" events, prompts
    are coloured and set the window title."""

    def __init__(self, title: str = "") -> None:
        self.t = 0.0
        self.events: list[list] = []
        self.title = title

    def _ev(self, code: str, data: str, dt: float = 0.05) -> None:
        self.t = round(self.t + dt, 6)
        self.events.append([self.t, code, data])

    def out(self, text: str, chunks: int = 1) -> "CastBuilder":
        size = max(1, -(-len(text) // chunks))
        for i in range(0, len(text), size):
            self._ev("o", text[i : i + size])
        return self

    def line(self, text: str = "") -> "CastBuilder":
        return self.out(text + "\r\n")

    def typed(self, text: str, echo: bool = True) -> "CastBuilder":
        for ch in text:
            self._ev("i", ch, 0.08)
            if echo:
                self._ev("o", ch, 0.002)
        self._ev("i", "\r", 0.1)
        self._ev("o", "\r\n", 0.01)
        return self

    def secret_entry(self, text: str) -> "CastBuilder":
        """Typing at a no-echo prompt."""
        return self.typed(text, echo=False)

    def prompt(self, user: str, host: str, cwd: str = "~", sigil: str = "$") -> "CastBuilder":
        return self.out(
            f"\x1b]0;{user}@{host}: {cwd}\x07"
            f"\x1b[01;32m{user}@{host}\x1b[00m:\x1b[01;34m{cwd}\x1b[00m{sigil} "
        )

    def cmd(self, user: str, host: str, cwd: str, command: str) -> "CastBuilder":
        return self.prompt(user, host, cwd, "#" if user == "root" else "$").typed(command)

    def build(self) -> str:
        header = {"version": 2, "width": 120, "height": 32, "timestamp": 1760000000,
                  "env": {"SHELL": "/bin/bash", "TERM": "xterm-256color"}}
        if self.title:
            header["title"] = self.title
        return "\n".join(json.dumps(x) for x in [header, *self.events]) + "\n"


def _r(rng: random.Random, alphabet: str, n: int) -> str:
    return "".join(rng.choice(alphabet) for _ in range(n))


def _password(rng: random.Random) -> str:
    # always mixed classes so it is clearly a password
    return _r(rng, string.ascii_uppercase, 2) + _r(rng, PW_CHARS, 10) + _r(rng, string.digits, 2)


def build_cases(seed: int = SEED) -> list[Case]:
    cases: list[Case] = []

    def rng_for(name: str) -> random.Random:
        return random.Random(f"{seed}:{name}")

    # 1 ---------------------------------------------------------------
    r = rng_for("sudo")
    pw = _password(r)
    b = CastBuilder()
    b.cmd("alice", "devbox", "~", "sudo apt update")
    b.out("[sudo] password for alice: ").secret_entry(pw)
    b.line("Hit:1 http://archive.ubuntu.com/ubuntu jammy InRelease")
    b.line("Reading package lists... Done")
    b.prompt("alice", "devbox")
    cases.append(Case("sudo_password", "password typed at a sudo prompt (no echo)", b.build(),
                      [Planted(pw, "PASSWORD"), Planted("alice", "USER"), Planted("devbox", "HOST")],
                      keep=["sudo apt update", "archive.ubuntu.com", "Reading package lists"]))

    # 2 ---------------------------------------------------------------
    r = rng_for("ssh_mysql")
    pw_ssh, pw_mysql, pw_inline = _password(r), _password(r), _r(r, B62, 14)
    host_fq = "db-prod-01.corp.internal"
    b = CastBuilder()
    b.cmd("alice", "devbox", "~", f"ssh deploy@{host_fq}")
    b.out(f"deploy@{host_fq}'s password: ").secret_entry(pw_ssh)
    b.line("Welcome to Ubuntu 22.04.4 LTS (GNU/Linux 5.15.0-91-generic x86_64)")
    b.cmd("deploy", "db-prod-01", "~", "mysql -u app -p orders")
    b.out("Enter password: ").secret_entry(pw_mysql)
    b.line("Welcome to the MySQL monitor.  Commands end with ; or \\g.")
    b.out("mysql> ").typed("exit")
    b.line("Bye")
    b.cmd("deploy", "db-prod-01", "~", f"mysqldump -u app -p{pw_inline} -h 10.20.30.40 orders > /tmp/orders.sql")
    b.prompt("deploy", "db-prod-01")
    cases.append(Case("ssh_and_mysql", "ssh password, mysql prompt password and an inline -p password", b.build(),
                      [Planted(pw_ssh, "PASSWORD"), Planted(pw_mysql, "PASSWORD"), Planted(pw_inline, "PASSWORD"),
                       Planted("deploy", "USER"), Planted(host_fq, "HOST"), Planted("db-prod-01", "HOST"),
                       Planted("10.20.30.40", "IP")],
                      keep=["mysql -u app -p orders", "Enter password:", "5.15.0-91-generic", "/tmp/orders.sql"]))

    # 3 ---------------------------------------------------------------
    r = rng_for("tokens")
    gh = "gh" + "p_" + _r(r, B62, 36)
    aws_id = "AK" + "IA" + _r(r, string.ascii_uppercase + string.digits, 16)
    aws_secret = _r(r, B64, 40)
    llm = "sk" + "-proj-" + _r(r, B62 + "_-", 48)
    b = CastBuilder()
    b.cmd("bob", "laptop", "~/proj", f"export GITHUB_TOKEN={gh}")
    b.cmd("bob", "laptop", "~/proj", f"export AWS_ACCESS_KEY_ID={aws_id}")
    b.cmd("bob", "laptop", "~/proj", f"export AWS_SECRET_ACCESS_KEY={aws_secret}")
    b.cmd("bob", "laptop", "~/proj", f"export OPENAI_API_KEY={llm}")
    b.cmd("bob", "laptop", "~/proj", "echo $GITHUB_TOKEN")
    b.out(gh + "\r\n", chunks=4)  # split across four output events
    b.cmd("bob", "laptop", "~/proj", "env | grep AWS")
    b.out(f"AWS_ACCESS_KEY_ID={aws_id}\r\nAWS_SECRET_ACCESS_KEY={aws_secret}\r\n", chunks=7)
    b.prompt("bob", "laptop", "~/proj")
    cases.append(Case("exported_tokens", "API tokens typed key by key, echoed, then printed split across events", b.build(),
                      [Planted(gh, "TOKEN"), Planted(aws_id, "TOKEN"), Planted(aws_secret, "SECRET"),
                       Planted(llm, "TOKEN"), Planted("bob", "USER"), Planted("laptop", "HOST")],
                      keep=["export GITHUB_TOKEN=", "echo $GITHUB_TOKEN", "env | grep AWS", "AWS_SECRET_ACCESS_KEY="]))

    # 4 ---------------------------------------------------------------
    r = rng_for("conn")
    db_pw, redis_pw = _r(r, B62, 18), _r(r, B62, 24)
    db_host = "pg.internal.example-corp.net"
    b = CastBuilder()
    b.cmd("carol", "ws-114", "~/billing", f"psql postgres://appuser:{db_pw}@{db_host}:5432/orders")
    b.line("psql (15.4)")
    b.out("orders=> ").typed("\\q")
    b.cmd("carol", "ws-114", "~/billing", "cat .env")
    b.line(f"DATABASE_URL=postgresql://appuser:{db_pw}@{db_host}:5432/orders")
    b.line(f"REDIS_URL=redis://:{redis_pw}@10.0.4.12:6379/0")
    b.line("LOG_LEVEL=debug")
    b.prompt("carol", "ws-114", "~/billing")
    cases.append(Case("connection_strings", "credentials inside database URLs, typed and in a .env file", b.build(),
                      [Planted(db_pw, "PASSWORD"), Planted(redis_pw, "PASSWORD"), Planted("appuser", "USER"),
                       Planted(db_host, "HOST"), Planted("10.0.4.12", "IP"), Planted("carol", "USER")],
                      keep=["psql postgres://", ":5432/orders", "DATABASE_URL=postgresql://", "LOG_LEVEL=debug", ":6379/0"]))

    # 5 ---------------------------------------------------------------
    r = rng_for("pem")
    body = [_r(r, B64, 70) for _ in range(6)] + [_r(r, B64, 20) + "=="]
    kind = "OPENSSH " + "PRIVATE KEY"
    b = CastBuilder()
    b.cmd("dave", "buildhost", "~", "cat ~/.ssh/id_ed25519")
    b.out(f"-----BEGIN {kind}-----\r\n" + "\r\n".join(body) + f"\r\n-----END {kind}-----\r\n", chunks=5)
    b.prompt("dave", "buildhost")
    cases.append(Case("private_key", "a private key printed to the terminal", b.build(),
                      [Planted(line, "PRIVATE_KEY") for line in body] + [Planted("dave", "USER")],
                      keep=["cat ~/.ssh/id_ed25519", f"-----BEGIN {kind}-----", f"-----END {kind}-----"]))

    # 6 ---------------------------------------------------------------
    r = rng_for("http")
    jwt = "eyJ" + _r(r, B62, 20) + ".eyJ" + _r(r, B62, 60) + "." + _r(r, B62 + "_-", 43)
    opaque = _r(r, B62, 32)
    api = "api.internal.acme-logistics.io"
    b = CastBuilder()
    b.cmd("erin", "ops-07", "~", f'curl -s -H "Authorization: Bearer {jwt}" https://{api}/v1/orders/42')
    b.line('{"id": 42, "owner": "maria.lopez@acme-logistics.io", "status": "shipped",')
    b.line(f' "refresh": {{"access_token": "{opaque}", "expires_in": 3600}}}}')
    b.prompt("erin", "ops-07")
    cases.append(Case("http_auth", "bearer JWT in a curl header and a token in a JSON response", b.build(),
                      [Planted(jwt, "TOKEN"), Planted(opaque, "SECRET"), Planted(api, "HOST"),
                       Planted("maria.lopez", "USER"), Planted("acme-logistics.io", "HOST")],
                      keep=['curl -s -H "Authorization: Bearer ', "/v1/orders/42", '"status": "shipped"', '"expires_in": 3600']))

    # 7 ---------------------------------------------------------------
    v6 = "fe80::1c2b:3dff:fe4e:5f60"
    node = "worker-1.k8s.prod.wire-customer.lan"
    b = CastBuilder()
    b.cmd("frank", "edge-gw", "~", "ip addr show eth0")
    b.line("2: eth0: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc fq_codel state UP")
    b.line("    link/ether 52:54:00:12:34:56 brd ff:ff:ff:ff:ff:ff")
    b.line("    inet 192.168.1.23/24 brd 192.168.1.255 scope global eth0")
    b.line("    inet 127.0.0.1/8 scope host lo")
    b.line(f"    inet6 {v6}/64 scope link")
    b.cmd("frank", "edge-gw", "~", "ifconfig eth0 | grep netmask")
    b.line("        inet 192.168.1.23  netmask 255.255.255.0  broadcast 192.168.1.255")
    b.cmd("frank", "edge-gw", "~", "ping -c1 10.0.0.7")
    b.line("64 bytes from 10.0.0.7: icmp_seq=1 ttl=64 time=0.412 ms")
    b.cmd("frank", "edge-gw", "~", "kubectl get nodes -o wide")
    b.line("NAME                                  STATUS   INTERNAL-IP")
    b.line(f"{node}   Ready    172.16.8.21")
    b.prompt("frank", "edge-gw")
    cases.append(Case("network", "IPv4, IPv6 and internal hostnames in command output", b.build(),
                      [Planted("192.168.1.23", "IP"), Planted("192.168.1.255", "IP"), Planted(v6, "IP"),
                       Planted("10.0.0.7", "IP"), Planted("172.16.8.21", "IP"), Planted(node, "HOST"),
                       Planted("frank", "USER"), Planted("edge-gw", "HOST")],
                      keep=["127.0.0.1/8", "netmask 255.255.255.0", "ip addr show eth0", "icmp_seq=1 ttl=64 time=0.412 ms",
                            "kubectl get nodes -o wide", "mtu 1500"]))

    # 8 ---------------------------------------------------------------
    r = rng_for("git_https")
    pw = _password(r)
    b = CastBuilder()
    b.cmd("gina", "mbp", "~/site", "git push origin main")
    b.out("Username for 'https://git.acme-logistics.io': ").typed("jdoe")
    b.out("Password for 'https://jdoe@git.acme-logistics.io': ").secret_entry(pw)
    b.line("Everything up-to-date")
    b.prompt("gina", "mbp", "~/site")
    cases.append(Case("git_https_prompts", "username (echoed) and password (hidden) at git credential prompts", b.build(),
                      [Planted(pw, "PASSWORD"), Planted("jdoe", "USER"), Planted("git.acme-logistics.io", "HOST"),
                       Planted("gina", "USER"), Planted("mbp", "HOST")],
                      keep=["git push origin main", "Username for 'https://", "Everything up-to-date"]))

    # 9 ---------------------------------------------------------------
    b = CastBuilder(title="debugging the acme-billing deploy")
    b.cmd("hiro", "hiro-desktop", "/home/hiro/clients/acme-billing", "ls /home/hiro/clients/acme-billing/deploy")
    b.line("docker-compose.yml  nginx.conf  README.md")
    b.cmd("hiro", "hiro-desktop", "/home/hiro/clients/acme-billing", "scp nginx.conf /Users/hiro.t/backup/")
    b.cmd("hiro", "hiro-desktop", "/home/hiro/clients/acme-billing", r"type C:\Users\hiro.t\notes.txt")
    b.prompt("hiro", "hiro-desktop", "/home/hiro/clients/acme-billing")
    cases.append(Case("home_paths_and_terms", "usernames inside home paths, plus a customer name given as an extra term", b.build(),
                      [Planted("hiro", "USER"), Planted("hiro.t", "USER"), Planted("hiro-desktop", "HOST"),
                       Planted("acme-billing", "NAME")],
                      keep=["/home/", "/clients/", "docker-compose.yml  nginx.conf  README.md", "/backup/", r"\notes.txt"],
                      extra_terms=["acme-billing"]))

    # 10 --------------------------------------------------------------
    r = rng_for("negatives")
    sha = _r(r, "0123456789abcdef", 40)
    digest = _r(r, "0123456789abcdef", 64)
    uuid = "-".join(_r(r, "0123456789abcdef", n) for n in (8, 4, 4, 4, 12))
    b = CastBuilder()
    root = lambda c: b.cmd("root", "localhost", "/srv/app", c)  # noqa: E731
    root("git log --oneline -2")
    b.line(f"{sha[:7]} Fix nginx reload ordering").line("a1b2c3d Add healthcheck endpoint")
    root("git rev-parse HEAD")
    b.line(sha)
    root("docker ps")
    b.line("CONTAINER ID   IMAGE          COMMAND                  STATUS         PORTS")
    b.line('3f4e5d6c7b8a   nginx:1.25.3   "/docker-entrypoint.sh"  Up 2 hours     0.0.0.0:80->80/tcp')
    root("docker inspect --format '{{.Image}}' web")
    b.line(f"sha256:{digest}")
    root("cat /proc/sys/kernel/random/uuid")
    b.line(uuid)
    root("uname -r")
    b.line("5.15.0-91-generic")
    root("ls /usr/lib/x86_64-linux-gnu | head -1")
    b.line("libLLVM-15.so.1")
    root("kubectl get pods")
    b.line("NAME                     READY   STATUS    RESTARTS   AGE").line("api-7d9f8b6c5d-x2k4p     1/1     Running   0          3d4h")
    root("git remote -v")
    b.line("origin\thttps://github.com/copyleft-ai/asciinema-annotator.git (fetch)")
    root("curl -s localhost:8080/health")
    b.line('{"status":"ok","uptime":"3d4h12m"}')
    root("grep -n PasswordAuthentication /etc/ssh/sshd_config")
    b.line("57:PasswordAuthentication no")
    root("export DB_PASSWORD=$(cat /run/secrets/db_password)")
    root("head -1 /etc/passwd")
    b.line("root:x:0:0:root:/root:/bin/bash")
    root("python3 -c 'import sys; print(sys.version)'")
    b.line("3.10.12 (main, Nov 20 2023, 15:14:05) [GCC 11.4.0]")
    b.prompt("root", "localhost", "/srv/app", "#")
    negatives = [sha, f"{sha[:7]} Fix nginx reload ordering", f"sha256:{digest}", uuid, "5.15.0-91-generic",
                 "0.0.0.0:80->80/tcp", "nginx:1.25.3", "api-7d9f8b6c5d-x2k4p", "libLLVM-15.so.1",
                 "https://github.com/copyleft-ai/asciinema-annotator.git", "localhost:8080/health",
                 "57:PasswordAuthentication no", "export DB_PASSWORD=$(cat /run/secrets/db_password)",
                 "root:x:0:0:root:/root:/bin/bash", "15:14:05", "/proc/sys/kernel/random/uuid", "root@localhost:/srv/app#"]
    cases.append(Case("hard_negatives", "hashes, IDs, versions, paths and public URLs that must NOT be scrubbed", b.build(),
                      [], keep=negatives))
    return cases


def write_set(out_dir: Path, seed: int = SEED) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = []
    for c in build_cases(seed):
        (out_dir / f"{c.name}.cast").write_text(c.cast, encoding="utf-8")
        manifest.append({"name": c.name, "description": c.description, "file": f"{c.name}.cast",
                         "planted": [vars(p) for p in c.planted], "keep": c.keep, "extra_terms": c.extra_terms})
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python tests/planted_set.py OUT_DIR")
    write_set(Path(sys.argv[1]))
