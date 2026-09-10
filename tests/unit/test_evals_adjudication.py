"""Attributed evaluation reviews never rewrite the attempt being reviewed."""
import importlib
import json
from concurrent.futures import ThreadPoolExecutor

import pytest
import test_recorded_runs as fixtures


def owner():
    return importlib.import_module("hardy.evals.adjudication")


def subject(tmp_path):
    directory = fixtures._verified(tmp_path)
    return directory, owner().freeze_subject(directory, problem_id="true-fixture", repeat=0)


def append(directory, frozen, head=None, decision="challenge"):
    return owner().append_review(directory, frozen, actor="reviewer:alice", decision=decision,
                                 reason="The informal comparison needs another read.", expected_head=head)


def test_review_history_survives_restart_without_rewriting_canonical_artifacts(tmp_path):
    directory, frozen = subject(tmp_path)
    before = {p.name: p.read_bytes() for p in directory.iterdir() if p.is_file()}
    first = append(directory, frozen)
    second = append(directory, frozen, first.digest, "withdraw")
    audit = owner().audit_reviews(directory)
    assert audit.issues == () and audit.head == second.digest
    assert [entry.review.decision for entry in audit.entries] == ["challenge", "withdraw"]
    assert all(entry.current for entry in audit.entries)
    assert audit.entries[0].review.subject == frozen
    assert audit.entries[0].review.actor == "reviewer:alice"
    assert audit.entries[0].review.at.tzinfo is not None
    assert all((directory / name).read_bytes() == data for name, data in before.items())


@pytest.mark.parametrize("mutation", ["change", "delete", "add"])
def test_changed_artifacts_stale_history_and_refuse_new_annotation(tmp_path, mutation):
    directory, frozen = subject(tmp_path)
    first = append(directory, frozen)
    if mutation == "change":
        (directory / "writeup.md").write_text("changed judgment")
    elif mutation == "delete":
        (directory / "writeup.md").unlink()
    else:
        (directory / "new-evidence.txt").write_text("later")
    report = owner().audit_reviews(directory)
    assert report.head == first.digest and len(report.entries) == 1
    assert not report.entries[0].current
    assert report.entries[0].issues
    with pytest.raises(ValueError, match="stale|artifact"):
        append(directory, frozen, first.digest)
    assert len(owner().audit_reviews(directory).entries) == 1


def test_concurrent_prior_head_allows_one_append(tmp_path):
    directory, frozen = subject(tmp_path)

    def attempt(_):
        try:
            return append(directory, frozen).digest
        except ValueError as error:
            return str(error)

    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(attempt, range(2)))
    assert sum("head" in result for result in results) == 1
    assert len(owner().audit_reviews(directory).entries) == 1


@pytest.mark.parametrize("attack", ["content", "reorder", "torn"])
def test_corrupt_history_refuses_append_and_retains_intact_prefix(tmp_path, attack):
    directory, frozen = subject(tmp_path)
    first = append(directory, frozen)
    append(directory, frozen, first.digest, "uphold")
    path = directory / "adjudications.jsonl"
    lines = path.read_bytes().splitlines(keepends=True)
    if attack == "content":
        lines[1] = lines[1].replace(b"reviewer:alice", b"reviewer:mallory")
    elif attack == "reorder":
        lines.reverse()
    else:
        lines.append(b'{"torn":')
    path.write_bytes(b"".join(lines))
    report = owner().audit_reviews(directory)
    assert report.issues
    assert len(report.entries) == {"content": 1, "reorder": 0, "torn": 2}[attack]
    with pytest.raises(ValueError, match="journal"):
        append(directory, frozen, report.head)


def test_missing_journal_is_unreviewed_and_legacy_identity_is_not_invented(tmp_path):
    directory, _ = subject(tmp_path)
    assert owner().audit_reviews(directory).entries == ()
    trajectory = json.loads((directory / "trajectory.json").read_text())
    trajectory.pop("attempt_receipt")
    trajectory["schema_version"] = 1
    (directory / "trajectory.json").write_text(json.dumps(trajectory))
    (directory / "attempt.json").unlink()
    (directory / "attempt.jsonl").unlink()
    with pytest.raises(ValueError, match="complete"):
        owner().freeze_subject(directory, problem_id="true-fixture", repeat=0)


def test_subject_from_other_attempt_and_blank_attribution_are_refused(tmp_path):
    directory, frozen = subject(tmp_path / "first")
    other, _ = subject(tmp_path / "second")
    with pytest.raises(ValueError, match="stale|artifact|attempt"):
        append(other, frozen)
    with pytest.raises(ValueError, match="actor"):
        owner().append_review(directory, frozen, actor=" ", decision="uphold", reason="reason", expected_head=None)


def test_new_artifact_review_retains_stale_history_without_relabeling_attempt(tmp_path):
    directory, frozen = subject(tmp_path)
    first = append(directory, frozen)
    (directory / "writeup.md").write_text("an explicitly revised writeup")
    revised = owner().freeze_subject(directory, problem_id=frozen.problem_id, repeat=frozen.repeat)
    second = append(directory, revised, first.digest, "uphold")
    audit = owner().audit_reviews(directory)
    assert audit.issues == () and audit.head == second.digest
    assert [entry.current for entry in audit.entries] == [False, True]
    with pytest.raises(ValueError, match="problem/repeat"):
        append(directory, revised.model_copy(update={"repeat": 1}), second.digest)
    assert owner().audit_reviews(directory).head == second.digest


def test_crash_after_append_leaves_readable_review(tmp_path):
    import subprocess
    import sys

    directory, _ = subject(tmp_path)
    code = '''import os,sys
from pathlib import Path
from hardy.evals.adjudication import freeze_subject, append_review
directory=Path(sys.argv[1])
subject=freeze_subject(directory,problem_id="true-fixture",repeat=0)
append_review(directory,subject,actor="reviewer:child",decision="challenge",reason="check again",expected_head=None)
os._exit(23)
'''
    child = subprocess.run([sys.executable, "-c", code, str(directory)], capture_output=True, timeout=15)
    assert child.returncode == 23, child.stderr
    audit = owner().audit_reviews(directory)
    assert audit.issues == () and len(audit.entries) == 1
    assert audit.entries[0].current and audit.entries[0].review.actor == "reviewer:child"
