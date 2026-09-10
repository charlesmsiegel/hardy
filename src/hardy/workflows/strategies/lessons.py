"""Source-bound replay of failed textual proof candidates.

Lessons quote a failure of one exact candidate in one frozen task; they do not
infer that a theorem or a tactic family is impossible. Compact and full replay
select the same recorded attempts, retaining complete artifact identities when
display fields are shortened. Preflight responses for a different source are
quarantined, because they cannot support advice about the candidate's proof.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import PurePosixPath
from typing import Literal
from uuid import UUID, uuid4

from hardy.formal.verifier import verification_source
from hardy.foundation.values import FrozenModel
from hardy.workflows.contracts import RunPhase
from hardy.workflows.storage import ArtifactIdentity, RunStore, TrajectoryEvent
from hardy.workflows.strategies.best_first import CandidateObservation, ProofCandidate
from hardy.workflows.strategies.contracts import ProofOutcome, ProofTask

ReplayMode = Literal["full", "compact"]


class FailedLesson(FrozenModel):
    candidate_id: str
    parent_id: str | None
    tried: str
    lean_said: str
    do_not_repeat: str
    truncated_fields: tuple[str, ...]
    source_artifacts: tuple[ArtifactIdentity, ...]


class QuarantinedAttempt(FrozenModel):
    candidate_id: str
    reason: str
    source_artifacts: tuple[ArtifactIdentity, ...]


class HistoryReplay(FrozenModel):
    run_id: UUID
    store_uri: str
    mode: ReplayMode
    text: str
    task_sha256: str
    claim_sha256: str
    lessons: tuple[FailedLesson, ...]
    quarantined: tuple[QuarantinedAttempt, ...]
    source_artifacts: tuple[ArtifactIdentity, ...]
    trajectory_prefix_sha256: str
    trajectory_events: int
    omitted_attempts: int
    field_chars: int
    max_lessons: int


class _CandidateRecord(FrozenModel):
    candidate_id: str
    parent_id: str | None
    candidate: ProofCandidate


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _recorded_bytes(value: FrozenModel) -> bytes:
    """The exact RunStore JSON encoding used by frontier artifact writes."""
    return (json.dumps(value.model_dump(mode="json"), ensure_ascii=False,
                       indent=2, sort_keys=True) + "\n").encode("utf-8")


def _read(store: RunStore, relative: str) -> tuple[bytes, ArtifactIdentity]:
    path = PurePosixPath(relative)
    if (path.is_absolute() or not path.parts
            or any(part in {".", ".."} or "\\" in part or ":" in part for part in path.parts)):
        raise ValueError("invalid replay artifact path")
    root = store.path.resolve()
    target = root.joinpath(*path.parts).resolve()
    if not target.is_relative_to(root):
        raise ValueError("replay artifact escapes the run store")
    try:
        raw = target.read_bytes()
    except OSError as exc:
        raise ValueError(f"replay artifact unavailable: {relative}") from exc
    return raw, ArtifactIdentity(relative_path=relative, byte_count=len(raw), sha256=_digest(raw))


def _trajectory(
    store: RunStore, count: int | None = None,
) -> tuple[bytes, tuple[TrajectoryEvent, ...]]:
    raw = _read(store, "trajectory.jsonl")[0] if store.trajectory_path.exists() else b""
    lines = raw.splitlines(keepends=True)
    if count is not None:
        if count < 0 or len(lines) < count:
            raise ValueError("replay trajectory prefix is missing")
        lines = lines[:count]
    events = []
    for sequence, line in enumerate(lines):
        if not line.endswith(b"\n"):
            raise ValueError("replay trajectory contains an unfinished event")
        event = TrajectoryEvent.model_validate_json(line)
        if event.run_id != store.run_id or event.sequence != sequence:
            raise ValueError("replay trajectory identity or sequence is invalid")
        events.append(event)
    return b"".join(lines), tuple(events)


def _attempts(store: RunStore, task: ProofTask, events: tuple[TrajectoryEvent, ...]):
    """Authenticate disk against both the proposal event and the check event."""
    candidates = {}
    checked = set()
    attempts = []
    for event in events:
        if event.kind == "best_first.candidate":
            payload = dict(event.payload)
            artifact = ArtifactIdentity.model_validate(payload.pop("artifact"))
            record = _CandidateRecord.model_validate(payload)
            if record.candidate_id in candidates:
                raise ValueError("duplicate recorded candidate identity")
            if record.parent_id is not None and record.parent_id not in checked:
                raise ValueError("candidate parent has no earlier recorded check")
            candidates[record.candidate_id] = (record, artifact)
        elif event.kind == "best_first.check":
            observation = CandidateObservation.model_validate(event.payload)
            candidate_id = observation.candidate_id
            if candidate_id not in candidates or candidate_id in checked:
                raise ValueError("check has a missing or duplicate recorded candidate")
            record, expected = candidates[candidate_id]
            if observation.candidate != record.candidate or observation.parent_id != record.parent_id:
                raise ValueError("check disagrees with its recorded candidate")
            candidate_raw, candidate_ref = _read(store, expected.relative_path)
            if candidate_ref != expected or _CandidateRecord.model_validate_json(candidate_raw) != record:
                raise ValueError("candidate artifact changed after its recorded proposal")
            folder = PurePosixPath(expected.relative_path).parent
            if (folder.parent != PurePosixPath("best_first/candidates")
                    or not folder.name.isdigit()
                    or PurePosixPath(expected.relative_path).name != "candidate.json"
                    or observation.submission_artifact != (folder / "submission.json").as_posix()
                    or observation.result_artifact != (folder / "result.json").as_posix()):
                raise ValueError("check artifact paths disagree with its candidate")
            submission_raw, submission_ref = _read(store, observation.submission_artifact)
            if submission_raw != _recorded_bytes(record.candidate.submission):
                raise ValueError("submission artifact disagrees with its recorded check")
            source_raw, source_ref = _read(store, (folder / "source.lean").as_posix())
            source = verification_source(task.claim, record.candidate.submission.proof_body,
                                         task.declared_assumptions)
            if source_raw != source.encode("utf-8") or source_ref.sha256 != candidate_id:
                raise ValueError("candidate source does not match the exact frozen task")
            result_raw, result_ref = _read(store, observation.result_artifact)
            if result_raw != _recorded_bytes(observation.result):
                raise ValueError("result artifact disagrees with its recorded check")
            checked.add(candidate_id)
            if observation.result.verified:
                ProofOutcome(task=task, status="submitted", submission=record.candidate.submission,
                             evidence=observation.result.evidence)
                continue
            refs = (candidate_ref, submission_ref, source_ref, result_ref)
            attempts.append((observation, refs, {
                "candidate": json.loads(candidate_raw), "submission": json.loads(submission_raw),
                "source": source_raw.decode("utf-8"), "result": json.loads(result_raw),
                "complete_artifacts": [ref.model_dump(mode="json") for ref in refs]}))
    return attempts


def _clip(text: str, limit: int, name: str, truncated: list[str]) -> str:
    if len(text) <= limit:
        return text
    truncated.append(name)
    return text[:limit - 1] + "…"


def replay_history(
    store: RunStore, task: ProofTask, *, mode: ReplayMode = "compact",
    field_chars: int = 512, max_lessons: int = 32,
) -> HistoryReplay:
    """Read a consistent recorded prefix, including during a frontier attempt.

    ``max_lessons`` selects the most recent failed attempts in both modes,
    including quarantined responses. Full replay preserves complete selected
    records; compact replay bounds display fields and marks every truncation.
    Neither mode implies that a failure proves a theorem impossible.
    """
    prefix, events = _trajectory(store)
    return _replay(store, task, mode, field_chars, max_lessons, prefix, events)


def _replay(store, task, mode, field_chars, max_lessons, prefix, events) -> HistoryReplay:
    if mode not in {"full", "compact"}:
        raise ValueError("replay mode must be full or compact")
    for bound in (field_chars, max_lessons):
        if isinstance(bound, bool) or not isinstance(bound, int) or bound < 1:
            raise ValueError("replay field and attempt bounds must be positive integers")
    task_raw, task_ref = _read(store, "best_first/task.json")
    if ProofTask.model_validate_json(task_raw) != task:
        raise ValueError("replay task differs from the requested frozen task")
    attempts = _attempts(store, task, events)
    omitted = max(0, len(attempts) - max_lessons)
    selected = attempts[omitted:]
    lessons, quarantined, full_records = [], [], []
    artifacts = [task_ref]
    lines = [f"Recorded failed attempts for frozen claim {task.claim.content_hash}.",
             "These observations concern exact candidates in this task's recorded context."]
    if omitted:
        lines.append(f"{omitted} earlier failed attempt(s) omitted by the shared replay limit.")
    for observation, refs, full_record in selected:
        artifacts.extend(refs)
        source_ref = refs[2]
        if observation.result.source_sha256 != source_ref.sha256:
            reason = ("Result source differs from candidate source; quarantined as an unbound "
                      "or preflight response. No proof-specific lesson is derived.")
            quarantine = QuarantinedAttempt(candidate_id=observation.candidate_id,
                                             reason=reason, source_artifacts=refs)
            quarantined.append(quarantine)
            lines.append(json.dumps({"quarantined": quarantine.model_dump(mode="json")},
                                    ensure_ascii=False, sort_keys=True))
            full_records.append({**full_record, "quarantined": reason})
            continue
        truncated = []
        result = observation.result
        reason = result.reason.name if result.reason else "UNKNOWN"
        feedback = reason + "\n" + "\n".join(
            f"{diagnostic.severity}: {diagnostic.message}" for diagnostic in result.diagnostics)
        lesson = FailedLesson(candidate_id=observation.candidate_id, parent_id=observation.parent_id,
            tried=_clip(observation.candidate.submission.proof_body, field_chars, "tried", truncated),
            lean_said=_clip(feedback, field_chars, "lean_said", truncated),
            do_not_repeat=_clip(f"Do not blindly repeat {observation.candidate_id} here.",
                                field_chars, "do_not_repeat", truncated),
            truncated_fields=tuple(truncated), source_artifacts=refs)
        lessons.append(lesson)
        lines.append(json.dumps({"candidate_id": lesson.candidate_id, "tried": lesson.tried,
            "Lean said": lesson.lean_said, "do not repeat": lesson.do_not_repeat,
            "truncated_fields": lesson.truncated_fields,
            "complete_artifacts": [ref.model_dump(mode="json") for ref in refs]},
            ensure_ascii=False, sort_keys=True))
        full_records.append(full_record)
    if mode == "full":
        lines = lines[:3 if omitted else 2]
        lines.append(json.dumps(full_records, ensure_ascii=False, sort_keys=True))
    return HistoryReplay(run_id=store.run_id, store_uri=store.path.resolve().as_uri(), mode=mode,
        text="\n".join(lines), task_sha256=task_ref.sha256, claim_sha256=task.claim.content_hash,
        lessons=tuple(lessons), quarantined=tuple(quarantined), source_artifacts=tuple(artifacts),
        trajectory_prefix_sha256=_digest(prefix), trajectory_events=len(events),
        omitted_attempts=omitted, field_chars=field_chars, max_lessons=max_lessons)


def record_replay(store: RunStore, replay: HistoryReplay) -> ArtifactIdentity:
    """Reauthenticate a replay immediately before giving its text to a provider.

    Existing events may have been appended since the replay was built. The
    recorded prefix and every selected artifact must still produce this exact
    replay. This checks freshness; it does not provide a filesystem sandbox.
    """
    if replay.run_id != store.run_id or replay.store_uri != store.path.resolve().as_uri():
        raise ValueError("replay belongs to a different run store")
    prefix, events = _trajectory(store, replay.trajectory_events)
    if _digest(prefix) != replay.trajectory_prefix_sha256:
        raise ValueError("replay trajectory prefix changed")
    task_raw, _ = _read(store, "best_first/task.json")
    task = ProofTask.model_validate_json(task_raw)
    derived = _replay(store, task, replay.mode, replay.field_chars, replay.max_lessons, prefix, events)
    if derived != replay:
        raise ValueError("replay sources or derived text changed")
    # A fresh random artifact name preserves prior replays even if recording
    # was interrupted between the artifact write and the trajectory append.
    artifact = store.write_json(PurePosixPath(f"strategy_history/replays/{uuid4().hex}.json"), replay)
    store.append("strategy.history_replay", {
        "mode": replay.mode, "artifact": artifact.model_dump(mode="json"),
        "task_sha256": replay.task_sha256, "text_sha256": _digest(replay.text.encode("utf-8")),
        "source_artifacts": [ref.model_dump(mode="json") for ref in replay.source_artifacts],
        "trajectory_prefix_sha256": replay.trajectory_prefix_sha256,
        "trajectory_events": replay.trajectory_events,
        "omitted_attempts": replay.omitted_attempts,
    }, phase=RunPhase.PROVING)
    return artifact
