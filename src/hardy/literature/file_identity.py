"""Reliable local file change tokens for verified literature cache reuse."""
from __future__ import annotations

import os
import sys
from pathlib import Path

WINDOWS = sys.platform == 'win32'


def change_token(path: Path, info: os.stat_result) -> int | None:
    """A change clock, or None when verified bytes must not be cached."""
    if WINDOWS:
        # Windows ctime is creation time on supported Python versions. NT
        # ChangeTime can also stay equal across a same-size overwrite with
        # restored mtime, so neither proves that the bytes are unchanged.
        return None
    return info.st_ctime_ns
