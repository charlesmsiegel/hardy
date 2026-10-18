"""Statement vocabularies, with inventory and structural reading kept distinct.

The TeX inventory lists assertions that may be proposed as assumptions; listing
one never admits it. A source tree also navigates remarks, exercises and other
units that are not assertions. These are different reading policies, so sharing
their vocabulary must not silently widen either policy. Abbreviations have one
meaning across both readers; printed text recognition remains English-only.
"""
from __future__ import annotations

from types import MappingProxyType

INVENTORY_KINDS = (
    "theorem", "lemma", "proposition", "corollary", "definition", "conjecture",
    "claim", "fact", "assumption", "hypothesis",
)
STRUCTURAL_KINDS = (
    "theorem", "lemma", "proposition", "corollary", "definition", "claim", "conjecture",
    "example", "exercise", "remark", "construction", "solution",
)
# Numbered prose references include equations but do not recognize construction
# or solution labels. Keep that lexical policy separate from tree membership.
REFERENCE_KINDS = (
    "theorem", "lemma", "proposition", "corollary", "definition", "claim", "conjecture",
    "example", "exercise", "remark", "equation",
)
ENVIRONMENT_ALIASES = MappingProxyType({
    "thm": "theorem", "lem": "lemma", "prop": "proposition", "cor": "corollary",
    "defn": "definition", "defi": "definition", "conj": "conjecture",
})
