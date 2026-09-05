"""Durable domain values for Hardy runs.

These are the values Hardy writes to disk and reads back, so they are strict and
immutable: a run's identity must not drift between the moment it is approved and
the moment it is graded.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from enum import Enum
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class FrozenModel(BaseModel):
    """A strict immutable value that is safe to hash or persist."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class RunLimits(FrozenModel):
    """Budgets frozen into every run."""

    active_seconds: int = 1_800
    proof_seconds: int = 1_200
    official_checks: int = 40
    # Every source Hardy sends Lean opens with `import Mathlib`, and that import
    # alone was measured at ~21s warm and 223s cold on a developer machine. At
    # the old 30 it left about nine seconds of actual work warm and could not
    # finish at all cold -- so the then-`#find`-backed `search_declarations`
    # timed out on every call, every time, and reported the timeout as a
    # search that found nothing. (That search now answers from the declaration
    # index without a Lean process; this deadline still governs every check
    # and inspection.) The
    # run as a whole is still bounded by `active_seconds` and `proof_seconds`;
    # this only stops a single process being killed before Mathlib has loaded.
    lean_process_seconds: int = 180
    tex_process_seconds: int = 120
    formalization_proposals: int = 5
    model_observation_bytes: int = 32 * 1024
    process_output_bytes: int = 4 * 1024 * 1024
    # A CAS cell is bounded twice: `cas_output_bytes` caps what Hardy captures
    # from the kernel at all, while `model_observation_bytes` caps what is
    # handed back. A cell can be fully recorded and still answered in summary.
    cas_cell_seconds: int = 60
    cas_session_seconds: int = 900
    cas_output_bytes: int = 256 * 1024
    # Premise retrieval is metered the way the official checks are: the budget
    # refuses the next call rather than interrupting one in flight. Wall-clock
    # seconds across the whole run -- a remote index spends its CPU elsewhere,
    # and how long Hardy waits is the part Hardy can enforce.
    #
    # Sized against what a source may cost rather than what it usually costs,
    # because admission is what spends this. A first ranking worst-cases at the
    # declaration index's cold read plus Loogle's 60 (see each source's
    # `worst_case_seconds`), and every later one at the warm figure plus
    # Loogle's -- so 600 guarantees seven rounds where a typical round, a
    # near-instant index pass and ~19s of Loogle, fits two dozen. It was 300
    # while Loogle's bound was believed to be 30s; correcting the bound
    # without correcting this would have quietly halved how much retrieval a
    # proving stage gets.
    retrieval_seconds: int = 600


class FormalizationProposal(FrozenModel):
    restatement: str
    domains: tuple[str, ...]
    quantifiers: tuple[str, ...]
    assumptions: tuple[str, ...]
    interpretation_choices: tuple[str, ...]
    theorem_name: str
    binders: str
    proposition: str


class EnvironmentIdentity(FrozenModel):
    lean_version: str
    lean_commit: str
    mathlib_revision: str
    lake_manifest_sha256: str
    imports: tuple[str, ...] = ("Mathlib",)


class FrozenClaim(FrozenModel):
    original_text: str
    proposal: FormalizationProposal
    environment: EnvironmentIdentity
    imports: tuple[str, ...] = ("Mathlib",)
    approved_at: datetime
    content_hash: str


