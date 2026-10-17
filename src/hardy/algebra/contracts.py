"""Recorded cells, kernel outcomes and replay comparisons; no live kernel."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from enum import Enum
from typing import Any, Literal, TypeAlias

from pydantic import Field, model_validator

from hardy.foundation.values import FrozenModel

HEADER_BYTES = 10
MERGED_CAPTURE_NOTE = (
    "Sentinel output combines stdout and stderr in the stdout transcript; "
    "the original stream origin is unavailable."
)
# A cell is bracketed by two markers, not trailed by one. A pipe preserves
# write order, so whatever the interpreter printed for the *previous* cell is
# necessarily before this cell's begin marker in the stream, however late it
# happens to arrive -- which is what lets the extractor exclude it without
# ever having to guess whether it has fully arrived yet.
SENTINEL_BEGIN = "«hardy-begin:{nonce}»"
SENTINEL_END = "«hardy-end:{nonce}»"
BackendName = Literal["sympy", "singular", "macaulay2"]

class ReadFailure(Enum):
    """Distinguishable non-answers from a read of one cell's reply."""

    # The deadline passed.
    TIMED_OUT = "timed out"
    # The stream said something that cannot belong to the cell we sent.
    DESYNCHRONISED = "desynchronised"
    # The interrupt was asked for and the kernel did not answer it within the
    # grace. Distinct from `TIMED_OUT` because the cell did not exceed anything
    # -- it was stopped -- and distinct from a kernel that *did* answer, which
    # is the whole point of interrupting rather than timing out.
    INTERRUPTED = "interrupted"


TIMED_OUT = ReadFailure.TIMED_OUT
DESYNCHRONISED = ReadFailure.DESYNCHRONISED
INTERRUPTED = ReadFailure.INTERRUPTED

# How hard a stop has been asked for: signal and let the kernel answer, or stop
# waiting for an answer. Mirrors the levels `process` keeps for its own
# register, and for the same reason.
_ASKED, _INSISTED = 1, 2


class CasError(Exception):
    """A CAS call that cannot be answered, phrased for the model that asked."""


class CellOutcome(FrozenModel):
    """What an adapter extracts from one framed reply, before Hardy records it."""

    status: Literal["ok", "error", "kernel_died", "timeout", "interrupted"]
    stdout: str = ""
    stderr: str = ""
    capture_mode: Literal["separate", "merged"] = "separate"
    value_repr: str = ""
    capture_truncated: bool = False
    # Whether the kernel has to be dropped because of this outcome. An
    # interrupt is the only status that goes both ways: a kernel that answers
    # one is still a kernel, with its namespace intact, which is the entire
    # reason for interrupting instead of timing out -- and one that does not
    # answer has to be stopped like any other unreachable kernel. CellRecord
    # retains this distinction so recovery after reopening has the same policy.
    kernel_lost: bool = False
    # The kernel's fingerprint of its own namespace once this cell was done.
    # Empty when the backend cannot produce one -- a sentinel interpreter has
    # no protocol to carry it -- and `_restore` says so rather than claiming a
    # rebuild it could not check.
    state_digest: str = ""
    # Whether Hardy actually signalled this cell. A cell that reports `ok`
    # after being signalled is not acceptable even though it says it worked: a
    # cell -- or a library under it -- may catch the interrupt and return
    # normally from a path it would not otherwise have taken, and a replay
    # without the signal would then not reproduce it. It is recorded and
    # reported like any other cell; it just cannot be built on.
    signalled: bool = False


# One cell's reply as a backend's extractor reads it out of the kernel's
# stdout: the outcome, and how many bytes of the stream it took. An extractor
# answers `None` while the reply is still arriving, and `DESYNCHRONISED` when
# the bytes cannot be a reply to the cell that was sent.
Reply: TypeAlias = tuple[CellOutcome, int]
Extractor: TypeAlias = Callable[[bytes], Reply | ReadFailure | None]


# Fields this model used to carry, dropped on the way in rather than refused.
# `FrozenModel` forbids extras, and `model_dump_json` writes every field --
# including a defaulted one nothing ever set -- into every line of the durable
# log. Retiring a field without this makes every log an earlier build wrote
# unloadable, and a `CasSession` that cannot be constructed takes chat startup
# down with it, which is the failure `_mend_log` already exists to avoid.
RETIRED_RECORD_FIELDS = ("output_artifact",)


