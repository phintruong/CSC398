"""Capture-time scrubbing of secrets and PII (KAN-4 / 1.2.1).

    from scrubber import scrub_cast_text, Scrubber
    clean_cast = scrub_cast_text(open("session.cast").read(), source="julia-laptop", consent="yes", license="CC-BY-SA-4.0")
    clean_diff = Scrubber().scrub_text(diff_text)

See docs/design/scrubbing.md for what is scrubbed and why.
"""

from .cast import Recording, read_cast, scrub_cast_text, scrub_recording, write_cast
from .core import REDACTION_VERSION, PlaceholderMap, Scrubber

__all__ = [
    "REDACTION_VERSION",
    "PlaceholderMap",
    "Recording",
    "Scrubber",
    "read_cast",
    "scrub_cast_text",
    "scrub_recording",
    "write_cast",
]
