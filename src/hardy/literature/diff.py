"""Mechanical correspondences between explicit manuscript source versions.

Exact text can move while its surrounding notation changes. This owner keeps
whole-file source identities and proposes text matches only; a workflow's named
semantic reader must decide whether mathematics or dependencies changed. It
does not read files, expand TeX or revise any mathematical record.
"""
from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal

from hardy.literature.manuscript import Inventory, SourceSpan, inventory


@dataclass(frozen=True)
class ManuscriptDiff:
    before: Mapping[str, str]
    after: Mapping[str, str]
    before_inventory: Inventory
    after_inventory: Inventory
    changed_files: tuple[str, ...]
    added_files: tuple[str, ...]
    removed_files: tuple[str, ...]


@dataclass(frozen=True)
class SpanCorrespondence:
    before: SourceSpan
    after: SourceSpan | None
    status: Literal["exact_text", "ambiguous", "unmatched"]
    candidates: tuple[SourceSpan, ...] = ()
    semantic_equivalence: Literal[False] = False


def compare_sources(before: Mapping[str, str], after: Mapping[str, str], *,
                    max_bytes: int = 2 * 1024 * 1024, max_files: int = 128) -> ManuscriptDiff:
    """Copy and inventory bounded source maps before proposing correspondences."""
    if any(isinstance(n, bool) or not isinstance(n, int) or n < 1 for n in (max_bytes, max_files)):
        raise ValueError("source limits must be positive integers")
    copies = [dict(before), dict(after)]
    for source in copies:
        if any(not isinstance(path, str) or not path or not isinstance(text, str)
               for path, text in source.items()):
            raise ValueError("source maps require nonempty paths and text values")
        if len(source) > max_files or sum(len(text.encode("utf-8")) for text in source.values()) > max_bytes:
            raise ValueError("manuscript source limit exceeded")
    old, new = copies
    return ManuscriptDiff(
        MappingProxyType(old), MappingProxyType(new), inventory(old), inventory(new),
        tuple(sorted(path for path in old.keys() & new.keys() if old[path] != new[path])),
        tuple(sorted(new.keys() - old.keys())), tuple(sorted(old.keys() - new.keys())),
    )


def map_span(diff: ManuscriptDiff, span: SourceSpan, *, max_matches: int = 32) -> SpanCorrespondence:
    """Propose a unique identical substring; ambiguous occurrences stay explicit.

    Even an exact position in an unchanged file is not semantic evidence about
    its imports, definitions or surrounding project. Match count is bounded;
    reaching the bound still yields ambiguity and never picks an arbitrary hit.
    """
    if isinstance(max_matches, bool) or not isinstance(max_matches, int) or max_matches < 2:
        raise ValueError("match limit must be an integer of at least two")
    old = diff.before.get(span.path)
    if old is None or not span.valid_for(span.path, old) or span.start == span.end:
        raise ValueError("span does not identify a nonempty original source slice")
    fragment = old[span.start:span.end]
    candidates = []
    for path, text in sorted(diff.after.items()):
        start = 0
        while len(candidates) < max_matches:
            index = text.find(fragment, start)
            if index < 0:
                break
            candidates.append(SourceSpan(path, hashlib.sha256(text.encode("utf-8")).hexdigest(),
                                         index, index + len(fragment)))
            start = index + 1
        if len(candidates) == max_matches:
            break
    if len(candidates) == 1:
        return SpanCorrespondence(span, candidates[0], "exact_text", tuple(candidates))
    return SpanCorrespondence(span, None, "ambiguous" if candidates else "unmatched", tuple(candidates))