class CellRecord(FrozenModel):
    """One line of the durable cell log: what ran, what it showed, and whether
    the accepted state that recovery and export rebuild from includes it."""

    seq: int
    # Incremented by reset. Only the highest segment is live, which is how a
    # reset survives a restart: it is on every record rather than inferred from
    # a sentinel line that a reader would have to know how to recognise.
    segment: int
    author: Literal["model", "human"]
    source: str
    # The file under `cas/` this cell was read from, relative to that
    # directory, or "" on a record written before cells were files. `source`
    # stays beside it: the file may be rewritten later, and the record has to
    # say what ran, not what the path holds now.
    path: str = ""
    # "interrupted" is never accepted, and for the same reason "error" is not:
    # the cell did not finish, and it may well have changed the namespace on
    # its way to being stopped. What it leaves behind is outside the accepted
    # set, exactly as an errored cell's is.
    status: Literal["ok", "error", "timeout", "kernel_died", "interrupted"]
    accepted: bool
    # None on legacy records: an interrupted/otherwise unaccepted cell may
    # have left live mutations. Only known terminal cells can be rolled back
    # without silently dropping effects that later accepted cells used.
    kernel_lost: bool | None = Field(default=None, strict=True)
    stdout: str = ""
    stderr: str = ""
    # Sentinel backends retain one ordered stdout/stderr transcript in stdout.
    # Old journals used separate pipes; matching bytes alone cannot establish
    # that those timing-dependent captures observed all diagnostics.
    capture_mode: Literal["separate", "merged"] = "separate"
    value_repr: str = ""
    duration_ms: int = 0
    # The session's running total of billed CAS wall clock as of this append,
    # across every process that has opened the log -- the figure `cas_state`
    # reports as `seconds_spent`. Cumulative rather than per-cell so a reopened
    # session reads its own figure off the last record instead of starting a
    # new one; `duration_ms` is this cell alone. Zero on records written before
    # the field existed, so an older log still loads.
    spent_ms: int = 0
    capture_truncated: bool = False
    # The toolchain that produced this record, carried on the durable log
    # rather than only in an export manifest: a session that is saved but never
    # exported has no other place to say what ran it, and a log reopened under
    # a different `cas_backend` would otherwise be replayed as if the source
    # were the new backend's language. Defaulted to "" so logs written before
    # this field existed still load; `_foreign_backend` ignores empty values
    # for the same reason.
    backend: str = ""
    backend_version: str = ""
    # The kernel's fingerprint of its own namespace once this cell had run.
    # Recorded for every cell, failed ones included: a cell that raised partway
    # through has still changed the namespace, and a rebuild that replays only
    # the accepted cells has to be able to notice that what it rebuilt is not
    # what was there. Empty on a backend that cannot produce one, and on every
    # record written before this field existed -- `reproduces` compares it only
    # when the record carries one, so an older log still loads and still
    # replays.
    state_digest: str = ""
    # Hardy's own commentary on the cell -- currently only that the kernel was
    # rebuilt before it ran. Deliberately its own field rather than a line
    # prepended to `stdout`: `stdout` is what the kernel produced, and it is
    # what `reproduces` compares and what the export replays. A note mixed into
    # it makes the record unreproducible by construction, which poisons the
    # next rebuild and marks every post-restart cell `diverged` on export.
    restart_note: str = ""

    @model_validator(mode="before")
    @classmethod
    def _drop_retired_fields(cls, data: Any) -> Any:
        if isinstance(data, dict) and any(name in data for name in RETIRED_RECORD_FIELDS):
            return {key: value for key, value in data.items() if key not in RETIRED_RECORD_FIELDS}
        return data


