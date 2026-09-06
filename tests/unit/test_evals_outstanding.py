# tests/unit/test_evals_outstanding.py
"""What is left to sweep and to run, under the pooling key this checkout would produce."""
from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from hardy.domain import EnvironmentIdentity
from hardy.evals import outstanding, sweep, taxonomy
from hardy.evals.problems import Entry, ProblemSet, Review

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


def _board(path, *, ids: list[str], key: tuple[str | None, str]) -> None:
    run_digest, env_digest = key
    path.mkdir(parents=True)
    board = {
        "condition": {"run_procedure_digest": run_digest},
        "environment": IDENTITY.model_dump(mode="json"),
        "host": {"digest": env_digest},
        "rows": [{"id": id_} for id_ in ids],
    }
    (path / "scoreboard.json").write_text(json.dumps(board), encoding="utf-8")


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
    assert outstanding.evaluated_ids(tmp_path, key=("run-digest", "env-digest")) == {"t"}


def test_a_board_with_no_run_digest_is_not_counted_as_evidence(tmp_path):
    # Absence is staleness, not agreement: a board written before the gate
    # existed says nothing about which condition produced it.
    _board(tmp_path / "old", ids=["t"], key=(None, "env-digest"))
    assert outstanding.evaluated_ids(tmp_path, key=("run-digest", "env-digest")) == set()


def test_outstanding_lists_active_work_only(tmp_path):
    result = outstanding.outstanding(_problems(), _baseline(), tmp_path, key=("r", "e"))
    assert result["unevaluated_active"] == ["u"]      # `t` and `f` are candidates
    assert result["unbaselined_active"] == ["u"]
