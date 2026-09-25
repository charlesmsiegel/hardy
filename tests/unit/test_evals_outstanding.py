# tests/unit/test_evals_outstanding.py
"""What is left to sweep and to run, under the pooling key this checkout would produce."""
from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from hardy.corpus import taxonomy
from hardy.corpus.problems import Entry, ProblemSet, Review
from hardy.evals import outstanding, sweep
from hardy.formal.contracts import EnvironmentIdentity

IDENTITY = EnvironmentIdentity(lean_version="4.33.1", lean_commit="819816b2", mathlib_revision="v4.33.1", lake_manifest_sha256="m" * 64)


@pytest.fixture(autouse=True)
def _stub_environment_digest(monkeypatch):
    """`environment_digest_of_board` hashes the board's recorded `environment`
    and `host` through `sweep.environment_digest_of` (a real sha256, spec §3).
    These tests only care whether two boards agree or differ, so this reduces
    the digest to a value the fixture's own `host` names directly, rather than
    fighting a one-way hash to produce a chosen literal.
    """
    monkeypatch.setattr(outstanding.sweep, "environment_digest_of", lambda environment, host: host.get("digest", ""))


REFUSED_BY_AUDIT: dict[str, tuple[str, ...]] = {}


@pytest.fixture(autouse=True)
def _stub_self_audit(monkeypatch):
    """`scoreboard_self_issues` re-derives every row from its run directory,
    which these hand-written boards do not have. What is under test is what
    `outstanding` does with the audit's answer, so the answer is scripted:
    a board passes unless its label is in `REFUSED_BY_AUDIT`."""
    from hardy.evals import scoreboard

    REFUSED_BY_AUDIT.clear()
    monkeypatch.setattr(scoreboard, "scoreboard_self_issues",
                        lambda path, **kw: REFUSED_BY_AUDIT.get(path.name, ()))


def _board(path, *, ids: list[str] = (), key: tuple[str | None, str], repeats: int = 1,
           rows: list[dict] | None = None) -> None:
    run_digest, env_digest = key
    path.mkdir(parents=True)
    board = {
        "condition": {"run_procedure_digest": run_digest, "repeats": repeats},
        "environment": IDENTITY.model_dump(mode="json"),
        "host": {"digest": env_digest},
        "rows": rows if rows is not None else [
            {"id": id_, "repeat": k, "outcome": "solved"} for id_ in ids for k in range(repeats)
        ],
    }
    (path / "scoreboard.json").write_text(json.dumps(board), encoding="utf-8")


def _paths(tmp_path) -> dict:
    return {"problems_path": tmp_path / "corpus", "baseline_path": tmp_path / "baseline.json"}


def _problems() -> ProblemSet:
    base = {
        "input": "True.", "conclusion": "True", "expected": "true", "source": "textbook",
        "msc": ("11Axx",), "difficulty": "routine", "rationale": "test fixture",
        "witness": None, "witness_note": "test fixture",
    }
    t = Entry(id="t", name="T", **base)
    u_draft = Entry(id="u", name="U", **base)
    review = Review(
        reviewer="cms", reviewed_at="2026-09-03T00:00:00Z",
        statement_digest=u_draft.statement_digest(), prompt_digest=u_draft.prompt_digest(),
        msc=list(u_draft.msc), group=taxonomy.group_of(u_draft.msc[0]), verdict="faithful",
    )
    u = Entry(id="u", name="U", status="active", review=review, **base)
    f = Entry(id="f", name="F", twin_of="t", input="True.", conclusion="True", expected="false",
              source="textbook", msc=("11Axx",), difficulty="routine", rationale="test fixture",
              witness=None, witness_note="test fixture")
    return ProblemSet(entries=(t, u, f))


def _baseline() -> sweep.Baseline:
    return sweep.Baseline(
        created_at=datetime(2026, 9, 1, tzinfo=UTC), problems_sha256="p" * 64, environment=IDENTITY,
        heartbeat_budget=200000, wall_backstop_seconds=600.0, singles=sweep.SINGLES, chains=sweep.CHAINS,
        host=sweep.host_info(), problems=(), entries={},
    )