class RebuildReport(FrozenModel):
    """What rebuilding a dead kernel's state from the accepted cells established."""

    replayed: int = 0
    diverged: tuple[int, ...] = ()
    failed: int | None = None
    ok: bool = True
    # Cells whose replay reproduced everything Hardy can see and nothing more.
    # A cell that prints nothing and changes the namespace -- `import random;
    # x = random.random()` -- reproduces three empty fields however different
    # the value it rebuilt, so on a backend that carries no state digest a
    # clean replay of it is not evidence of a faithful rebuild. Named here so
    # the session can say which cells it could not check rather than reporting
    # a rebuild as if it had.
    unverified: tuple[int, ...] = ()
    # Which reason put each of them there. A cell can be on the list because
    # the record carries no fingerprint, because the *replay* could not take
    # one, or because a capture stopped at `cas_output_bytes` and the
    # discarded tails were never compared -- and a report that named one told
    # a reader to go and look at the wrong thing. They point somewhere
    # different: the log never had it, this kernel could not produce it, or
    # the evidence exists and stops early.
    digestless: tuple[int, ...] = ()
    unfingerprintable: tuple[int, ...] = ()
    clipped: tuple[int, ...] = ()
    # Retained for existing report consumers. Current recovery refuses live
    # unaccepted cells instead of returning a report that omits their effects.
    unreplayed: tuple[int, ...] = ()

    @classmethod
    def from_checks(cls, checks: Mapping[int, ReplayCheck]) -> RebuildReport:
        """A rebuild whose every replay agreed, naming what each could not compare.

        A clean replay is not the same as a checked one. A cell whose namespace
        was never compared agrees with its record whatever it rebuilt, and a
        truncated capture belongs beside it for the same reason: the retained
        prefixes matched and the discarded tails were never compared, so a
        cell printing a deterministic prefix over a random tail replays
        "cleanly" without anything having checked the part that differs.
        """
        return cls(
            replayed=len(checks),
            unverified=tuple(seq for seq, check in checks.items() if check.verdict == "unverified"),
            digestless=tuple(seq for seq, check in checks.items() if check.digestless),
            unfingerprintable=tuple(seq for seq, check in checks.items() if check.unfingerprinted),
            clipped=tuple(seq for seq, check in checks.items() if check.clipped),
        )


def normalise(text: str) -> str:
    """Compare outputs without being defeated by trailing whitespace.

    Trailing only, in both senses: whitespace at the end of each line, and
    whitespace at the end of the whole capture. Leading whitespace is content.
    `text.strip()` used to take it off the front as well, so a replay that
    printed `x` matched a session that had printed `  x` -- and the notebook,
    which stores the original bytes, showed the difference this function had
    just declared not to exist. Indentation is meaningful in every language
    Hardy drives, and in Macaulay2's pretty-printed matrices it is most of the
    value.
    """
    return "\n".join(line.rstrip() for line in text.rstrip().splitlines())


def _why_unverified(report: RebuildReport) -> str:
    """Say which of the two gaps a rebuild's unverified cells actually have.

    Both end in "the replay agreed with everything Hardy compared, and that is
    not everything", but they are different omissions and a reader chasing one
    will not find the other. A cell can be on the list for both.
    """
    reasons = []
    if report.digestless:
        reasons.append(
            f"cell(s) {list(report.digestless)} carry no state digest, so their "
            "replay agrees whatever namespace it rebuilt"
        )
    if report.unfingerprintable:
        reasons.append(
            f"cell(s) {list(report.unfingerprintable)} were recorded with a state "
            "digest the replay could not produce one to compare against"
        )
    if report.clipped:
        reasons.append(
            f"cell(s) {list(report.clipped)} were captured up to cas_output_bytes, "
            "so the discarded tails were never compared"
        )
    return ("; ".join(reasons) + ". What they printed, as far as it was kept, "
            "did reproduce.")


def reproduces(record: CellRecord, outcome: CellOutcome) -> bool:
    """Whether a replayed cell produced what the live session recorded.

    stderr counts. The notebook preserves it, so a cell whose warnings did not
    reproduce has not reproduced, whatever its stdout says.

    So does the namespace, where the backend can describe it. Output is what a
    cell *showed*, not what it *did*: `import random; x = random.random()`
    shows nothing at all, and comparing only what it showed called a replay
    that rebuilt a different `x` faithful -- with every later cell then
    standing on a value nobody had compared. The digest closes that, and it
    closes the other half of the same hole for free: a cell whose recorded
    digest includes an effect left behind by a *failed* cell (`x = 41; 1 / 0`,
    then an accepted `pass`) cannot match a replay that never ran the failure.

    Compared only when *both* sides carry one. A log written before the field
    existed, or by a backend with no protocol to carry it, has nothing to
    compare; so does a replay that could not fingerprint what it rebuilt --
    a leaf whose `__repr__` succeeds live and mutates the namespace under some
    changed external condition leaves the same state and no digest for it.
    Testing only the record for absence read that as a *different* namespace,
    poisoned the session, and told the reader the one thing that had not been
    established. `state_unchecked` is what says so instead, and it says it in
    the field meant for it.
    """
    if not same_output(record, outcome):
        return False
    if record.state_digest and outcome.state_digest:
        return outcome.state_digest == record.state_digest
    return True


def state_unchecked(record: CellRecord, outcome: CellOutcome | None) -> bool:
    """Whether this replay proved anything about the state the cell left.

    Either side missing is the whole answer. `unobservable` is the record's
    half, kept separate because a rebuild reports on records it has not
    replayed yet and because the two absences have different causes: one says
    the log never carried a fingerprint, the other that this kernel could not
    take one.
    """
    return unobservable(record) or outcome is None or not outcome.state_digest


