"""Format-reader evidence contracts, independent of concrete readers and construction.

Readers produce attributable representations, mappings, observations and metadata.
They do not mint mathematical claims or decide bibliographic identity. This module
contains only the pass values, refusal types and protocol, so a new format can
depend on that contract without importing the registry that constructs formats.
The historical imports from `sources.adapters` remain available through re-exports.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from .contracts import (
    DerivedRepresentation,
    Diagnostic,
    RepresentationMapping,
    SourceArtifact,
    StructuralObservation,
)


class AdapterUnavailable(RuntimeError):
    """The adapter's reader is not installed on this machine; nothing was read."""


class ExtractionRefused(ValueError):
    """The input exceeded a bound or could not be read as its format at all."""


@dataclass(frozen=True)
class ExtractionBudget:
    """Caller ceilings on pages, retained text and native fragment counts."""

    max_pages: int = 5_000
    max_text_bytes: int = 64 * 1024 * 1024
    max_fragments: int = 2_000_000


@dataclass(frozen=True)
class ExtractionResult:
    """One adapter pass's readings and evidence, including disclosed extraction gaps."""

    representations: tuple[tuple[DerivedRepresentation, Mapping[str, bytes]], ...]
    mappings: tuple[RepresentationMapping, ...] = ()
    observations: tuple[StructuralObservation, ...] = ()
    metadata: tuple[tuple[str, str], ...] = ()
    diagnostics: tuple[Diagnostic, ...] = ()


class SourceAdapter(Protocol):
    """A format reader supplies attributable evidence without deciding identity or claims."""

    name: str
    version: str

    def handles(self, artifact: SourceArtifact) -> bool: ...

    def extract(self, artifact: SourceArtifact, data: bytes, *, budget: ExtractionBudget) -> ExtractionResult: ...