def test_evaluated_ids_counts_only_boards_under_the_same_key(tmp_path):
    _board(tmp_path / "a", ids=["t"], key=("run-digest", "env-digest"))
    _board(tmp_path / "b", ids=["u"], key=("other-digest", "env-digest"))
    admitted, refused = outstanding.poolable_boards(tmp_path, key=("run-digest", "env-digest"), **_paths(tmp_path))
    assert (admitted, refused) == (["a"], {})
    assert outstanding.evaluated_ids(tmp_path, boards=admitted) == ({"t"}, set())


def test_a_board_with_no_run_digest_is_not_counted_as_evidence(tmp_path):
    # Absence is staleness, not agreement: a board written before the gate
    # existed says nothing about which condition produced it.
    _board(tmp_path / "old", ids=["t"], key=(None, "env-digest"))
    assert outstanding.poolable_boards(tmp_path, key=("run-digest", "env-digest"), **_paths(tmp_path)) == ([], {})


def test_outstanding_lists_active_work_only(tmp_path):
    result = outstanding.outstanding(_problems(), _baseline(), tmp_path, key=("r", "e"), **_paths(tmp_path), procedure_digest="p" * 64)
    assert result["unevaluated_active"] == ["u"]      # `t` and `f` are candidates
    assert result["unbaselined_active"] == ["u"]
    assert result["partially_evaluated_active"] == []
    assert result["boards_counted"] == [] and result["boards_refused"] == {}


# --- Evidence is what `evals pool` would accept (#213) ---


def test_a_board_failing_its_self_audit_counts_nothing_and_is_named_refused(tmp_path):
    """`evals pool` refuses a board its own audit rejects, so none of that
    board's rows is evidence -- and `boards_counted` must not claim it."""
    key = ("r", "e")
    _board(tmp_path / "bad", ids=["u"], key=key)
    _board(tmp_path / "good", ids=["t"], key=key)
    REFUSED_BY_AUDIT["bad"] = ("runs/u/batch-0: the recorded-run audit reports findings",)
    result = outstanding.outstanding(_problems(), _baseline(), tmp_path, key=key, **_paths(tmp_path), procedure_digest="p" * 64)
    assert result["boards_counted"] == ["good"]
    assert result["boards_refused"] == {"bad": ["runs/u/batch-0: the recorded-run audit reports findings"]}
    assert result["unevaluated_active"] == ["u"]


def test_an_invalid_row_leaves_its_id_unevaluated(tmp_path):
    key = ("r", "e")
    _board(tmp_path / "a", key=key, rows=[{"id": "u", "repeat": 0, "outcome": "invalid"}])
    result = outstanding.outstanding(_problems(), _baseline(), tmp_path, key=key, **_paths(tmp_path), procedure_digest="p" * 64)
    assert result["unevaluated_active"] == ["u"]
    assert result["partially_evaluated_active"] == []


def test_an_entry_with_only_some_of_its_repeats_is_partial_not_evaluated(tmp_path):
    """An interrupted `--repeats 3` board holding only `(u, 0)`: counting `u`
    as done pools one sample beside other entries' three, and rerunning it
    whole claims `(u, 0)` twice, which `evals pool` refuses. It is reported
    apart, and not selected by the default run."""
    key = ("r", "e")
    _board(tmp_path / "a", key=key, repeats=3, rows=[{"id": "u", "repeat": 0, "outcome": "solved"}])
    result = outstanding.outstanding(_problems(), _baseline(), tmp_path, key=key, **_paths(tmp_path), procedure_digest="p" * 64)
    assert result["partially_evaluated_active"] == ["u"]
    assert result["unevaluated_active"] == []

    _board(tmp_path / "b", key=key, repeats=3, rows=[{"id": "u", "repeat": k, "outcome": "solved"} for k in (1, 2)])
    done = outstanding.outstanding(_problems(), _baseline(), tmp_path, key=key, **_paths(tmp_path), procedure_digest="p" * 64)
    assert done["partially_evaluated_active"] == [] and done["unevaluated_active"] == []