def same_output(record: CellRecord, outcome: CellOutcome) -> bool:
    """The half of `reproduces` a reader can see for themselves.

    Separate so a divergence can say *which* comparison failed. A silent
    `x = random.random()` reproduces every printed field and fails only on the
    digest, and calling that "different output" told the manifest and the
    notebook the opposite of what happened.
    """
    return (
        outcome.capture_mode == record.capture_mode
        and normalise(outcome.stdout) == normalise(record.stdout)
        and normalise(outcome.stderr) == normalise(record.stderr)
        and normalise(outcome.value_repr) == normalise(record.value_repr)
    )


def unobservable(record: CellRecord) -> bool:
    """Whether replaying this cell could prove anything about the state it left.

    A missing digest is the whole answer, and reproduced output is not a
    second opinion on it. This used to also require the cell to have printed
    nothing, on the theory that a cell which printed something had been
    checked -- but output is what a cell *showed*, and a cell that prints a
    stable banner is free to leave a different value behind it. The narrower
    rule reported an ordinary successful rebuild for exactly the cells it
    could not check.

    Empty digests are not rare, either: every sentinel backend, every record
    written before the field existed, and any namespace the default kernel
    could only fingerprint a prefix of.
    """
    return not record.state_digest


# What one replayed cell establishes about its record, in the words an export
# publishes. `verified` is the whole claim: the cell ran, reproduced every
# field Hardy compares, and its namespace was compared as well.
Verdict = Literal["verified", "diverged", "failed", "unverified"]


class ReplayCheck(FrozenModel):
    """One replayed cell, judged against its record.

    The single rule a rebuild and an export both apply, so that a cell a
    rebuild calls unchecked is never one an export publishes as verified. An
    agreeing replay is still `unverified` when any comparison it rests on is
    incomplete, and the flags say which: a capture cut at `cas_output_bytes`
    on either side, a record with no namespace digest, or a replay that could
    not take one. More than one can hold.
    """

    verdict: Verdict
    detail: str = ""
    clipped: bool = False
    digestless: bool = False
    unfingerprinted: bool = False


def check_replay(record: CellRecord, outcome: CellOutcome | None) -> ReplayCheck:
    """Judge one replay of `record`; `outcome` is None for a cell never reached.

    A missing digest is a gap whatever the backend. Singular and Macaulay2
    have no protocol to carry one, so every replay there reproduces what the
    cell showed and says nothing about what it left -- which is exactly what a
    rebuild has always reported, and what an export now publishes too.
    """
    if outcome is None:
        return ReplayCheck(verdict="unverified", detail="not reached")
    if outcome.status != "ok":
        excerpt = (outcome.stderr or outcome.stdout).strip()[:160]
        return ReplayCheck(verdict="failed", detail=f"replay {outcome.status}: {excerpt}")
    if not reproduces(record, outcome):
        # Which comparison failed, not just that one did. A silent
        # `x = random.random()` reproduces every printed field and fails only
        # on the digest, and reporting that as "different output" told
        # `export.json` and the notebook the opposite of what happened --
        # hiding exactly the state discrepancy the digest was added to expose.
        return ReplayCheck(
            verdict="diverged",
            detail=(
                "replay produced different output"
                if not same_output(record, outcome)
                else "replay reproduced the output and rebuilt a different namespace"
            ),
        )
    gaps = ReplayCheck(
        verdict="unverified",
        clipped=record.capture_truncated or outcome.capture_truncated,
        digestless=unobservable(record),
        unfingerprinted=not unobservable(record) and not outcome.state_digest,
    )
    if gaps.clipped:
        # The prefixes match and nothing is known about the rest. Calling that
        # "verified" would claim complete reproduction on evidence that stops
        # at `cas_output_bytes`; AGENTS.md asks for a partial result to state
        # its limits rather than round itself up.
        return gaps.model_copy(update={"detail": (
            "capture truncated at cas_output_bytes: the retained prefixes "
            "match, and the discarded tails could not be compared"
        )})
    if state_unchecked(record, outcome):
        # The replay reproduced everything Hardy can see and nothing more.
        # Either side of the comparison missing puts a cell here: a record
        # with no fingerprint and a replay that could not take one are
        # different causes with the same consequence, nothing was compared.
        return gaps.model_copy(update={"detail": "output reproduced, state not compared: " + (
            "this kernel could not fingerprint the namespace this cell left"
            if gaps.digestless
            else "the replay could not fingerprint the namespace it rebuilt"
        )})
    return ReplayCheck(verdict="verified")
