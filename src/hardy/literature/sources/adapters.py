"""The contract every source format adapter implements.

An adapter turns one exact artifact's bytes into attributable representations,
explicit mappings between them, native structural observations and metadata
assertions. It never mints claims, never decides bibliographic identity, and
never executes anything it reads. Adding a format means adding an adapter
behind this protocol; nothing downstream of the representation store learns
what format produced a span.
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
    SourceFormat,
    StructuralObservation,
)


class AdapterUnavailable(RuntimeError):
    """The adapter's reader is not installed on this machine; nothing was read."""


class ExtractionRefused(ValueError):
    """The input exceeded a bound or could not be read as its format at all."""


@dataclass(frozen=True)
class ExtractionBudget:
    max_pages: int = 5_000
    max_text_bytes: int = 64 * 1024 * 1024
    max_fragments: int = 2_000_000


@dataclass(frozen=True)
class ExtractionResult:
    representations: tuple[tuple[DerivedRepresentation, Mapping[str, bytes]], ...]
    mappings: tuple[RepresentationMapping, ...] = ()
    observations: tuple[StructuralObservation, ...] = ()
    metadata: tuple[tuple[str, str], ...] = ()
    diagnostics: tuple[Diagnostic, ...] = ()


class SourceAdapter(Protocol):
    name: str
    version: str

    def handles(self, artifact: SourceArtifact) -> bool: ...

    def extract(self, artifact: SourceArtifact, data: bytes, *, budget: ExtractionBudget) -> ExtractionResult: ...


class AdapterRegistry:
    def __init__(self, adapters: tuple[SourceAdapter, ...]) -> None:
        self._adapters = adapters

    def for_artifact(self, artifact: SourceArtifact) -> SourceAdapter | None:
        for adapter in self._adapters:
            if adapter.handles(artifact):
                return adapter
        return None

    def formats(self) -> tuple[SourceFormat, ...]:
        found: list[SourceFormat] = []
        for adapter in self._adapters:
            for format_ in SourceFormat:
                probe = SourceArtifact(
                    sha256="0" * 64, byte_size=0, format=format_, media_type="application/octet-stream",
                    access="private_local", admitted_at="1970-01-01T00:00:00+00:00",
                )
                if adapter.handles(probe) and format_ not in found:
                    found.append(format_)
        return tuple(found)


def default_adapters() -> AdapterRegistry:
    from .epub import EpubAdapter
    from .pdf import PdfAdapter
    from .tex import TexAdapter

    return AdapterRegistry((PdfAdapter(), TexAdapter(), EpubAdapter()))
