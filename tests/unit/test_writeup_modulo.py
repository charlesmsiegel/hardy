"""A verified-modulo document says so, and says what it stands on."""

from __future__ import annotations

import importlib
import re
from datetime import UTC, datetime
from uuid import UUID

NOW = datetime(2026, 7, 24, tzinfo=UTC)
RUN_ID = UUID("12345678-1234-5678-1234-567812345678")


def _pieces(domain, writeup, assumed=()):
    proposal = domain.FormalizationProposal(
        restatement="Two equals two.",
        domains=(),
        quantifiers=(),
        assumptions=(),
        interpretation_choices=(),
        theorem_name="two_eq_two",
        binders="",
        proposition="2 = 2",
    )
    environment = domain.EnvironmentIdentity(
        lean_version="4.32.0",
        lean_commit="8c9756b",
        mathlib_revision="81a5d257",
        lake_manifest_sha256="b" * 64,
        imports=("Mathlib",),
    )
    claim = domain.freeze_claim("Two equals two.", proposal, environment, NOW)
    evidence = domain.VerificationEvidence(
        claim_sha256=claim.content_hash,
        source_sha256="a" * 64,
        axioms=("propext", *assumed),
        toolchain=environment,
    )
    review = domain.FaithfulnessVerdict(
        claim_sha256=claim.content_hash,
        reviewer_model="reader",
        prompt_sha256="c" * 64,
        response_schema_sha256="d" * 64,
        outcome=domain.FaithfulnessOutcome.AGREED,
        review=domain.FaithfulnessReview(
            formalization_entails_claim=True, claim_entails_formalization=True
        ),
    )
    grades = domain.Grades(
        formal=(
            domain.FormalStatus.VERIFIED_MODULO
            if assumed
            else domain.FormalStatus.KERNEL_VERIFIED
        ),
        faithfulness=domain.FaithfulnessStatus.USER_APPROVED,
        faithfulness_review=review,
        verification_sha256=evidence.digest,
        verification_evidence=evidence,
        assumed=tuple(assumed),
    )
    content = writeup.WriteupContent(
        title="Two equals two",
        theorem_text="Two equals two.",
        proof_text="Reflexivity.",
        known_gaps=(),
    )
    return claim, grades, content, evidence


def _render(assumed=(), declared=()):
    domain = importlib.import_module("hardy.domain")
    verifier = importlib.import_module("hardy.verifier")
    writeup = importlib.import_module("hardy.writeup")
    claim, grades, content, evidence = _pieces(domain, writeup, assumed)
    verification = verifier.VerificationResult(
        verified=True,
        reason=None,
        axioms=evidence.axioms,
        diagnostics=(),
        source_sha256=evidence.source_sha256,
        verification_sha256=evidence.digest,
        evidence=evidence,
        assumed=tuple(assumed),
    )
    identities = writeup.RunIdentities(
        run_id=RUN_ID,
        model="test-model",
        backend="fixture",
        runtime_sdk_version="0",
        lean_version="4.32.0",
        mathlib_revision="81a5d257",
        tectonic_version="0.15",
        tectonic_bundle="bundle",
        tectonic_bundle_sha256="e" * 64,
        tectonic_executable="/usr/bin/tectonic",
        prompt_set_sha256="f" * 64,
    )
    return writeup._render(
        claim,
        content,
        grades,
        verification,
        identities,
        domain.DocumentStatus.TEX_COMPILED,
        declared=declared,
    )


def test_a_modulo_document_does_not_say_kernel_verified() -> None:
    """The one thing a reader takes from the front page is the grade, and
    `verified modulo` must not be able to read as unconditional."""
    rendered = _render(assumed=("Papers.perelman.no_local_collapsing",))

    assert "Verified modulo" in rendered
    assert "Kernel verified" not in rendered


def test_a_modulo_document_names_every_assumption_it_rests_on() -> None:
    rendered = _render(assumed=("Papers.a.one", "Papers.b.two"))

    assert "Papers.a.one" in rendered
    assert "Papers.b.two" in rendered


def test_an_unassumed_document_says_it_rests_on_none() -> None:
    """Silence would read the same as an assumption nobody rendered."""
    rendered = _render()

    assert "Kernel verified" in rendered
    assert "no assumption" in rendered.lower()


def test_the_document_states_what_was_assumed_not_only_its_name() -> None:
    """A reader holding the PDF sees `Papers.perelman.no_local_collapsing` and
    cannot tell what was assumed or on whose authority. AGENTS.md: partial
    results are valid only when their assumptions are explicit."""
    domain = importlib.import_module("hardy.domain")
    declared = (
        domain.DeclaredAssumption(
            name="Papers.perelman.no_local_collapsing",
            statement="∀ n : Nat, n = n",
            source="arXiv:math.DG/0211159v1 (thm:collapse)",
            justification="Mathlib has no Ricci flow theory.",
        ),
    )

    rendered = _render(assumed=("Papers.perelman.no_local_collapsing",), declared=declared)

    # As TeX renders it: an underscore in a Lean name is escaped, and what
    # matters is what the reader sees on the page.
    from hardy.writeup import escape_tex_text

    assert escape_tex_text("Papers.perelman.no_local_collapsing") in rendered
    assert "n = n" in rendered, "the statement that was assumed"
    assert "math.DG/0211159v1" in rendered, "and where it came from"
    assert "Ricci flow theory" in rendered, "and why"


def test_an_assumption_the_document_cannot_describe_is_still_named() -> None:
    """A grade naming an axiom no declaration describes must not go silent
    about it -- that is the case a reader most needs to see."""
    rendered = _render(assumed=("Papers.mystery.one",), declared=())

    assert "Papers.mystery.one" in rendered


def test_every_item_the_document_writes_is_inside_a_list() -> None:
    r"""`\item` outside a list environment is a fatal LaTeX error, so a
    document that emits one has no PDF at all -- and the assumptions block
    is exactly where a reader goes to check what the result rests on."""
    domain = importlib.import_module("hardy.domain")
    declared = (
        domain.DeclaredAssumption(
            name="Papers.a.one",
            statement="∀ n : Nat, n = n",
            source="arXiv:2401.00001v1 (thm:one)",
            justification="Mathlib has no such theory.",
        ),
    )
    rendered = _render(assumed=("Papers.a.one", "Papers.b.two"), declared=declared)

    slash = chr(92)
    pattern = re.compile(
        slash + slash + r"(begin|end)" + slash + r"{(itemize|enumerate|description)"
        + slash + r"}|" + slash + slash + r"item(?![A-Za-z])"
    )
    depth = 0
    for match in pattern.finditer(rendered):
        if match.group(1) == "begin":
            depth += 1
        elif match.group(1) == "end":
            depth -= 1
        else:
            assert depth > 0, (
                f"an item at character {match.start()} is outside any list: "
                f"{rendered[match.start() : match.start() + 80]!r}"
            )
    assert depth == 0, "a list environment was left open"


def test_a_described_assumption_breaks_its_line_once() -> None:
    r"""The provenance goes on its own line under the statement, which is one
    `\\`. Four backslashes are two line breaks in a row, and LaTeX refuses
    the second with "there's no line here to end"."""
    domain = importlib.import_module("hardy.domain")
    declared = (
        domain.DeclaredAssumption(
            name="Papers.a.one",
            statement="∀ n : Nat, n = n",
            source="arXiv:2401.00001v1 (thm:one)",
            justification="Mathlib has no such theory.",
        ),
    )
    rendered = _render(assumed=("Papers.a.one",), declared=declared)

    assert chr(92) * 4 not in rendered
