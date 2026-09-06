# tests/unit/test_evals_pool.py
"""Combining scoreboards: only under one key, only once per (id, repeat)."""
from __future__ import annotations

import json
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import pytest
from test_evals_runner import SOLVE, _batch_runner, _condition, _files
from test_recorded_runs import IDENTITY as RAW_IDENTITY

from hardy.domain import EnvironmentIdentity
from hardy.evals import outstanding, pool, runner

IDENTITY = EnvironmentIdentity(**RAW_IDENTITY)

# Built once, on disk, for the whole module: every board in every test is
# validated against this same corpus and baseline (`pool.pool` takes a single
# `problems_path`/`baseline_path` for the whole call), so the two live outside
# any one test's `tmp_path`.
_ROOT = Path(tempfile.mkdtemp(prefix="hardy-pool-test-"))
PROBLEMS, BASELINE = _files(_ROOT)


@pytest.fixture(autouse=True)
def _stub_environment_digest(monkeypatch):
    """`environment_digest_of_board` hashes the board's recorded `environment`
    and `host` through `sweep.environment_digest_of` (a real sha256). These
    tests only care whether two boards agree or differ, so this reduces the
    digest to a value each fixture's own `host` names directly, rather than
    fighting a one-way hash to produce a chosen literal.

    Patched on `outstanding.environment_digest_of_board` itself, not on
    `sweep.environment_digest_of`: `_board` below calls the real
    `runner.run_set` to produce a genuinely valid scoreboard, and `run_set`'s
    own staleness gate calls `sweep.environment_digest_of` directly to check
    this run against the shared `BASELINE` -- patching that function out
    from under it would make every `_board` call refuse for staleness
    instead of succeeding.
    """
    monkeypatch.setattr(
        outstanding, "environment_digest_of_board",
        lambda board: (board.get("host") or {}).get("digest", ""),
    )


def _board(path: Path, *, ids: list[str], run_digest: str | None, env_digest: str) -> Path:
    """A scoreboard that genuinely validates (`runner.run_set` writes it for
    real, against the shared `PROBLEMS`/`BASELINE`), with its pooling key
    then pinned to exactly the values a test wants to compare.

    `run_procedure_digest` is set through the condition, the same field a
    real run records it in. The environment digest is pinned by overwriting
    the written board's `host` -- `validate_scoreboard` never re-derives
    `host` against anything, so this cannot make an otherwise-valid board
    fail its own audit -- and read back through the stubbed
    `environment_digest_of_board` above.
    """
    out = runner.run_set(
        label=path.name, problems_path=PROBLEMS, baseline_path=BASELINE, scoreboards_root=path.parent,
        condition=_condition(run_procedure_digest=run_digest, selection={"only": ids, "tiers": None, "twins": False}),
        environment=IDENTITY, batch_runner=_batch_runner({id_: SOLVE for id_ in ids}),
        now=lambda: datetime(2026, 9, 1, tzinfo=UTC), report=lambda _: None,
    )
    assert out == path
    board_path = out / "scoreboard.json"
    payload = json.loads(board_path.read_text(encoding="utf-8"))
    payload["host"] = {"digest": env_digest}
    board_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return out


def test_two_boards_under_one_key_pool(tmp_path):
    a = _board(tmp_path / "a", ids=["t"], run_digest="r", env_digest="e")
    b = _board(tmp_path / "b", ids=["u"], run_digest="r", env_digest="e")
    result = pool.pool([a, b], problems_path=PROBLEMS, baseline_path=BASELINE)
    assert sorted(row["id"] for row in result["rows"]) == ["t", "u"]
    assert result["aggregates"]["totals"]["rows"] == 2


def test_a_differing_run_digest_is_refused_by_name(tmp_path):
    a = _board(tmp_path / "a", ids=["t"], run_digest="r", env_digest="e")
    b = _board(tmp_path / "b", ids=["u"], run_digest="other", env_digest="e")
    with pytest.raises(pool.PoolRefused) as caught:
        pool.pool([a, b], problems_path=PROBLEMS, baseline_path=BASELINE)
    assert "run_procedure_digest" in str(caught.value)


def test_a_differing_environment_is_refused_by_name(tmp_path):
    a = _board(tmp_path / "a", ids=["t"], run_digest="r", env_digest="e")
    b = _board(tmp_path / "b", ids=["u"], run_digest="r", env_digest="other")
    with pytest.raises(pool.PoolRefused) as caught:
        pool.pool([a, b], problems_path=PROBLEMS, baseline_path=BASELINE)
    assert "environment_digest" in str(caught.value)


def test_a_duplicate_id_and_repeat_is_refused(tmp_path):
    a = _board(tmp_path / "a", ids=["t"], run_digest="r", env_digest="e")
    b = _board(tmp_path / "b", ids=["t"], run_digest="r", env_digest="e")
    with pytest.raises(pool.PoolRefused) as caught:
        pool.pool([a, b], problems_path=PROBLEMS, baseline_path=BASELINE)
    assert "t" in str(caught.value) and "repeat 0" in str(caught.value)


def test_a_board_that_fails_its_own_audit_is_refused(tmp_path):
    a = _board(tmp_path / "a", ids=["t"], run_digest="r", env_digest="e")
    (a / "scoreboard.json").write_text("{ not json", encoding="utf-8")
    with pytest.raises(pool.PoolRefused):
        pool.pool([a], problems_path=PROBLEMS, baseline_path=BASELINE)


def test_a_board_with_no_run_procedure_digest_is_refused(tmp_path):
    """Absence is staleness, not agreement: a board written before this gate
    existed carries no `run_procedure_digest`, and nothing establishes which
    code produced it -- so it cannot be pooled even alone.
    """
    a = _board(tmp_path / "a", ids=["t"], run_digest=None, env_digest="e")
    with pytest.raises(pool.PoolRefused) as caught:
        pool.pool([a], problems_path=PROBLEMS, baseline_path=BASELINE)
    assert "run_procedure_digest" in str(caught.value)


def test_the_wall_seconds_note_names_the_worker_ceiling_and_disclaims_serial_time(tmp_path):
    a = _board(tmp_path / "a", ids=["t"], run_digest="r", env_digest="e")
    result = pool.pool([a], problems_path=PROBLEMS, baseline_path=BASELINE)
    workers = result["aggregates"]["totals"]["workers"]
    assert result["wall_seconds_note"] == (
        f"summed under up to {workers} concurrent workers; not a serial wall-clock figure"
    )
