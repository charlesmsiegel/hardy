"""MSC2020 lookups, read from the corpus's own vendored tables.

The tables live under `corpus/taxonomy/` rather than beside this module: they
are corpus data a third party gets when they take the dataset, not Hardy
configuration (spec §1).
"""
from __future__ import annotations

import json
from functools import cache
from pathlib import Path

CORPUS = Path(__file__).resolve().parents[3] / "corpus"


class UnknownCode(KeyError):
    """A code absent from the vendored MSC2020 table."""


@cache
def _codes() -> dict[str, str]:
    return json.loads((CORPUS / "taxonomy" / "msc2020.json").read_text(encoding="utf-8"))["codes"]


@cache
def _mapping() -> dict[str, dict[str, str]]:
    return json.loads((CORPUS / "taxonomy" / "msc-to-arxiv.json").read_text(encoding="utf-8"))


def is_known(code: str) -> bool:
    return code in _codes()


def _lookup(table: dict[str, str], key: str, code: str) -> str:
    try:
        return table[key]
    except KeyError as exc:
        raise UnknownCode(code) from exc


def name_of(code: str) -> str:
    """The MSC2020 name of the full code -- what §12.1's reviewer actually reads.

    A reviewer cannot check `13A15`; they can check "Ideals and multiplicative
    ideal theory". Since the `review` record binds the classification, the
    editor has to show something checkable.
    """
    return _lookup(_codes(), code, code)


def field_of(code: str) -> str:
    """The 2-digit class's human label."""
    return _lookup(_mapping()["fields"], code[:2], code)


def group_of(code: str) -> str:
    """The reporting group: a versioned many-to-one map over 2-digit classes.

    Exists apart from `field_of` because the planned fields are not the 2-digit
    classes -- "real analysis and measure" is MSC 26 *and* 28 (spec §5).
    """
    return _lookup(_mapping()["groups"], code[:2], code)


def arxiv_of(code: str) -> str:
    return _lookup(_mapping()["arxiv"], code[:2], code)


@cache
def arxiv_classes() -> frozenset[str]:
    """The mapping's codomain -- what an `arxiv_override` may name."""
    return frozenset(_mapping()["arxiv"].values())
