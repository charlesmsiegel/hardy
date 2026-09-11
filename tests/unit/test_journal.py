"""A hash-chained journal keeps exact history and refuses stale or damaged appends."""

from __future__ import annotations

import json
import threading

import pytest

from hardy.foundation.journal import Journal, JournalError, StaleRevision
from hardy.foundation.values import FrozenModel


class Note(FrozenModel):
    id: str
    text: str


class Mark(FrozenModel):
    id: str
    value: int


TYPES = {"Note": Note, "Mark": Mark}


def make(tmp_path):
    return Journal(tmp_path / "journal", types=TYPES)


def test_empty_directory_reads_as_revision_zero(tmp_path):
    snapshot = make(tmp_path).read()
    assert snapshot.revision == 0
    assert snapshot.records == ()
    assert snapshot.head_digest is None


def test_append_and_replay_verifies_the_chain(tmp_path):
    journal = make(tmp_path)
    first = journal.append([Note(id="a", text="one")], expected_revision=0)
    second = journal.append([Mark(id="m", value=2)], expected_revision=1)
    assert first.revision == 1 and second.revision == 2
    replayed = make(tmp_path).read()
    assert replayed.revision == 2
    assert replayed.of(Note) == (Note(id="a", text="one"),)
    assert replayed.of(Mark) == (Mark(id="m", value=2),)
    assert replayed.head_digest == second.head_digest
    files = sorted(p.name for p in (tmp_path / "journal").iterdir() if p.suffix == ".json")
    assert files == ["00000000000000000001.json", "00000000000000000002.json"]


def test_stale_revision_is_refused(tmp_path):
    journal = make(tmp_path)
    journal.append([Note(id="a", text="one")], expected_revision=0)
    with pytest.raises(StaleRevision):
        journal.append([Note(id="b", text="two")], expected_revision=0)
    assert journal.read().revision == 1


def test_expected_revision_must_be_an_int(tmp_path):
    with pytest.raises(StaleRevision):
        make(tmp_path).append([Note(id="a", text="one")], expected_revision="0")  # type: ignore[arg-type]


def test_empty_transaction_is_refused(tmp_path):
    with pytest.raises(JournalError):
        make(tmp_path).append([], expected_revision=0)


def test_unknown_record_type_is_refused(tmp_path):
    class Other(FrozenModel):
        id: str

    with pytest.raises(JournalError):
        make(tmp_path).append([Other(id="x")], expected_revision=0)


def test_tampered_file_is_refused_on_read(tmp_path):
    journal = make(tmp_path)
    journal.append([Note(id="a", text="one")], expected_revision=0)
    path = tmp_path / "journal" / "00000000000000000001.json"
    event = json.loads(path.read_text(encoding="utf-8"))
    event["records"][0]["value"]["text"] = "changed"
    path.write_text(json.dumps(event), encoding="utf-8")
    with pytest.raises(JournalError):
        journal.read()


def test_sequence_gap_is_refused(tmp_path):
    journal = make(tmp_path)
    journal.append([Note(id="a", text="one")], expected_revision=0)
    journal.append([Note(id="b", text="two")], expected_revision=1)
    (tmp_path / "journal" / "00000000000000000001.json").unlink()
    with pytest.raises(JournalError):
        journal.read()


def test_validator_runs_before_the_write_and_can_refuse(tmp_path):
    journal = make(tmp_path)

    def refuse(before, after):
        raise ValueError("no marks allowed")

    with pytest.raises(ValueError, match="no marks"):
        journal.append([Mark(id="m", value=1)], expected_revision=0, validate=refuse)
    assert journal.read().revision == 0
    assert not list((tmp_path / "journal").glob("*.json"))


def test_concurrent_appenders_never_both_win_one_revision(tmp_path):
    journal = make(tmp_path)
    outcomes: list[str] = []
    barrier = threading.Barrier(2)

    def worker(name):
        barrier.wait()
        try:
            journal.append([Note(id=name, text=name)], expected_revision=0)
            outcomes.append("ok")
        except StaleRevision:
            outcomes.append("stale")

    threads = [threading.Thread(target=worker, args=(n,)) for n in ("x", "y")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(outcomes) == ["ok", "stale"]
    assert journal.read().revision == 1
