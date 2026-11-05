"""Streaming annotation (1.1.4, KAN-41/42/43). See docs/design/streaming-annotation.md."""

from .annotator import GoalTree, StreamingAnnotator, annotate_session, build_prompt, parse_labels

__all__ = ["GoalTree", "StreamingAnnotator", "annotate_session", "build_prompt", "parse_labels"]
