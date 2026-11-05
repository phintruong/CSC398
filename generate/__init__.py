"""Automation scripts and Dockerfiles from canonical procedures (KAN-47, KAN-75). See docs/design/automation.md."""

from .core import to_dockerfile, to_script, variables

__all__ = ["to_dockerfile", "to_script", "variables"]
