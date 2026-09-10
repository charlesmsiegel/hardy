"""Compact replay quotes exact failed attempts; it cannot invent general impossibility."""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import PurePosixPath
from uuid import uuid4

import pytest
from test_iterative_strategy import _result
from test_strategy_contracts import _claim, _task

from hardy.formal.lean import LeanDiagnostic
from hardy.formal.verifier import verification_source
from hardy.workflows.contracts import ProofSubmission, RunLimits
from hardy.workflows.storage import RunStore
from hardy.workflows.strategies.best_first import BestFirstStrategy, ProofCandidate
from hardy.workflows.strategies.lessons import record_replay, replay_history


def _run(tmp_path, *, bodies=("by first", "by second"), mismatch=False, accepted=False,
         on_propose=lambda store, task: None):
    task = _task(limits=RunLimits(official_checks=len(bodies) + 1))
    store = RunStore.create(tmp_path, "lessons", now=datetime.now(UTC), run_id=uuid4())

    def propose(task, parent):
        on_propose(store, task)
        if parent is not None:
            return ()
        return tuple(ProofCandidate(submission=ProofSubmission(proof_body=body,
            informal_proof="Original informal account " * 100), priority=index)
            for index, body in enumerate(bodies))

    def verify(task, submission, check_store):
        result = _result(task, submission.proof_body,
                         accepted=accepted and submission.proof_body == bodies[-1])
        if result.verified:
            return result
        source = verification_source(task.claim, submission.proof_body, task.declared_assumptions)
        return result.model_copy(update={
            "source_sha256": "0" * 64 if mismatch else hashlib.sha256(source.encode()).hexdigest(),
            "diagnostics": (LeanDiagnostic(severity="error", message="unsolved goals: " + "n = n; " * 300),),
        })

    strategy = BestFirstStrategy(propose=propose, verify=verify, store=store,
        check_cancelled=lambda: None, active_elapsed=lambda: 0)
    strategy.run(task)
    return store, task


def test_compact_and_full_cover_same_failures_and_retain_exact_source_access(tmp_path):
    store, task = _run(tmp_path, bodies=("by\n  -- comment\n  first", "by second"))
    full = replay_history(store, task, mode="full", field_chars=100)
    compact = replay_history(store, task, mode="compact", field_chars=100)
    assert len(full.lessons) == len(compact.lessons) == 2
    assert full.source_artifacts == compact.source_artifacts
    assert len(compact.text.encode()) < len(full.text.encode())
    assert "by\\n  -- comment\\n  first" in full.text
    assert "tried" in compact.text and "Lean said" in compact.text
    assert "do not repeat" in compact.text
    assert "impossible" not in compact.text
    for lesson in compact.lessons:
        assert "lean_said" in lesson.truncated_fields
        assert len(lesson.lean_said) <= 100
        assert lesson.candidate_id in lesson.do_not_repeat
        for artifact in lesson.source_artifacts:
            raw = (store.path / artifact.relative_path).read_bytes()
            assert hashlib.sha256(raw).hexdigest() == artifact.sha256


def test_history_works_during_next_expansion_without_outcome_file(tmp_path):
    replays = []

    def capture(store, task):
        assert not (store.path / "best_first/outcome.json").exists()
        replays.append(replay_history(store, task, mode="compact"))

    store, task = _run(tmp_path, on_propose=capture)
    assert [len(replay.lessons) for replay in replays] == [0, 1, 2]


def test_accepted_results_are_excluded_and_source_mismatch_is_quarantined(tmp_path):
    store, task = _run(tmp_path, accepted=True, mismatch=True)
    replay = replay_history(store, task)
    assert not replay.lessons
    assert len(replay.quarantined) == 1
    assert "source" in replay.quarantined[0].reason
    assert "quarantined" in replay.text
    assert "do not repeat" not in replay.text


@pytest.mark.parametrize("path", ["task.json", "candidates/1/source.lean",
    "candidates/1/candidate.json", "candidates/1/submission.json", "candidates/1/result.json"])
def test_changed_artifacts_are_refused_against_frozen_task_and_trajectory(tmp_path, path):
    store, task = _run(tmp_path)
    target = store.path / "best_first" / path
    if target.suffix == ".lean":
        target.write_text("theorem changed : True := by trivial")
    else:
        record = json.loads(target.read_text())
        if path == "task.json":
            record["declared_assumptions"] = [{"name": "assumeEverything", "statement": "False",
                "source": "invented", "justification": "not authorized"}]
        elif path.endswith("candidate.json"):
            record["candidate"]["priority"] = -100
        elif path.endswith("submission.json"):
            record["informal_proof"] = "changed prose"
        else:
            record["diagnostics"] = []
        target.write_text(json.dumps(record))
    with pytest.raises(ValueError):
        replay_history(store, task)


def test_foreign_task_is_refused_even_when_sources_are_internally_consistent(tmp_path):
    store, task = _run(tmp_path)
    other = task.model_copy(update={"claim": _claim(theorem_name="another")})
    with pytest.raises(ValueError, match="task"):
        replay_history(store, other)


