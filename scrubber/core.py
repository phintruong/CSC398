"""Placeholder assignment, overlap resolution and text rewriting."""

from __future__ import annotations

import re
from bisect import bisect_right, insort
from typing import Iterable, Sequence

from .detect import HOST, IP, NAME, SECRET_KINDS, USER, Finding, detect_all, strip_controls

REDACTION_VERSION = "scrub-0.2.0"  # 0.2: a shared PlaceholderMap spreads already-learned values

# Kinds whose values, once learned anywhere, are replaced everywhere they occur.
_PROPAGATE_MIN_LEN = 3
_WORDY_KINDS = frozenset({USER, HOST, NAME, IP})


class PlaceholderMap:
    """Same original value -> same placeholder, numbered per kind.

    The map holds the original values, so it lives in memory only. It is
    never written to disk, logged or sent anywhere.
    """

    def __init__(self) -> None:
        self._by_value: dict[str, str] = {}
        self._counts: dict[str, int] = {}

    def get(self, kind: str, value: str) -> str:
        ph = self._by_value.get(value)
        if ph is None:
            n = self._counts.get(kind, 0) + 1
            self._counts[kind] = n
            ph = f"<{kind}_{n}>"
            self._by_value[value] = ph
        return ph

    def lookup(self, value: str) -> str | None:
        return self._by_value.get(value)

    def known(self) -> list[tuple[str, str]]:
        """(kind, value) for every value already replaced. In memory only."""
        return [(ph[1:].rsplit("_", 1)[0], v) for v, ph in self._by_value.items()]

    def counts(self) -> dict[str, int]:
        """Distinct values replaced, per kind. Safe to store and log."""
        return dict(sorted(self._counts.items()))


Span = tuple[int, int, str, str]  # (start, end, kind, value) in raw coordinates


def _to_raw(f: Finding, index: list[int]) -> Finding:
    return Finding(index[f.start], index[f.end - 1] + 1, f.kind, f.value, f.priority)


def _resolve(findings: Iterable[Finding]) -> list[Finding]:
    """Keep the best non-overlapping findings: lowest priority number, then longest."""
    ranked = sorted(findings, key=lambda f: (f.priority, -(f.end - f.start), f.start))
    starts: list[int] = []
    kept: dict[int, Finding] = {}
    for f in ranked:
        if f.end <= f.start:
            continue
        i = bisect_right(starts, f.start)
        if i > 0 and kept[starts[i - 1]].end > f.start:
            continue
        if i < len(starts) and starts[i] < f.end:
            continue
        insort(starts, f.start)
        kept[f.start] = f
    return [kept[s] for s in starts]


class Scrubber:
    def __init__(self, extra_terms: Sequence[str] = (), mapping: PlaceholderMap | None = None) -> None:
        self.map = mapping or PlaceholderMap()
        self.extra_terms = [t for t in extra_terms if t and t.strip()]

    # -- detection --------------------------------------------------------

    def _detect(self, raw: str) -> list[Finding]:
        clean, index = strip_controls(raw)
        found = [_to_raw(f, index) for f in detect_all(clean)]
        for term in self.extra_terms:
            for m in re.finditer(r"(?i)(?<![A-Za-z0-9])" + re.escape(term) + r"(?![A-Za-z0-9])", raw):
                found.append(Finding(m.start(), m.end(), NAME, term, 1))
        return found

    @staticmethod
    def _propagate(raw: str, learned: dict[str, Finding]) -> list[Finding]:
        """Find every literal occurrence of a value learned anywhere in the recording."""
        out = []
        for value, f in learned.items():
            pat = re.escape(value)
            if f.kind in _WORDY_KINDS:
                pat = r"(?<![A-Za-z0-9_.-])" + pat + r"(?![A-Za-z0-9_-])"
            prio = 0 if f.kind in SECRET_KINDS else f.priority
            for m in re.finditer(pat, raw):
                out.append(Finding(m.start(), m.end(), f.kind, value, prio))
        return out

    def find_spans(self, texts: Sequence[str], extra: Sequence[Sequence[Finding]] = ()) -> list[list[Span]]:
        """Detect across several related texts (e.g. the output and input
        streams of one recording) so a value learned in one is replaced in all.
        ``extra`` adds findings per text, e.g. typed passwords."""
        per_text = [self._detect(t) + list(extra[i] if i < len(extra) else ()) for i, t in enumerate(texts)]

        learned: dict[str, Finding] = {}
        # Values already replaced earlier with this map (e.g. a password typed
        # at a prompt in the .cast) are replaced here too, so a shared map
        # keeps diffs and hook events consistent with their recording.
        for kind, v in self.map.known():
            if len(v) >= _PROPAGATE_MIN_LEN and "\n" not in v:
                learned[v] = Finding(0, 0, kind, v, 0 if kind in SECRET_KINDS else 2)
        for f in sorted((f for fs in per_text for f in fs), key=lambda f: f.priority):
            v = f.value
            if len(v) >= _PROPAGATE_MIN_LEN and v not in learned and "\n" not in v:
                learned[v] = f

        result = []
        for raw, found in zip(texts, per_text):
            kept = _resolve(found + self._propagate(raw, learned))
            result.append([(f.start, f.end, f.kind, f.value) for f in kept])
        return result

    # -- rewriting --------------------------------------------------------

    def rewrite(self, text: str, bounds: Sequence[tuple[int, int]], spans: Sequence[Span]) -> list[str]:
        """Apply spans to ``text`` and split the result back into pieces.

        ``bounds`` are the (start, end) offsets of each original piece
        (e.g. each asciicast event) inside ``text``. A placeholder goes into
        the piece where its secret started, and the rest of the secret is
        removed from the following pieces, so a secret split across events
        is still fully replaced.
        """
        starts = [s for s, _ in bounds]
        pieces: list[list[str]] = [[] for _ in bounds]

        def piece_of(pos: int) -> int:
            return max(bisect_right(starts, pos) - 1, 0)

        def emit(a: int, b: int) -> None:
            while a < b:
                # the last piece starting at or before a always contains a
                # (empty pieces share their start with the next piece)
                k = piece_of(a)
                stop = min(b, bounds[k][1])
                pieces[k].append(text[a:stop])
                a = stop

        pos = 0
        for a, b, kind, value in spans:
            emit(pos, a)
            pieces[piece_of(a)].append(self.map.get(kind, value))
            pos = b
        emit(pos, len(text))
        return ["".join(p) for p in pieces]

    def scrub_texts(self, texts: Sequence[str]) -> list[str]:
        spans = self.find_spans(texts)
        return [self.rewrite(t, [(0, len(t))], s)[0] for t, s in zip(texts, spans)]

    def scrub_text(self, text: str) -> str:
        """Scrub one string: a diff, a prompt for a model, a log line."""
        return self.scrub_texts([text])[0]
