"""Benchmark and eval harness (1.1.7, KAN-62). See docs/design/benchmark.md."""

from .metrics import RUBRIC, descriptions, rubric, segmentation, tagging
from .runner import DEFAULT_RULES, run

__all__ = ["DEFAULT_RULES", "RUBRIC", "descriptions", "rubric", "run", "segmentation", "tagging"]
