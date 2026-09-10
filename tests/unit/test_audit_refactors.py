"""Characterize the byte and token contracts shared during audit cleanup."""

import hashlib

import pytest

from hardy.formal.contracts import EnvironmentIdentity, VerificationEvidence
from hardy.formal.retrieval import RetrievalProvenance, premises_digest
from hardy.workflows.admission import _split_top, _split_top_before


def test_verification_digest_preserves_utf8_and_canonical_bytes():
    evidence = VerificationEvidence(
        claim_sha256="claim", source_sha256="source", axioms=("α", "β"),
        toolchain=EnvironmentIdentity(
            lean_version="4", lean_commit="lean", mathlib_revision="mathlib",
            lake_manifest_sha256="manifest", imports=("Mathlib",),
        ),
    )
    canonical = (
        '{"axioms":["α","β"],"claim_sha256":"claim","source_sha256":"source",'
        '"toolchain":{"imports":["Mathlib"],"lake_manifest_sha256":"manifest",'
        '"lean_commit":"lean","lean_version":"4","mathlib_revision":"mathlib"}}'
    )
    assert evidence.digest == hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def test_retrieval_digest_preserves_its_existing_bytes():
    provenance = RetrievalProvenance(
        goal_sha256="goal", query_sha256="query", premises_sha256="premises",
        budget_seconds=30, prior_seconds_spent=1.5, ranker="α", sources=(),
    )
    canonical = (
        '{"budget_seconds":30,"goal_sha256":"goal","premises_sha256":"premises",'
        '"prior_seconds_spent":1.5,"query_sha256":"query","ranker":"α","sources":[]}'
    )
    assert provenance.digest == hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    assert premises_digest(()) == hashlib.sha256(b"[]").hexdigest()


@pytest.mark.parametrize(
    ("text", "separator", "limit", "expected"),
    [
        ("", ", ", 0, [""]),
        ("a, b, c", ", ", 0, ["a, b, c"]),
        ("a, b, c", ", ", 1, ["a, b, c"]),
        ("a, b, c", ", ", 2, ["a", "b, c"]),
        ("a, b, c", ", ", 7, ["a", "b", "c"]),
        ("(a, b), [c, d], e", ", ", 16, ["(a, b)", "[c, d]", "e"]),
        ("P → ∃ f : α → Prop, Q f", " → ", 4, ["P", "∃ f : α → Prop, Q f"]),
    ],
)
def test_bounded_split_preserves_the_unsplit_tail(text, separator, limit, expected):
    assert _split_top_before(text, separator, limit) == expected


def test_unbounded_split_keeps_bracketed_separators():
    assert _split_top("(a, b), [c, d], e", ", ") == ["(a, b)", "[c, d]", "e"]