def freeze_claim(
    original_text: str,
    proposal: FormalizationProposal,
    environment: EnvironmentIdentity,
    approved_at: datetime,
) -> FrozenClaim:
    """Freeze an approved statement and its verifier identity under a stable hash."""
    payload = {
        "approved_at": approved_at.isoformat(),
        "environment": environment.model_dump(mode="json"),
        "imports": list(environment.imports),
        "original_text": original_text,
        "proposal": proposal.model_dump(mode="json"),
    }
    canonical = json.dumps(
        payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return FrozenClaim(
        original_text=original_text,
        proposal=proposal,
        environment=environment,
        imports=environment.imports,
        approved_at=approved_at,
        content_hash=hashlib.sha256(canonical).hexdigest(),
    )


class RunPhase(str, Enum):
    SETUP = "setup"
    FORMALIZING = "formalizing"
    AWAITING_APPROVAL = "awaiting_approval"
    PROVING = "proving"
    FINAL_VERIFICATION = "final_verification"
    WRITEUP = "writeup"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class TerminalReason(str, Enum):
    SETUP_FAILURE = "setup_failure"
    AUTHENTICATION_FAILURE = "authentication_failure"
    MALFORMED_MODEL_OUTPUT = "malformed_model_output"
    USER_REJECTION = "user_rejection"
    LEAN_ELABORATION_FAILURE = "lean_elaboration_failure"
    PROOF_INCOMPLETE = "proof_incomplete"
    FORBIDDEN_HOLE = "forbidden_hole"
    STATEMENT_MISMATCH = "statement_mismatch"
    FAITHFULNESS_DISPUTED = "faithfulness_disputed"
    # Kept apart from the above, because they are different facts and a
    # reader consuming `terminal_reason` acts on them differently: one
    # says the translation was read and refused, the other that nobody
    # read it. Collapsing them would report a rejected translation for a
    # run where none was ever assessed.
    FAITHFULNESS_UNAVAILABLE = "faithfulness_unavailable"
    UNEXPECTED_AXIOM = "unexpected_axiom"
    # A declared assumption whose negation Lean proved. Distinct from
    # `unexpected_axiom`, which is an axiom nobody declared: this one was
    # declared, and is false.
    REFUTED_ASSUMPTION = "refuted_assumption"
    AGENT_RUNTIME_FAILURE = "agent_runtime_failure"
    TIMEOUT_BUDGET_EXHAUSTED = "timeout_budget_exhausted"
    TEX_COMPILATION_FAILURE = "tex_compilation_failure"
    USER_CANCELLATION = "user_cancellation"
    INTERNAL_ERROR = "internal_error"


class FormalStatus(str, Enum):
    """How much of a proof the kernel established, and on what.

    `verified_modulo` is a third grade rather than a shade of either
    neighbour. A proof that used an assumption a human declared is not
    `kernel_verified` -- the kernel checked it against something nobody
    proved -- and calling it `partial` would be false in the other direction,
    since nothing about it is unfinished. What it is worth depends entirely
    on the assumptions, which is why `Grades.assumed` names them exactly.
    """

    KERNEL_VERIFIED = "kernel_verified"
    VERIFIED_MODULO = "verified_modulo"
    PARTIAL = "partial"
    NOT_FORMALIZED = "not_formalized"


class DeclaredAssumption(FrozenModel):
    """One axiom a run is permitted to stand on, declared before it starts.

    `statement` is the Lean type after the colon and nothing else: Hardy
    writes `axiom <name> :` in front of it into the source the independent
    verifier elaborates, so a statement carrying its own header, a proof, or
    a second declaration is refused rather than written.

    `source` and `justification` are for the reader of the artifact, and are
    required for the same reason the interactive flow requires them: an
    assumption whose provenance nobody wrote down is indistinguishable from
    one somebody invented.
    """

    name: str
    statement: str
    source: str
    justification: str = ""


class FaithfulnessStatus(str, Enum):
    """Whether the translation from the user's words to Lean was established.

    `user_approved` names two witnesses, not one: the human approved the
    proposal, *and* an independent reader that never saw the conversation
    which wrote it agreed the Lean says the same thing. `Grades` enforces the
    second half, so the value cannot be reached by approval alone.
    """

    USER_APPROVED = "user_approved"
    NOT_APPROVED = "not_approved"


def schema_text(model: type[BaseModel]) -> str:
    """The JSON Schema text a structured stage sends, rendered one way only.

    Defined here rather than at each call site because two renderings of the
    same schema are two different requests: the staged runtime appends this
    string to the prompt, and the faithfulness gate persists it as the
    response contract the reader answered. Rendered apart -- one compact and
    one spaced, one escaping non-ASCII and one not -- the recorded identity
    would describe a serialization nobody was sent, which is exactly what
    hashing it was for.
    """
    return json.dumps(model.model_json_schema(), sort_keys=True)


class FaithfulnessOutcome(str, Enum):
    AGREED = "agreed"
    DISPUTED = "disputed"
    # The reader could not be reached, or answered with something that is not
    # a review. Kept distinct from `disputed` because they are different
    # facts, and treated the same way by the gate because neither is a pass.
    UNAVAILABLE = "unavailable"


class FaithfulnessReview(FrozenModel):
    """The independent reader's answer, in the shape it is asked for.

    Two entailments rather than a score. A wrong translation is usually
    produced at high confidence -- the model is fluent in Lean, so it renders
    a confident statement of a slightly different claim -- so asking "how sure
    are you" would miss precisely the mismatches this exists to catch. Asking
    both directions separately is what distinguishes a formalization that
    overstates the claim from one that understates it, and either is a
    divergence.

    `divergences` outranks the two flags: a reader that lists a problem and
    then answers yes twice has found something, and this reads it as a
    finding. Being unsure is a divergence, by the prompt's instruction.

    So does `notes`, and for the same reason. It is free-form and shown to the
    user, so a reader could answer yes twice, leave `divergences` empty, and
    explain a mismatch there -- a hole it actually reported, which the gate
    would have stepped over on its way to proving. An agreement is therefore
    silent: anything worth writing about the translation is a divergence, and
    `notes` is where a refusal explains itself. The prompt says this outright,
    so a reader that follows it never trips the rule; one that does not has
    told us something, and a false halt is the cheap direction.
    """

    formalization_entails_claim: bool
    claim_entails_formalization: bool
    divergences: tuple[str, ...] = ()
    notes: str = ""

    @property
    def agrees(self) -> bool:
        return (
            self.formalization_entails_claim
            and self.claim_entails_formalization
            and not self.divergences
            and not self.notes.strip()
        )


class FaithfulnessVerdict(FrozenModel):
    """One independent read of one translation, and what produced it.

    Persisted as `faithfulness.json` and carried in the manifest, so a later
    reader can see that the translation was checked and by what without
    re-running the check. The claim hash ties the verdict to the exact frozen
    statement it read: a verdict naming a different claim is a verdict about
    something else.
    """

    claim_sha256: str
    reviewer_model: str
    # A model name does not say what ran it. Hardy already treats the backend
    # as a result-affecting identity in `RunIdentities`, but that record is
    # written with the document -- which a disputed or unavailable run never
    # reaches, so a halted run would name no runtime at all.
    reviewer_backend: str = "unknown"
    # What the reader's isolation was actually worth, from the runtime that
    # ran it. A name -- "tools-refused" -- means Hardy established that the
    # reader could not reach the run directory; `None` means it could not
    # establish that, which is a different verdict from the same words. The
    # gate's claim is that the reader never saw the conversation it audits,
    # and a record that cannot say which case it was cannot support the claim.
    reviewer_isolation: str | None = None
    # The rendered question, hashed. `prompt_set_sha256` covers the template;
    # this covers the text this claim actually produced from it, which is what
    # the reader was asked. The text itself is kept as
    # `faithfulness-prompt.md`, so this hash is recomputable from the run
    # directory rather than taken on trust.
    prompt_sha256: str
    # The schema the answer had to satisfy, hashed. Generated from
    # `FaithfulnessReview` rather than written in a template, so the prompt-set
    # hash does not cover it: without this, changing the questions the reader
    # is made to answer would leave no trace in the record.
    response_schema_sha256: str = ""
    outcome: FaithfulnessOutcome
    review: FaithfulnessReview | None = None
    detail: str = ""

    @property
    def agreed(self) -> bool:
        return self.outcome is FaithfulnessOutcome.AGREED

    @model_validator(mode="after")
    def outcome_must_follow_the_review(self) -> FaithfulnessVerdict:
        """Refuse a verdict that does not follow from the answer it names.

        Same reason `Grades` recomputes its verification digest: a record
        whose summary can disagree with its evidence is a record that has to
        be believed. Here the summary is the outcome and the evidence is the
        reader's own two answers, so `agreed` cannot be written over a review
        that listed divergences.
        """
        if self.outcome is FaithfulnessOutcome.UNAVAILABLE:
            if self.review is not None:
                raise ValueError("an unavailable review carries no answer")
            if not self.detail:
                raise ValueError("an unavailable review must say why it is unavailable")
            return self
        if self.review is None:
            raise ValueError(f"a {self.outcome.value} verdict requires the review it grades")
        if self.review.agrees is not (self.outcome is FaithfulnessOutcome.AGREED):
            raise ValueError("verdict does not follow from the review it names")
        return self


class InformalStatus(str, Enum):
    NO_GAPS_DETECTED = "no_gaps_detected"
    KNOWN_GAPS = "known_gaps"
    NOT_INDEPENDENTLY_ASSESSED = "not_independently_assessed"


class DocumentStatus(str, Enum):
    TEX_COMPILED = "tex_compiled"
    TEX_FAILED = "tex_failed"
    NOT_ATTEMPTED = "not_attempted"


class VerificationEvidence(FrozenModel):
    """What a kernel-verified grade stands on, and what its digest is taken over.

    `verification_sha256` is this record's digest, so it is derived rather than
    declared: anyone holding the run's artifacts can rebuild the record and
    recompute the number. Every component is separately checkable against the
    run directory — the claim hash against `formalization.json`, the source
    hash against `lean/Main.lean`, the toolchain against the frozen claim — so
    a grade that names evidence names something a reader can go and audit.
    """

    claim_sha256: str
    source_sha256: str
    axioms: tuple[str, ...]
    toolchain: EnvironmentIdentity

    @property
    def digest(self) -> str:
        canonical = json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()


class Grades(FrozenModel):
    """Orthogonal grades. A compiled document never implies a proved theorem."""

    formal: FormalStatus = FormalStatus.NOT_FORMALIZED
    faithfulness: FaithfulnessStatus = FaithfulnessStatus.NOT_APPROVED
    informal: InformalStatus = InformalStatus.NOT_INDEPENDENTLY_ASSESSED
    document: DocumentStatus = DocumentStatus.NOT_ATTEMPTED
    known_gaps: tuple[str, ...] = ()
    #: Exactly the declared assumptions the proof actually used, read from
    #: `#print axioms` rather than from what the run declared. Declaring an
    #: assumption permits it; only the kernel's own report says whether it was
    #: spent, and a manifest listing what was permitted would overstate the
    #: trust base of a proof that did not need it.
    assumed: tuple[str, ...] = ()
    verification_sha256: str | None = None
    verification_evidence: VerificationEvidence | None = None
    faithfulness_review: FaithfulnessVerdict | None = None

    @model_validator(mode="after")
    def require_verification_evidence(self) -> Grades:
        """Tie the formal grade to evidence a reader can re-derive.

        The digest alone proved nothing: any 64 characters satisfied a
        non-empty check, so `kernel_verified` was self-asserted. Carrying the
        record the digest is taken over makes the claim recomputable here and
        on every read-back, and the biconditional keeps the two grades that
        have no evidence — `partial` and `not_formalized` — from carrying a
        hash that would read as one.
        """
        verified = self.formal in (FormalStatus.KERNEL_VERIFIED, FormalStatus.VERIFIED_MODULO)
        if self.verification_evidence is None:
            if verified:
                raise ValueError(f"{self.formal.value} requires final verification evidence")
            if self.verification_sha256 is not None:
                raise ValueError("verification_sha256 without the evidence it is taken over")
            return self
        if not verified:
            raise ValueError("verification evidence without a verified grade")
        if self.verification_sha256 != self.verification_evidence.digest:
            raise ValueError("verification_sha256 does not match its evidence")
        return self

    @model_validator(mode="after")
    def modulo_names_what_it_stands_on(self) -> Grades:
        """Keep the three formal grades from blurring into each other.

        `kernel_verified` is Lean's own foundations and nothing else, so a
        grade naming an assumption may not wear it -- that is the whole
        distinction, and a run that could report an assumed proof as verified
        would make the word worthless. `verified_modulo` without a named
        assumption is the same failure from the other side: a grade that says
        "modulo something" and cannot say what.
        """
        assumed = self.assumed
        if self.formal is FormalStatus.KERNEL_VERIFIED and assumed:
            raise ValueError(
                "a proof resting on declared assumptions is verified_modulo, not "
                "kernel_verified"
            )
        if self.formal is FormalStatus.VERIFIED_MODULO and not assumed:
            raise ValueError("a verified_modulo grade must name the assumption it rests on")
        if assumed and self.formal not in (
            FormalStatus.VERIFIED_MODULO,
            FormalStatus.PARTIAL,
        ):
            raise ValueError("assumptions recorded against a grade that cannot carry them")
        return self

    @model_validator(mode="after")
    def approval_requires_an_independent_reader(self) -> Grades:
        """Tie the faithfulness grade to the reader that agreed with it.

        Kernel acceptance says a Lean statement was proved; it says nothing
        about whether that statement is the claim the user made. So the
        translation carries its own evidence, on the same terms the formal
        grade carries its verification record: `user_approved` holds exactly
        when an independent reader that never saw the conversation which wrote
        the formalization read it and agreed. A disputed verdict cannot wear
        the approved grade, and the grade cannot be self-asserted with no
        verdict behind it at all.

        The last clause is the one the whole gate rests on: a kernel-verified
        result whose translation was disputed is a proof of the wrong theorem,
        and this refuses to record one.
        """
        review = self.faithfulness_review
        approved = self.faithfulness is FaithfulnessStatus.USER_APPROVED
        agreed = review is not None and review.agreed
        if approved and not agreed:
            raise ValueError(
                "user_approved faithfulness requires an agreeing independent review"
            )
        if agreed and not approved:
            raise ValueError("an agreeing independent review without an approved grade")
        if (
            self.formal in (FormalStatus.KERNEL_VERIFIED, FormalStatus.VERIFIED_MODULO)
            and not approved
        ):
            raise ValueError(
                f"{self.formal.value} requires an approved, independently read claim"
            )
        return self


class RunManifest(BaseModel):
    # `extra="forbid"` makes every added field a breaking read, so the version
    # moves whenever the shape does -- including the shapes nested inside it,
    # which are strict for the same reason. 2 added `grades.
    # verification_evidence`: a version-1 manifest graded `kernel_verified`
    # names a hash with nothing behind it, which is exactly what that version
    # stopped accepting. 3 added `limits.retrieval_seconds`, so a version-2
    # reader would reject every manifest written since premise retrieval
    # landed; leaving the version at 2 would have let one number name two
    # incompatible shapes. 4 added `grades.faithfulness_review`, and with it
    # the rule that `user_approved` names an independent reader's agreement --
    # a version-3 manifest's approval was the human's alone, which is exactly
    # the weaker claim this version stopped accepting. 5 gave `usage` the
    # shape `hardy.usage.Usage.summary` writes -- a figure the provider never
    # stated is `None`, and `reported` says how many exchanges each figure
    # covers -- where version 4 typed it as a map of integers and every
    # staged run left it empty, which read as a run that had spent nothing.
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[5] = 5
    run_id: UUID
    created_at: datetime
    phase: RunPhase
    model: str
    prompt_set_sha256: str
    limits: RunLimits = Field(default_factory=RunLimits)
    environment: EnvironmentIdentity | None = None
    claim_sha256: str | None = None
    grades: Grades = Field(default_factory=Grades)
    terminal_reason: TerminalReason | None = None
    artifacts: dict[str, str] = Field(default_factory=dict)
    timings_ms: dict[str, int] = Field(default_factory=dict)
    # What the provider said the run cost, as `Usage.summary` states it: the
    # cost, the four token counters, and the exchange count, each `None` where
    # nothing was reported rather than 0. Empty only for a run that never
    # opened a provider thread.
    usage: dict[str, Any] = Field(default_factory=dict)