def _row() -> sweep.EntryBaseline:
    return sweep.EntryBaseline(tier=3, elaborates=False, attempts={}, closed_by=())


def test_an_active_entry_whose_row_would_not_be_carried_counts_as_unbaselined():
    """What `evals baseline`'s default sweeps: no row at all, or a row the
    sweep would not carry today because the statement moved since it was
    measured -- the entry `staleness` tells the operator to re-sweep."""
    problems = _problems()
    u = problems.by_id("u")
    fresh = _baseline().model_copy(update={"entries": {"u": _row()}, "statement_digests": {"u": u.statement_digest()}})
    assert outstanding.unbaselined_active(problems, fresh) == []
    moved = fresh.model_copy(update={"statement_digests": {"u": "x" * 64}})
    assert outstanding.unbaselined_active(problems, moved) == ["u"]
    unidentified = fresh.model_copy(update={"statement_digests": {}})
    assert outstanding.unbaselined_active(problems, unidentified) == ["u"]


def test_an_active_entry_whose_row_never_ran_counts_as_unbaselined():
    """A row whose stage A did not run is no measurement (#362), so the
    default `evals baseline` re-sweeps it rather than refusing with "every
    active entry already has a baseline row"."""
    problems = _problems()
    u = problems.by_id("u")
    never = {name: sweep.Attempt(status="not_run", message="panic") for name in (*sweep.SINGLES, *sweep.CHAINS)}
    row = sweep.EntryBaseline(tier=3, elaborates=True, attempts=never, closed_by=())
    baseline = _baseline().model_copy(update={"entries": {"u": row}, "statement_digests": {"u": u.statement_digest()}})
    assert outstanding.unbaselined_active(problems, baseline) == ["u"]


def test_the_baseline_default_after_a_moved_procedure_is_every_held_row():
    """What `evals todo` and a bare `evals baseline` share (review of #202):
    under a file swept by another procedure, nothing may be carried, so the
    default is every row the file holds -- candidates too -- plus the active
    entries it lacks, and it says why."""
    problems = _problems()
    t = problems.by_id("t")
    held = _baseline().model_copy(update={
        "entries": {"t": _row()}, "statement_digests": {"t": t.statement_digest()},
        "environment_digest": "e", "procedure_digest": "q" * 64,
    })
    ids, moved = outstanding.baseline_default(problems, held, environment_digest="e", procedure_digest="p" * 64)
    assert ids == ["t", "u"] and "procedure digest" in moved
    current = held.model_copy(update={"procedure_digest": "p" * 64})
    assert outstanding.baseline_default(problems, current, environment_digest="e", procedure_digest="p" * 64) == (["u"], None)


def test_the_baseline_default_includes_an_unmeasured_row_whatever_its_status():
    problems = _problems()
    t = problems.by_id("t")   # a candidate
    never = {name: sweep.Attempt(status="not_run", message="panic") for name in (*sweep.SINGLES, *sweep.CHAINS)}
    row = sweep.EntryBaseline(tier=3, elaborates=True, attempts=never, closed_by=())
    prior = _baseline().model_copy(update={
        "entries": {"t": row}, "statement_digests": {"t": t.statement_digest()},
        "environment_digest": "e", "procedure_digest": "p" * 64,
    })
    ids, moved = outstanding.baseline_default(problems, prior, environment_digest="e", procedure_digest="p" * 64)
    assert ids == ["t", "u"] and moved is None


# --- Codex on #393: evidence is a set of boards `evals pool` accepts together ---


def _three_entries() -> ProblemSet:
    """Three active entries, `x`, `y` and `z`, for the overlap example."""
    base = {
        "input": "True.", "conclusion": "True", "expected": "true", "source": "textbook",
        "msc": ("11Axx",), "difficulty": "routine", "rationale": "test fixture",
        "witness": None, "witness_note": "test fixture",
    }
    entries = []
    for id_ in ("x", "y", "z"):
        draft = Entry(id=id_, name=id_.upper(), **base)
        review = Review(
            reviewer="cms", reviewed_at="2026-09-03T00:00:00Z",
            statement_digest=draft.statement_digest(), prompt_digest=draft.prompt_digest(),
            msc=list(draft.msc), group=taxonomy.group_of(draft.msc[0]), verdict="faithful",
        )
        entries.append(Entry(id=id_, name=id_.upper(), status="active", review=review, **base))
    return ProblemSet(entries=tuple(entries))


