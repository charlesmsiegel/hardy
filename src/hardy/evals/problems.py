"""The problem list: entries a sweep can tier and a runner can pose.

`binders` and `conclusion` are kept apart so nothing here parses Lean: the
declaration, the proposition and its negation are assembled by string
concatenation, one way, for every consumer.
"""
from __future__ import annotations

import hashlib
import re
from collections import Counter
from datetime import datetime
from functools import cached_property
from pathlib import Path
from typing import Literal

from pydantic import ConfigDict, Field, model_validator

from ..domain import FrozenModel
from . import digests, taxonomy

SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_'.]*$")
# A dotted Lean module path -- what a real `import` line accepts -- and
# nothing else: no newline, no space, no stray token that could smuggle a
# second Lean command onto the same or a following line (item 6b).
# `12Fxx` or `12F10`, the two forms that name mathematics. MSC also publishes
# `12-XX` (the bare class) and `12-01` (publication type: textbooks, surveys,
# proceedings), and neither classifies a theorem.
SUBJECT_CODE = re.compile(r"\d\d[A-Z](xx|\d\d)")
IMPORT = re.compile(r"^[A-Za-z_][A-Za-z0-9_']*(?:\.[A-Za-z_][A-Za-z0-9_']*)*$")


class Occurrence(FrozenModel):
    """Where a result appears in a text: a source and an ordered position.

    `locator` is `(chapter, section, item)` compared lexicographically. The
    constraints are load-bearing rather than tidiness: an empty tuple sorts
    before every non-empty one and `(-1,)` before any real chapter, so an
    unconstrained tuple would let malformed provenance satisfy §9.0's "strictly
    earlier" antecedent gate without naming any earlier result.
    """

    source_id: str = Field(min_length=1)
    locator: tuple[int, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _nonnegative(self) -> Occurrence:
        if any(part < 0 for part in self.locator):
            raise ValueError(f"locator parts must be non-negative: {self.locator!r}")
        return self

    def __lt__(self, other: Occurrence) -> bool:
        return self.locator < other.locator


class Review(FrozenModel):
    """A recorded human faithfulness read (spec §2.2).

    The digests and the classification are both in here: an edit to the
    statement, the prompt, or the field invalidates the approval, because a
    reviewer approved a specific thing filed in a specific place. A
    wrong-but-syntactically-valid MSC code passes every mechanical check, so
    this review is the only gate between a misclassified entry and the wrong
    field's headline.
    """

    reviewer: str = Field(min_length=1)
    reviewed_at: str = Field(min_length=1)
    # See `_identifies_a_reviewer_and_a_date`: `min_length` alone accepts " "
    # and "unknown", and `active_ids` trusts the status this record grants.
    statement_digest: str = Field(min_length=64, max_length=64)
    prompt_digest: str = Field(min_length=64, max_length=64)
    msc: tuple[str, ...] = Field(min_length=1)
    group: str = Field(min_length=1)
    verdict: Literal["faithful", "unfaithful"]
    reason: str | None = None

    @model_validator(mode="after")
    def _identifies_a_reviewer_and_a_date(self) -> Review:
        """A review is the gate between a candidate and a field headline, so
        it must say who read the entry and when. `min_length=1` accepts a
        whitespace name and `"unknown"` as a date, which identifies neither.
        """
        if not self.reviewer.strip():
            raise ValueError("a review must name its reviewer")
        try:
            datetime.fromisoformat(self.reviewed_at.replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError(
                f"reviewed_at must be an ISO 8601 date or timestamp, not {self.reviewed_at!r}"
            ) from error
        return self

    @model_validator(mode="after")
    def _unfaithful_needs_a_reason(self) -> Review:
        if self.verdict == "unfaithful" and not (self.reason or "").strip():
            raise ValueError("an unfaithful verdict must record why")
        return self


class Audit(FrozenModel):
    """A spot-audit raised by C3 against one measurement panel (spec §9.2).

    Bound to its panel so a later panel raises a fresh audit rather than
    inheriting a verdict reached about different models.
    """

    panel: str = Field(min_length=1)
    raised_at: str = Field(min_length=1)
    verdict: Literal["pending", "sound", "broken"]
    note: str | None = None

    @model_validator(mode="after")
    def _broken_needs_a_note(self) -> Audit:
        if self.verdict == "broken" and not (self.note or "").strip():
            raise ValueError("a broken verdict must record why")
        return self


class Entry(FrozenModel):
    id: str = Field(pattern=SLUG.pattern)
    input: str = Field(min_length=1)
    name: str = Field(pattern=IDENT.pattern)
    binders: str = ""
    conclusion: str = Field(min_length=1)
    imports: tuple[str, ...] = ("Mathlib",)
    expected: Literal["true", "false"]
    twin_of: str | None = None
    source: Literal["textbook", "classical", "mathlib-gap", "competition"]
    title: str | None = None
    msc: tuple[str, ...] = Field(min_length=1)
    arxiv_override: str | None = None
    override_reason: str | None = None
    difficulty: Literal["routine", "substantial", "qualifying", "research-adjacent"]
    occurrences: tuple[Occurrence, ...] = ()
    rationale: str | None = None
    witness: str | None = None
    witness_note: str | None = None
    status: Literal["candidate", "active", "retired"] = "candidate"
    retired_reason: str | None = None
    review: Review | None = None
    audit: tuple[Audit, ...] = ()
    fixtures: tuple[str, ...] = ()

    @property
    def shard(self) -> str:
        """Derived, never stored: a stored shard is a derived value in the corpus."""
        return self.msc[0][:2]

    @model_validator(mode="after")
    def _codes_are_known_and_finer_than_their_class(self) -> Entry:
        for code in self.msc:
            if not SUBJECT_CODE.fullmatch(code):
                raise ValueError(
                    f"{code!r} is not an MSC2020 subject code. A code names a section "
                    "(`12Fxx`) or a subsection (`12F10`): `12-XX` is the bare class, which is "
                    "what a tagger writes when they did not look, and `12-01` classifies a "
                    "publication type rather than mathematics (spec section 2)"
                )
            if not taxonomy.is_known(code):
                raise ValueError(f"unknown MSC2020 code: {code!r}")
        return self

    @model_validator(mode="after")
    def _reasons_accompany_the_states_that_need_them(self) -> Entry:
        if self.status == "retired" and not (self.retired_reason or "").strip():
            raise ValueError("a retired entry must record why")
        if self.arxiv_override is not None:
            if not (self.override_reason or "").strip():
                raise ValueError("an arxiv_override must record why")
            if self.arxiv_override not in taxonomy.arxiv_classes():
                raise ValueError(
                    f"arxiv_override outside the mapping codomain: {self.arxiv_override!r}"
                )
        if self.witness is None and not (self.witness_note or "").strip():
            raise ValueError("witness: null must record why no witness can be produced")
        return self

    @model_validator(mode="after")
    def _authored_entries_are_self_describing_and_carry_no_fixtures(self) -> Entry:
        if self.occurrences:
            return self
        if not (self.rationale or "").strip():
            raise ValueError("an entry with no occurrences must record a rationale (spec 2.2)")
        if self.fixtures:
            raise ValueError(
                "an authored entry has no primary occurrence, so the antecedent check cannot "
                "apply: it may not carry fixtures"
            )
        return self

    @model_validator(mode="after")
    def _an_active_entry_carries_a_current_faithful_review(self) -> Entry:
        """Only `active` entries reach a headline (spec section 2.2).

        Nothing mechanical distinguishes a faithful formalisation from a
        plausible-looking wrong one, so a human read is the gate -- and a
        reviewer approved a *specific* statement, prompt and filing. Binding
        the review to all three means an edit or a re-tag drops the entry back
        to `candidate` instead of leaving a stale approval standing.
        """
        if self.status != "active":
            return self
        if self.review is None:
            raise ValueError("an active entry must carry a review: only active entries reach a headline")
        if self.review.verdict != "faithful":
            raise ValueError(f"an active entry needs a faithful review, not {self.review.verdict!r}")
        if self.review.statement_digest != self.statement_digest():
            raise ValueError("the review approved a different statement; re-review or set status to candidate")
        if self.review.prompt_digest != self.prompt_digest():
            raise ValueError("the review approved a different prompt; re-review or set status to candidate")
        if self.review.msc != self.msc or self.review.group != taxonomy.group_of(self.msc[0]):
            raise ValueError(
                "the review approved a different classification; a wrong-but-valid code passes every "
                "mechanical check, so re-review or set status to candidate"
            )
        return self

    @model_validator(mode="after")
    def _binders_never_carry_an_antecedent(self) -> Entry:
        """An antecedent in `binders` reaches the bare condition too (spec 9.1)."""
        for fixture in self.fixtures:
            if fixture in self.binders:
                raise ValueError(
                    f"binders mention fixture {fixture!r}: an antecedent must never reach the "
                    "bare condition"
                )
        return self

    def statement_digest(self) -> str:
        """The A1/A2/A3/A6 component. Fixtures are a *separate* component --
        see `fixture_set_digest` -- so an edit to a shared fixture cannot
        stale the conditions that never load one (spec section 3)."""
        return digests.statement_digest(
            name=self.name, binders=self.binders, conclusion=self.conclusion,
            imports=self.imports, witness=self.witness, witness_note=self.witness_note,
        )

    def prompt_digest(self) -> str:
        """The B1/B2/B3 component: the statement plus everything else the
        model sees or that shapes the run. Fixture-free, for the same reason."""
        return digests.prompt_digest(
            statement=self.statement_digest(), input=self.input,
            expected=self.expected, twin_of=self.twin_of,
        )

    def fixture_set_digest(self, resolved: tuple[str, ...]) -> str:
        """The A4/A5/B4 component, over the *resolved* fixture contents.

        `resolved` comes from the fixture store, which phase 3 adds; until
        then an entry carries no fixtures and this is the digest of nothing.
        """
        return digests.fixture_set_digest(resolved)

    @model_validator(mode="after")
    def _statement_only(self) -> Entry:
        if ":=" in self.conclusion or ":=" in self.binders:
            raise ValueError("an entry states a theorem, not a proof: no ':='")
        return self

    @model_validator(mode="after")
    def _no_lean_command_injection(self) -> Entry:
        """`declaration`/`proposition`/`negation` assemble Lean source by
        string concatenation (module docstring), which every consumer --
        `sweep.py`'s baseline elaborator and the batch runner's request --
        then hands to a real Lean process. A newline anywhere in `binders`,
        `conclusion`, or `name` ends the surrounding `theorem`/`example`
        command and lets whatever text follows run as a new one; a bad
        `imports` entry can do the same on its own `import` line. Both are a
        Lean command injection through a problem file neither `evals baseline`
        nor `evals run` otherwise treats as anything but data.
        """
        for field in ("binders", "conclusion", "name"):
            value = getattr(self, field)
            if "\n" in value or "\r" in value:
                raise ValueError(f"{field} carries a newline: no Lean command injection")
        bad = [name for name in self.imports if not IMPORT.fullmatch(name)]
        if bad:
            raise ValueError(f"imports must be dotted Lean module names: {bad!r}")
        return self

    def declaration(self) -> str:
        binders = f" {self.binders.strip()}" if self.binders.strip() else ""
        return f"theorem {self.name}{binders} : {self.conclusion.strip()}"

    def proposition(self) -> str:
        if not self.binders.strip():
            return self.conclusion.strip()
        return f"∀ {self.binders.strip()}, {self.conclusion.strip()}"

    def negation(self) -> str:
        return f"¬ ({self.proposition()})"


class ProblemSet(FrozenModel):
    model_config = ConfigDict(extra="forbid", frozen=True, ignored_types=(cached_property,))

    schema_version: Literal[2] = 2
    entries: tuple[Entry, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _consistent(self) -> ProblemSet:
        for label, seen in (("id", [e.id for e in self.entries]),
                            ("name", [e.name for e in self.entries])):
            dupes = sorted(value for value, count in Counter(seen).items() if count > 1)
            if dupes:
                raise ValueError(f"duplicate {label}: {', '.join(dupes)}")
        by_id = {e.id: e for e in self.entries}
        for entry in self.entries:
            if entry.expected == "true" and entry.twin_of is not None:
                raise ValueError(f"{entry.id}: a true entry has no twin_of")
            if entry.expected == "false":
                target = by_id.get(entry.twin_of or "")
                if target is None:
                    raise ValueError(f"{entry.id}: twin_of must name an entry in the list")
                if target.expected != "true":
                    raise ValueError(f"{entry.id}: twin_of must name a true entry, not a twin")
                if entry.msc[0] != target.msc[0]:
                    raise ValueError(
                        f"{entry.id}: a twin is in the same field as the statement it perturbs; "
                        f"{entry.msc[0]} drifts from {target.msc[0]}"
                    )
        return self

    def by_id(self, id: str) -> Entry:
        """O(1): a linear scan here is quadratic when a caller loops over it."""
        return self.index[id]

    @cached_property
    def index(self) -> dict[str, Entry]:
        """Built once. `by_id` in a loop was quadratic without it."""
        return {e.id: e for e in self.entries}

    @property
    def true_entries(self) -> tuple[Entry, ...]:
        return tuple(e for e in self.entries if e.expected == "true")

    @property
    def twins(self) -> tuple[Entry, ...]:
        return tuple(e for e in self.entries if e.expected == "false")


def sha256_of(path: Path) -> str:
    """The digest a baseline or scoreboard binds to: the file's bytes, not its parse."""
    return hashlib.sha256(path.read_bytes()).hexdigest()