def test_compact_and_full_apply_same_visible_attempt_limit(tmp_path):
    store, task = _run(tmp_path)
    full = replay_history(store, task, mode="full", max_lessons=1)
    compact = replay_history(store, task, mode="compact", max_lessons=1)
    assert full.omitted_attempts == compact.omitted_attempts == 1
    assert [lesson.candidate_id for lesson in full.lessons] == [
        lesson.candidate_id for lesson in compact.lessons]
    assert full.lessons[0].tried == "by second"
    assert "1 earlier failed attempt" in full.text
    assert "1 earlier failed attempt" in compact.text


def test_record_replay_preserves_mode_exact_text_and_append_only_provenance(tmp_path):
    store, task = _run(tmp_path)
    full = replay_history(store, task, mode="full")
    artifact = record_replay(store, full)
    compact = replay_history(store, task, mode="compact")
    second = record_replay(store, compact)
    assert artifact.relative_path != second.relative_path
    saved = json.loads((store.path / artifact.relative_path).read_text())
    assert saved["mode"] == "full" and saved["text"] == full.text
    assert saved["source_artifacts"]
    assert saved["trajectory_prefix_sha256"] == full.trajectory_prefix_sha256
    events = [json.loads(line) for line in store.trajectory_path.read_text().splitlines()]
    assert events[-1]["kind"] == "strategy.history_replay"
    assert events[-1]["payload"]["mode"] == "compact"


@pytest.mark.parametrize("mutate", ["artifact", "prefix", "foreign_store"])
def test_record_replay_rechecks_sources_before_provider_receives_it(tmp_path, mutate):
    store, task = _run(tmp_path)
    replay = replay_history(store, task)
    if mutate == "artifact":
        (store.path / "best_first/candidates/1/source.lean").write_text("by changed")
    elif mutate == "prefix":
        store.trajectory_path.write_text(store.trajectory_path.read_text().replace("first", "other"))
    else:
        store = RunStore.create(tmp_path, "foreign", now=datetime.now(UTC), run_id=uuid4())
    with pytest.raises(ValueError):
        record_replay(store, replay)


def test_replay_refuses_check_event_paths_that_escape_the_store(tmp_path):
    store, task = _run(tmp_path)
    events = [json.loads(line) for line in store.trajectory_path.read_text().splitlines()]
    check = next(event for event in events if event["kind"] == "best_first.check")
    check["payload"]["result_artifact"] = "../../foreign.json"
    store.write_text(PurePosixPath("trajectory.jsonl"),
                     "\n".join(json.dumps(event) for event in events) + "\n")
    with pytest.raises(ValueError):
        replay_history(store, task)


@pytest.mark.parametrize("kwargs", [{"mode": "summary"}, {"field_chars": 0},
                                   {"max_lessons": -1}])
def test_invalid_replay_configuration_is_refused(tmp_path, kwargs):
    store, task = _run(tmp_path)
    with pytest.raises(ValueError):
        replay_history(store, task, **kwargs)


def test_every_compact_field_is_bounded_with_explicit_truncation(tmp_path):
    store, task = _run(tmp_path, bodies=("by\n  -- lengthy candidate\n  first",))
    replay = replay_history(store, task, field_chars=20)
    lesson = replay.lessons[0]
    assert all(len(getattr(lesson, name)) <= 20 for name in ("tried", "lean_said", "do_not_repeat"))
    assert set(lesson.truncated_fields) == {"tried", "lean_said", "do_not_repeat"}
    assert lesson.candidate_id in replay.text


@pytest.mark.parametrize("artifact", ["submission.json", "result.json"])
def test_artifact_byte_changes_are_not_silently_reissued_with_new_digests(tmp_path, artifact):
    store, task = _run(tmp_path)
    target = store.path / "best_first/candidates/1" / artifact
    target.write_bytes(target.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="artifact"):
        replay_history(store, task)


def test_replay_can_be_recorded_after_append_but_cannot_inject_invented_lessons(tmp_path):
    from hardy.workflows.contracts import RunPhase

    store, task = _run(tmp_path)
    replay = replay_history(store, task)
    store.append("provider.observation", {"status": "ready"}, phase=RunPhase.PROVING)
    artifact = record_replay(store, replay)
    assert (store.path / artifact.relative_path).is_file()
    forged = replay.model_copy(update={"text": "The theorem cannot be proved."})
    with pytest.raises(ValueError, match="derived text"):
        record_replay(store, forged)


@pytest.mark.parametrize("fault", ["sequence", "missing_candidate", "duplicate_check", "missing_file"])
def test_broken_recorded_lineage_is_refused(tmp_path, fault):
    store, task = _run(tmp_path)
    events = [json.loads(line) for line in store.trajectory_path.read_text().splitlines()]
    if fault == "sequence":
        events[0]["sequence"] = 10
    elif fault == "missing_candidate":
        events[0]["kind"] = "unknown"
    elif fault == "duplicate_check":
        check = next(event for event in events if event["kind"] == "best_first.check")
        events.append({**check, "sequence": len(events)})
    else:
        (store.path / "best_first/candidates/1/result.json").unlink()
    store.write_text(PurePosixPath("trajectory.jsonl"),
                     "\n".join(json.dumps(event) for event in events) + "\n")
    with pytest.raises(ValueError):
        replay_history(store, task)