def test_boards_that_overlap_on_a_slot_count_nothing_and_are_named_conflicting(tmp_path):
    """One repeat: A holds x and y, B holds y and z. Each passes its own audit,
    but `evals pool` refuses them together (`y repeat 0 appears in both`), so
    neither is evidence -- the union counted x, y and z complete and the
    default run selected nothing."""
    key = ("r", "e")
    _board(tmp_path / "a", ids=["x", "y"], key=key)
    _board(tmp_path / "b", ids=["y", "z"], key=key)
    result = outstanding.outstanding(_three_entries(), _baseline(), tmp_path, key=key, **_paths(tmp_path),
                                     procedure_digest="p" * 64)
    assert result["boards_counted"] == []
    assert result["boards_refused"] == {}
    assert set(result["boards_conflicting"]) == {"a", "b"}
    assert any("y repeat 0" in finding and "b" in finding for finding in result["boards_conflicting"]["a"])
    # A rerun of any of them would claim a slot one of the two still holds,
    # so none is selected: setting a board aside is the remedy.
    assert result["unevaluated_active"] == []
    assert result["partially_evaluated_active"] == []
    assert result["conflicted_active"] == ["x", "y", "z"]


def test_a_board_clear_of_the_conflict_still_counts(tmp_path):
    key = ("r", "e")
    _board(tmp_path / "a", ids=["x"], key=key)
    _board(tmp_path / "b", ids=["x"], key=key)
    _board(tmp_path / "c", ids=["y"], key=key)
    result = outstanding.outstanding(_three_entries(), _baseline(), tmp_path, key=key, **_paths(tmp_path),
                                     procedure_digest="p" * 64)
    assert result["boards_counted"] == ["c"]
    assert set(result["boards_conflicting"]) == {"a", "b"}
    assert result["conflicted_active"] == ["x"]
    assert result["unevaluated_active"] == ["z"]


def test_an_invalid_row_still_claims_its_slot(tmp_path):
    """`evals pool` refuses a slot two boards claim whatever its outcome."""
    key = ("r", "e")
    _board(tmp_path / "a", key=key, rows=[{"id": "x", "repeat": 0, "outcome": "invalid"}])
    _board(tmp_path / "b", ids=["x"], key=key)
    result = outstanding.outstanding(_three_entries(), _baseline(), tmp_path, key=key, **_paths(tmp_path),
                                     procedure_digest="p" * 64)
    assert set(result["boards_conflicting"]) == {"a", "b"}
    assert result["conflicted_active"] == ["x"]


def test_setting_a_conflicting_board_aside_is_the_remedy(tmp_path):
    key = ("r", "e")
    _board(tmp_path / "a", ids=["x", "y"], key=key)
    _board(tmp_path / "b", ids=["y", "z"], key=key)
    (tmp_path / "a").rename(tmp_path.parent / f"{tmp_path.name}-aside")
    result = outstanding.outstanding(_three_entries(), _baseline(), tmp_path, key=key, **_paths(tmp_path),
                                     procedure_digest="p" * 64)
    assert result["boards_counted"] == ["b"] and result["boards_conflicting"] == {}
    assert result["unevaluated_active"] == ["x"] and result["conflicted_active"] == []


# --- Codex on #393: a board's exposure failure is reported, not skipped ---


def _exposed_board(path, *, ids: list[str], key: tuple[str | None, str]) -> None:
    """A board whose rows name an exposure journal that is not on disk."""
    _board(path, key=key, rows=[
        {"id": id_, "repeat": 0, "outcome": "solved", "run_dir": f"runs/{id_}", "exposure_sha256": "0" * 64}
        for id_ in ids
    ])


