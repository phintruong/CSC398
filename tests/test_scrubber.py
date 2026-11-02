"""Unit tests for the scrubber's building blocks."""

from __future__ import annotations

import json

from scrubber import PlaceholderMap, Scrubber, read_cast, scrub_cast_text, scrub_recording
from scrubber.cast import typed_value
from scrubber.detect import detect_ips, looks_random, strip_controls


def _cast(*events) -> str:
    header = {"version": 2, "width": 80, "height": 24}
    return "\n".join(json.dumps(x) for x in [header, *[[i * 0.1, c, d] for i, (c, d) in enumerate(events)]]) + "\n"


def test_same_value_same_placeholder():
    m = PlaceholderMap()
    assert m.get("IP", "10.1.1.1") == "<IP_1>"
    assert m.get("IP", "10.1.1.2") == "<IP_2>"
    assert m.get("IP", "10.1.1.1") == "<IP_1>"
    assert m.counts() == {"IP": 2}


def test_scrub_text_keeps_structure():
    out = Scrubber().scrub_text("ping 10.9.8.7 && ssh ops@10.9.8.7")
    assert out == "ping <IP_1> && ssh <USER_1>@<IP_1>"


def test_mapping_shared_across_texts():
    s = Scrubber()
    a, b = s.scrub_texts(["host 10.2.2.2 up", "connect to 10.2.2.2"])
    assert a == "host <IP_1> up" and b == "connect to <IP_1>"


def test_strip_controls_index_maps_back():
    raw = "\x1b[01;32mab\x1b[0mc"
    clean, idx = strip_controls(raw)
    assert clean == "abc"
    assert [raw[i] for i in idx] == list("abc")


def test_secret_inside_colour_codes_is_found():
    out = Scrubber().scrub_text("\x1b[32m10.0.\x1b[0m0.9 ok")
    assert "10.0." not in out and "0.9 ok" not in out.replace("<IP_1>", "")


def test_secret_split_across_events():
    text = _cast(("o", "token=Ab3dE"), ("o", "fGh1jKlm"), ("o", "Np\r\nok\r\n"))
    rec = read_cast(scrub_cast_text(text))
    data = [e[2] for e in rec.events]
    assert data[0] == "token=<SECRET_1>"
    assert data[1] == ""
    assert data[2] == "\r\nok\r\n"


def test_backspaces_in_typed_password():
    assert typed_value("hunx\x7fter2") == "hunter2"
    assert typed_value("\x1b[Dab") == "ab"


def test_password_prompt_capture_and_reuse():
    text = _cast(("o", "Password: "), ("i", "s"), ("i", "3"), ("i", "cr"), ("i", "et9\r"),
                 ("o", "\r\n$ "), ("o", "echo s3cret9\r\ns3cret9\r\n"))
    rec, s = scrub_recording(read_cast(text))
    joined = "".join(e[2] for e in rec.events)
    assert "s3cret9" not in joined
    assert s.map.lookup("s3cret9") == "<PASSWORD_1>"
    assert [e[2] for e in rec.events[1:5]] == ["<PASSWORD_1>", "", "", "\r"]


def test_input_without_prompt_not_captured():
    rec, s = scrub_recording(read_cast(_cast(("o", "$ "), ("i", "ls -la\r"))))
    assert s.map.counts() == {}


def test_header_title_and_markers_scrubbed():
    text = _cast(("o", "x"), ("m", "fixed 10.3.3.3"))
    obj = [json.loads(l) for l in text.splitlines()]
    obj[0]["title"] = "debug on 10.3.3.3"
    rec = read_cast(scrub_cast_text("\n".join(json.dumps(o) for o in obj)))
    assert rec.header["title"] == "debug on <IP_1>"
    assert rec.events[1][2] == "fixed <IP_1>"


def test_ips_allowlist():
    vals = [f.value for f in detect_ips("127.0.0.1 0.0.0.0 255.255.0.0 ::1 10.0.0.1 2001:db8::5 12:30:45")]
    assert vals == ["10.0.0.1", "2001:db8::5"]


def test_entropy_heuristic():
    assert looks_random("q8ZrT2vLx9WmA4kPf7Bn")
    assert not looks_random("e83c5163316f89bfbde7d9ab23ca2e25604af290")  # git sha
    assert not looks_random("MyApplicationService")  # no digit, low entropy
    assert not looks_random("/usr/lib/Python3/site-packages")


def test_v3_header_accepted():
    text = '{"version": 3, "term": {"cols": 80, "rows": 24}}\n[0.1, "o", "ip 10.4.4.4"]\n'
    assert "<IP_1>" in scrub_cast_text(text)
