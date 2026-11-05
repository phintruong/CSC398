"""File-edit detection (#50, KAN-30). Design: docs/design/file-edit-detection.md.

    targets = targets_for_command("sudo vim /etc/hosts", cwd)   # preexec
    before = Snapshot.take(targets)
    ... command runs ...
    events = before.diff(Snapshot.take(targets), t=1.0, t_end=9.5, command=cmd)  # precmd

Diffs are always scrubbed before they are returned unless ``raw=True`` is
passed, which only the capture spool uses (it scrubs the whole session at
stop time with one placeholder map).
"""

from .detect import is_config, targets_for_command
from .snapshot import MAX_BYTES, FileState, Snapshot

__all__ = ["MAX_BYTES", "FileState", "Snapshot", "is_config", "targets_for_command"]
