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


class MalformedTaxonomy(ValueError):
    """A taxonomy file that parses as JSON but is not a taxonomy.

    A `ValueError` so that `corpus.load_corpus`, which runs these lookups
    inside entry validation, normalises it to a `CorpusError` rather than
    letting a bare `KeyError` walk out of `hardy evals corpus check`.
    """


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


def _table(payload: dict, key: str, path: Path) -> dict:
    """One taxonomy table, checked to be a mapping of strings to strings.

    Dict-ness alone is not enough: a class mapped to a list makes `group_of`
    return a list, `corpus check` pass, and `corpus report` crash when
    `Counter` is handed something unhashable -- on a corpus the check just
    declared clean.
    """
    value = payload.get(key)
    if not isinstance(value, dict):
        raise MalformedTaxonomy(f"{path.name} has no {key!r} table")
    bad = sorted(
        repr(k) for k, v in value.items() if not isinstance(k, str) or not isinstance(v, str)
    )
    if bad:
        raise MalformedTaxonomy(
            f"{path.name}: {key!r} must map strings to strings; these do not: {', '.join(bad[:5])}"
        )
    return value


@cache
def _codes_at(root: Path) -> dict[str, str]:
    path = root / "taxonomy" / "msc2020.json"
    return _table(json.loads(path.read_text(encoding="utf-8")), "codes", path)


@cache
def _mapping_at(root: Path) -> dict[str, dict[str, str]]:
    path = root / "taxonomy" / "msc-to-arxiv.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    for name in ("arxiv", "fields", "groups"):
        _table(payload, name, path)
    return payload


def _codes() -> dict[str, str]:
    return _codes_at(_active)


def _mapping() -> dict[str, dict[str, str]]:
    return _mapping_at(_active)


def forget() -> None:
    """Drop the cached tables so the next lookup re-reads them.

    The caches are keyed by root and never expire, which is right for a CLI
    process that exits. A long-lived reader -- the viewer -- promises the
    corpus as it is on disk, and a cached taxonomy would quietly break that
    half of the promise: a code added to `msc2020.json` would keep being
    rejected until the server restarted.
    """
    _codes_at.cache_clear()
    _mapping_at.cache_clear()


def is_known(code: str) -> bool:
    return code in _codes()


def _lookup(table: dict[str, str], key: str, code: str) -> str:
    try:
        return table[key]
    except KeyError as exc:
        raise UnknownCode(code) from exc


def _rollup(table: dict[str, str], code: str) -> str:
    """The most specific entry that covers `code`, for a code that exists.

    Tried whole code, then section (`12L`), then class (`12`). MSC classes are
    not homogeneous under an arXiv reading -- MSC 12 alone spans math.NT
    (Galois theory), math.AC (valuation theory), math.RA (near-fields) and
    math.LO (model theory of fields) -- so a class-only table would file a
    third of a class under the wrong archive.

    Rolling up on the prefix without checking the code itself would report
    `13ZZZ` as valid commutative algebra: the prefix resolves and the invented
    tail is never looked at. `Entry` already rejects unknown codes, so this is
    what protects every caller that is not an `Entry` -- the editor, a report,
    a third party's script over the published corpus.
    """
    if not is_known(code):
        raise UnknownCode(code)
    for key in (code, code[:3], code[:2]):
        if key in table:
            return table[key]
    raise UnknownCode(code)


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
