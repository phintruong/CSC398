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
        clean = s.scrub_text(prompt)
        if clean != prompt:
            raise UnscrubbedPrompt(f"prompt for {task!r} still contains: {sorted(s.map.counts())}")
        return self.model.complete(task, evidence_ids, prompt)


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
