"""MSC2020 lookups, read from the corpus's own vendored tables.

The tables live under `corpus/taxonomy/` rather than beside this module: they
are corpus data a third party gets when they take the dataset, not Hardy
configuration (spec §1).
"""
from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from functools import cache
from pathlib import Path

CORPUS = Path(__file__).resolve().parents[3] / "corpus"

# Which corpus these lookups read. `--corpus` may name an extracted release or
# a newer checkout, and `manifest_digest` binds *that* corpus's taxonomy files
# -- so validating its entries against Hardy's own vendored tables would
# reject codes it carries, accept codes it removed, and report groups from a
# different mapping. `load_corpus` scopes every lookup to the root it reads.
_active = CORPUS


class UnknownCode(KeyError):
    """A code absent from the vendored MSC2020 table."""


@contextmanager
def using(root: Path) -> Iterator[None]:
    """Resolve lookups against `root`'s tables for the duration of the block.

    A module-level root rather than a parameter threaded through every call
    because `Entry`'s own validators do the lookups, and a pydantic validator
    has nowhere to take a corpus from. One corpus at a time per process is the
    real constraint, so this states it rather than pretending otherwise.
    """
    global _active
    previous, _active = _active, Path(root)
    try:
        yield
    finally:
        _active = previous


@cache
def _codes_at(root: Path) -> dict[str, str]:
    return json.loads((root / "taxonomy" / "msc2020.json").read_text(encoding="utf-8"))["codes"]


@cache
def _mapping_at(root: Path) -> dict[str, dict[str, str]]:
    return json.loads((root / "taxonomy" / "msc-to-arxiv.json").read_text(encoding="utf-8"))


def _codes() -> dict[str, str]:
    return _codes_at(_active)


def _mapping() -> dict[str, dict[str, str]]:
    return _mapping_at(_active)


def is_known(code: str) -> bool:
    return code in _codes()


def _lookup(table: dict[str, str], key: str, code: str) -> str:
    try:
        return table[key]
    except KeyError as exc:
        raise UnknownCode(code) from exc


def _rollup(table: dict[str, str], code: str) -> str:
    """A 2-digit roll-up, but only for a code that exists.

    Rolling up on the prefix alone would report `13ZZZ` as valid commutative
    algebra: the prefix resolves and the invented tail is never looked at.
    `Entry` already rejects unknown codes, so this is what protects every
    caller that is not an `Entry` -- the editor, a report, a third party's
    script over the published corpus.
    """
    if not is_known(code):
        raise UnknownCode(code)
    return _lookup(table, code[:2], code)


def name_of(code: str) -> str:
    """The MSC2020 name of the full code -- what §12.1's reviewer actually reads.

    A reviewer cannot check `13A15`; they can check "Ideals and multiplicative
    ideal theory". Since the `review` record binds the classification, the
    editor has to show something checkable.
    """
    return _lookup(_codes(), code, code)


def field_of(code: str) -> str:
    """The 2-digit class's human label."""
    return _rollup(_mapping()["fields"], code)


def group_of(code: str) -> str:
    """The reporting group: a versioned many-to-one map over 2-digit classes.

    Exists apart from `field_of` because the planned fields are not the 2-digit
    classes -- "real analysis and measure" is MSC 26 *and* 28 (spec §5).
    """
    return _rollup(_mapping()["groups"], code)


def arxiv_of(code: str) -> str:
    return _rollup(_mapping()["arxiv"], code)


def arxiv_classes() -> frozenset[str]:
    """The mapping's codomain -- what an `arxiv_override` may name."""
    return frozenset(_mapping()["arxiv"].values())
