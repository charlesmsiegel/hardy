"""Registry and built-in construction for source readers.

Pure reader contracts live in `adapter_contracts`; they are re-exported here for
existing callers. Concrete readers depend on those contracts rather than this
registry. Their optional PDF SDK is still loaded only when extraction asks for it.
"""
from __future__ import annotations

from .adapter_contracts import AdapterUnavailable as AdapterUnavailable
from .adapter_contracts import ExtractionBudget as ExtractionBudget
from .adapter_contracts import ExtractionRefused as ExtractionRefused
from .adapter_contracts import ExtractionResult as ExtractionResult
from .adapter_contracts import SourceAdapter as SourceAdapter
from .contracts import SourceArtifact, SourceFormat
from .epub import EpubAdapter
from .pdf import PdfAdapter
from .tex import TexAdapter
from .text import TextAdapter


class AdapterRegistry:
    """Choose the first registered reader that declares support for an artifact."""

    def __init__(self, adapters: tuple[SourceAdapter, ...]) -> None:
        self._adapters = adapters

    def for_artifact(self, artifact: SourceArtifact) -> SourceAdapter | None:
        """Find a declared format match without reading the artifact's bytes."""
        for adapter in self._adapters:
            if adapter.handles(artifact):
                return adapter
        return None

    def formats(self) -> tuple[SourceFormat, ...]:
        """Distinct declared format support, in registry and format-enumeration order."""
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
    """Construct the built-in registry without loading an optional parser SDK."""
    return AdapterRegistry((PdfAdapter(), TexAdapter(), EpubAdapter(), TextAdapter()))
