"""Live event capture and asciinema import (KAN-26), scrubbed at capture time (KAN-35).

Design: docs/design/capture.md. Output format: contracts/events.py (schema v1).
"""

from .importer import import_cast, load_cast_tolerant, segment
from .session import Session

__all__ = ["Session", "import_cast", "load_cast_tolerant", "segment"]
