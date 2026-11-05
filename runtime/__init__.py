"""Runtime interface: how every component calls a model (KAN-104).

Every model call in the pipeline goes through one small interface:

    model.complete(task, evidence_ids, prompt) -> str

* ``task``: what is asked ("annotate", "summarize", "answer", ...);
* ``evidence_ids``: the events the prompt is about (``session#event``),
  used for caching/replay, logging and citations;
* ``prompt``: the full text sent. It must already be scrubbed.

Implementations: ``fixtures.ReplayModel`` (tests), and later the local
quantized Model 1 behind FastAPI (KAN-50) and the local/cloud router
(KAN-9, owned by Danny), which wraps whatever backends exist.

Two wrappers every caller should use:

* ``Guarded(model)``: refuses any prompt in which the scrubber still finds
  something to replace (defence in depth for the KAN-36 "prompts" sink);
* ``Logged(model, log)``: records task, evidence ids, sizes and timing,
  never prompt or reply text.
"""

from __future__ import annotations

import re
import time
from typing import Callable, Protocol, runtime_checkable


@runtime_checkable
class Model(Protocol):
    def complete(self, task: str, evidence_ids: list[str], prompt: str) -> str: ...


class UnscrubbedPrompt(RuntimeError):
    """Raised instead of sending a prompt that still contains secrets or PII.
    The message names only the kinds found, never the values."""


class Guarded:
    def __init__(self, model: Model, scrubber_factory: Callable | None = None) -> None:
        from scrubber import Scrubber

        self.model = model
        self._new = scrubber_factory or Scrubber

    def complete(self, task: str, evidence_ids: list[str], prompt: str) -> str:
        s = self._new()
        # prompts embed events as JSON: scan what the text means (a backslash-n is a newline),
        # not its escaped spelling, so 'password: <backslash>nWelcome' isn't read as a password
        text = _json_unescape(prompt)
        clean = s.scrub_text(text)
        if clean != text:
            raise UnscrubbedPrompt(f"prompt for {task!r} still contains: {sorted(s.map.counts())}")
        return self.model.complete(task, evidence_ids, prompt)


_ESC = {'n': chr(10), 't': chr(9), 'r': chr(13), 'b': chr(8), 'f': chr(12), '"': '"', chr(92): chr(92), '/': '/'}
_JSON_ESCAPE = re.compile(chr(92) * 2 + r'(u[0-9a-fA-F]{4}|["' + chr(92) * 2 + r'/bfnrt])')


def _json_unescape(text: str) -> str:
    def sub(m: re.Match) -> str:
        c = m.group(1)
        return chr(int(c[1:], 16)) if c.startswith('u') else _ESC[c]

    return _JSON_ESCAPE.sub(sub, text)


class Logged:
    def __init__(self, model: Model, log: list | None = None) -> None:
        self.model = model
        self.log: list[dict] = log if log is not None else []

    def complete(self, task: str, evidence_ids: list[str], prompt: str) -> str:
        t0 = time.perf_counter()
        ok = False
        reply = ""
        try:
            reply = self.model.complete(task, evidence_ids, prompt)
            ok = True
            return reply
        finally:
            self.log.append({"task": task, "evidence": list(evidence_ids), "prompt_chars": len(prompt),
                             "reply_chars": len(reply), "ok": ok, "ms": round((time.perf_counter() - t0) * 1000, 2)})


__all__ = ["Guarded", "Logged", "Model", "UnscrubbedPrompt"]
