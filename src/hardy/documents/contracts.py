"""Document compilation and informal-review statuses."""
from __future__ import annotations

from enum import Enum


class InformalStatus(str, Enum):
    NO_GAPS_DETECTED = "no_gaps_detected"
    KNOWN_GAPS = "known_gaps"
    NOT_INDEPENDENTLY_ASSESSED = "not_independently_assessed"


class DocumentStatus(str, Enum):
    TEX_COMPILED = "tex_compiled"
    TEX_FAILED = "tex_failed"
    NOT_ATTEMPTED = "not_attempted"
