"""Append-only attributed measurement reviews beside exact batch artifacts.

Actors and problem/repeat labels are caller declarations, not authenticated
identities. Decisions never replace canonical reviews, outcomes or kernel
evidence. A changed artifact makes an annotation stale while retaining history.
This first owner requires a complete batch journal; legacy identity is unknown.
"""
from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, StringConstraints, field_validator

from hardy.foundation.files import WriteGuard
from hardy.foundation.locking import FileLock
from hardy.foundation.values import FrozenModel, json_digest
from hardy.workflows.batch_recording import read_attempt

JOURNAL = "adjudications.jsonl"
LOCK = "adjudications.lock"
Text = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1)]
Digest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class Artifact(FrozenModel):
    path: str
    sha256: Digest


class Subject(FrozenModel):
    attempt_id: Text
    problem_id: Text
    repeat: int = Field(ge=0, strict=True)
    artifacts: tuple[Artifact, ...]


class Review(FrozenModel):
    subject: Subject
    actor: Text
    at: datetime
    decision: Literal["uphold", "challenge", "withdraw"]
    reason: Text

    @field_validator("at")
    @classmethod
    def aware_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("review time must have a timezone")
        return value


class Record(FrozenModel):
    schema_version: Literal[1] = 1
    sequence: int = Field(ge=0, strict=True)
    previous: Digest | None
    review: Review
    digest: Digest


class AuditedReview(FrozenModel):
    review: Review
    digest: Digest
    current: bool
    issues: tuple[str, ...]


class ReviewAudit(FrozenModel):
    entries: tuple[AuditedReview, ...] = ()
    head: str | None = None
    issues: tuple[str, ...] = ()


def _artifacts(guard: WriteGuard) -> tuple[Artifact, ...]:
    artifacts = []
    for path in sorted(guard.directory.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"artifact is a symbolic link: {path}")
        if not path.is_file():
            continue
        relative = path.relative_to(guard.directory).as_posix()
        if relative in {JOURNAL, LOCK}:
            continue
        guard.confirm()
        with WriteGuard(path.parent).open(path.name, "rb") as stream:
            digest = sha256(stream.read()).hexdigest()
        artifacts.append(Artifact(path=relative, sha256=digest))
    return tuple(artifacts)


def freeze_subject(directory: Path, *, problem_id: str, repeat: int) -> Subject:
    """Freeze all attempt files, excluding only this owner's journal and lock."""
    guard = WriteGuard(directory)
    before = _artifacts(guard)
    attempt = read_attempt(directory)
    if attempt["status"] != "complete":
        raise ValueError("adjudication requires a complete journaled batch attempt")
    after = _artifacts(guard)
    if before != after:
        raise ValueError("attempt artifacts changed while freezing the subject")
    return Subject(attempt_id=attempt["manifest"]["attempt_id"], problem_id=problem_id,
                   repeat=repeat, artifacts=after)


def _current(directory: Path, subject: Subject) -> tuple[str, ...]:
    try:
        current = freeze_subject(directory, problem_id=subject.problem_id, repeat=subject.repeat)
        return () if current == subject else ("stale adjudication subject: attempt artifacts changed",)
    except (OSError, ValueError) as error:
        return (f"stale adjudication subject: {error}",)


def audit_reviews(directory: Path) -> ReviewAudit:
    """Read intact history without writing; corrupt tails are never repaired."""
    entries: list[AuditedReview] = []
    head = None
    try:
        guard = WriteGuard(directory)
        if not guard.path(JOURNAL).exists():
            return ReviewAudit()
        with guard.open(JOURNAL, "rb") as stream:
            lines = stream.readlines()
        first_identity = None
        for sequence, line in enumerate(lines):
            if not line.endswith(b"\n"):
                raise ValueError("torn trailing record")
            record = Record.model_validate_json(line)
            value = record.model_dump(mode="json", exclude={"digest"})
            if record.sequence != sequence or record.previous != head or record.digest != json_digest(value):
                raise ValueError("record content, sequence or prior head differs")
            subject = record.review.subject
            identity = (subject.attempt_id, subject.problem_id, subject.repeat)
            if first_identity is not None and identity != first_identity:
                raise ValueError("journal names different attempt/problem/repeat identities")
            first_identity = identity
            issues = _current(directory, subject)
            entries.append(AuditedReview(review=record.review, digest=record.digest,
                                         current=not issues, issues=issues))
            head = record.digest
        return ReviewAudit(entries=tuple(entries), head=head)
    except (OSError, ValueError) as error:
        return ReviewAudit(entries=tuple(entries), head=head, issues=(f"adjudication journal: {error}",))


def append_review(directory: Path, subject: Subject, *, actor: str,
                  decision: Literal["uphold", "challenge", "withdraw"], reason: str,
                  expected_head: str | None) -> Record:
    """Append under a prior-head CAS; callers must read before superseding a review."""
    review = Review(subject=subject, actor=actor, at=datetime.now(UTC),
                    decision=decision, reason=reason)
    guard = WriteGuard(directory)
    with FileLock(guard.path(LOCK)):
        history = audit_reviews(directory)
        if history.issues:
            raise ValueError("; ".join(history.issues))
        if history.head != expected_head:
            raise ValueError("adjudication prior head changed; read the current history")
        if history.entries:
            previous = history.entries[0].review.subject
            if (previous.attempt_id, previous.problem_id, previous.repeat) != (
                    subject.attempt_id, subject.problem_id, subject.repeat):
                raise ValueError("adjudication journal already names another attempt/problem/repeat")
        issues = _current(directory, subject)
        if issues:
            raise ValueError("; ".join(issues))
        value = {"schema_version": 1, "sequence": len(history.entries), "previous": history.head,
                 "review": review.model_dump(mode="json")}
        record = Record(**value, digest=json_digest(value))
        with guard.open(JOURNAL, "a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(record.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":")) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        return record