def test_a_board_whose_exposure_the_self_audit_rejects_is_named_refused(tmp_path):
    """`matching_boards` dropped a board with a missing or tampered exposure
    artifact before the self-audit ran, so it never reached `boards_refused`
    and its entries were silently selected again."""
    key = ("r", "e")
    _exposed_board(tmp_path / "tampered", ids=["u"], key=key)
    REFUSED_BY_AUDIT["tampered"] = ("runs/u: exposure journal digest mismatch",)
    result = outstanding.outstanding(_problems(), _baseline(), tmp_path, key=key, **_paths(tmp_path),
                                     procedure_digest="p" * 64)
    assert result["boards_refused"] == {"tampered": ["runs/u: exposure journal digest mismatch"]}
    assert result["boards_counted"] == []


def test_an_exposure_finding_the_board_check_alone_makes_still_refuses(tmp_path):
    """Belt and braces: whatever the board-level exposure check finds is a
    refusal with its finding, never a silent skip, even where the self-audit
    passes."""
    key = ("r", "e")
    _exposed_board(tmp_path / "tampered", ids=["u"], key=key)
    result = outstanding.outstanding(_problems(), _baseline(), tmp_path, key=key, **_paths(tmp_path),
                                     procedure_digest="p" * 64)
    assert list(result["boards_refused"]) == ["tampered"]
    assert any("exposure" in finding for finding in result["boards_refused"]["tampered"])
    assert result["boards_counted"] == []
    assert result["unevaluated_active"] == ["u"]


# --- Review minors: the stderr account of what was not counted ----------------


def test_a_board_refused_only_by_the_board_exposure_check_is_not_said_to_fail_pool(tmp_path, capsys):
    """`evals pool` does not run `board_exposure_issues`, so saying pool would
    refuse such a board was false; it is left out of the evidence anyway."""
    from hardy.app.evals import _report_uncounted

    key = ("r", "e")
    _exposed_board(tmp_path / "tampered", ids=["u"], key=key)
    result = outstanding.outstanding(_problems(), _baseline(), tmp_path, key=key, **_paths(tmp_path),
                                     procedure_digest="p" * 64)
    _report_uncounted(result)
    err = capsys.readouterr().err
    assert "tampered" in err and "exposure" in err
    assert "`evals pool` would refuse it" not in err


def test_a_board_the_self_audit_refuses_is_still_said_to_fail_pool(tmp_path, capsys):
    from hardy.app.evals import _report_uncounted

    key = ("r", "e")
    _board(tmp_path / "bad", ids=["u"], key=key)
    REFUSED_BY_AUDIT["bad"] = ("runs/u/batch-0: the recorded-run audit reports findings",)
    _report_uncounted(outstanding.outstanding(_problems(), _baseline(), tmp_path, key=key, **_paths(tmp_path),
                                              procedure_digest="p" * 64))
    assert "board bad fails its own audit, so `evals pool` would refuse it" in capsys.readouterr().err


def test_a_board_that_cannot_be_read_again_is_refused_not_conflicting(tmp_path, monkeypatch, capsys):
    from hardy.app.evals import _report_uncounted

    key = ("r", "e")
    _board(tmp_path / "a", ids=["x"], key=key)
    _board(tmp_path / "b", ids=["y"], key=key)
    real = outstanding.board_slots

    def flaky(root, label):
        if label == "a":
            raise OSError("gone")
        return real(root, label)

    monkeypatch.setattr(outstanding, "board_slots", flaky)
    result = outstanding.outstanding(_three_entries(), _baseline(), tmp_path, key=key, **_paths(tmp_path),
                                     procedure_digest="p" * 64)
    assert result["boards_conflicting"] == {}
    assert list(result["boards_refused"]) == ["a"] and "could not be read" in result["boards_refused"]["a"][0]
    assert result["boards_counted"] == ["b"]
    _report_uncounted(result)
    err = capsys.readouterr().err
    assert "passes its own audit" not in err
    assert "board a" in err and "could not be read" in err
