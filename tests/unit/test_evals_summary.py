# tests/unit/test_evals_summary.py
"""`hardy evals summary`: one Markdown row per model, pooled per-model via `pool.pool`."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from corpus_helpers import write_corpus
from test_evals_runner import _batch_runner, _condition
from test_recorded_runs import IDENTITY as RAW_IDENTITY
from test_recorded_runs import _Runtime

from hardy.domain import EnvironmentIdentity
from hardy.evals import outstanding, runner, summary
from hardy.evals.corpus import load_corpus
from hardy.evals.problems import Entry

IDENTITY = EnvironmentIdentity(**RAW_IDENTITY)
SOLVE = [("submit_proof", {"proof": "by exact True.intro"})]

# Two entries in different MSC classes (and so different arXiv categories),
# each true so a run against either can be scored: "t" is elementary number
# theory (11), "c" is general commutative ring theory (13).
NT_ENTRY = Entry(id="t", input="True.", name="T", conclusion="True", expected="true", source="textbook",
                 msc=("11Axx",), difficulty="routine", rationale="test fixture", witness=None,
                 witness_note="test fixture")
CA_ENTRY = Entry(id="c", input="True too.", name="C", conclusion="True", expected="true", source="textbook",
                 msc=("13Axx",), difficulty="routine", rationale="test fixture", witness=None,
                 witness_note="test fixture")
CA_TWIN = Entry(id="c-twin", input="False.", name="CTwin", conclusion="True", expected="false", twin_of="c",
                source="textbook", msc=("13Axx",), difficulty="routine", rationale="test fixture", witness=None,
                witness_note="test fixture")
ENTRIES = (NT_ENTRY, CA_ENTRY, CA_TWIN)


def _row(id: str, expected: str, outcome: str, **kw) -> dict:
    """The subset of `Row`'s fields `summary`'s pure functions actually read,
    with every numeric field defaulted to `None` unless a test overrides it --
    exactly the shape a row with unreported usage or timing carries.
    """
    base = {
        "id": id, "expected": expected, "outcome": outcome,
        "input_tokens": None, "output_tokens": None,
        "cache_read_tokens": None, "cache_write_tokens": None,
        "wall_seconds": None, "workers": None,
    }
    base.update(kw)
    return base


# --- pure computation: row_stats, twin_stats, classify/_group_by -----------


def test_invalid_rows_are_out_of_the_solved_denominator_but_counted_separately():
    rows = [_row("t", "true", "solved"), _row("t", "true", "invalid")]
    stats = summary.row_stats(rows)
    assert stats["solved"] == 1
    assert stats["denominator"] == 1
    assert stats["rate"] == 100.0
    assert stats["invalid"] == 1


def test_solved_other_is_not_in_the_numerator_but_has_its_own_column():
    rows = [_row("t", "true", "solved_other")]
    stats = summary.row_stats(rows)
    assert stats["solved"] == 0
    assert stats["solved_other"] == 1
    assert stats["denominator"] == 1
    assert stats["rate"] == 0.0


def test_twins_are_excluded_from_the_completion_rate():
    rows = [_row("t", "true", "solved"), _row("f", "false", "refused")]
    stats = summary.row_stats(rows)
    assert stats["denominator"] == 1
    assert stats["solved"] == 1
    assert stats["rate"] == 100.0
    # Twins still count toward the group's absolute token/wall sums (they ran
    # too), just not toward the solved fraction.
    assert stats["rows"] == 2


def test_twins_appear_in_the_twin_table_with_a_refusal_rate():
    rows = [_row("t", "true", "solved"), _row("f", "false", "refused"), _row("g", "false", "graded")]
    stats = summary.twin_stats(rows)
    assert stats == {"refused": 1, "twins": 2, "rate": 50.0}


def test_totals_and_averages_survive_none_tokens_and_none_wall_seconds():
    rows = [
        _row("t", "true", "solved", input_tokens=None, output_tokens=None, wall_seconds=None),
        _row("u", "true", "solved", input_tokens=100, output_tokens=50, wall_seconds=10.0),
    ]
    stats = summary.row_stats(rows)
    assert stats["input_tokens"] == 100
    assert stats["output_tokens"] == 50
    assert stats["tok_per_prob"] == 75  # (100 + 50) / 2, rounded
    assert stats["wall_seconds"] == 10.0
    assert stats["wall_per_prob"] == 5.0


def test_a_group_with_no_true_rows_reports_no_rate_rather_than_dividing_by_zero():
    stats = summary.row_stats([_row("f", "false", "refused")])
    assert stats["denominator"] == 0
    assert stats["rate"] is None


def test_msc_and_arxiv_grouping_put_an_entry_in_the_right_bucket(tmp_path):
    root = write_corpus(tmp_path / "corpus", ENTRIES)
    problems = load_corpus(root)
    assert summary.classify(problems.by_id("t"), root) == ("11", "math.NT")
    assert summary.classify(problems.by_id("c"), root) == ("13", "math.AC")

    rows = [_row("t", "true", "solved"), _row("c", "true", "unsolved")]
    by_msc = summary._group_by(rows, problems, root, 0)
    assert {k: [r["id"] for r in v] for k, v in by_msc.items()} == {"11": ["t"], "13": ["c"]}
    by_arxiv = summary._group_by(rows, problems, root, 1)
    assert {k: [r["id"] for r in v] for k, v in by_arxiv.items()} == {"math.NT": ["t"], "math.AC": ["c"]}


def test_an_id_the_corpus_no_longer_carries_is_filed_as_unknown_not_dropped(tmp_path):
    root = write_corpus(tmp_path / "corpus", ENTRIES)
    problems = load_corpus(root)
    rows = [_row("gone", "true", "solved")]
    by_msc = summary._group_by(rows, problems, root, 0)
    assert list(by_msc) == ["unknown"]
    assert by_msc["unknown"][0]["id"] == "gone"


# --- rendering: determinism -------------------------------------------------


def _fixed_data() -> dict:
    return {
        "scoreboards_root": "evals/scoreboards",
        "models": [
            {
                "model": "model-a", "boards": ["a"],
                "pooling_key": {"run_procedure_digest": "r1", "environment_digest": "e1"},
                "rows": [_row("t", "true", "solved", input_tokens=10, output_tokens=5, wall_seconds=2.0, workers=4)],
            },
            {
                "model": "model-b", "boards": ["b"],
                "pooling_key": {"run_procedure_digest": "r2", "environment_digest": "e2"},
                "rows": [_row("c", "true", "unsolved"), _row("c-twin", "false", "refused")],
            },
        ],
    }


def test_regenerating_with_unchanged_input_is_byte_identical(tmp_path):
    root = write_corpus(tmp_path / "corpus", ENTRIES)
    problems = load_corpus(root)
    data = _fixed_data()
    first = summary.render(data, problems=problems, root=root)
    second = summary.render(_fixed_data(), problems=problems, root=root)
    assert first == second


def test_two_models_in_fixed_data_each_get_their_own_table_1_row(tmp_path):
    root = write_corpus(tmp_path / "corpus", ENTRIES)
    problems = load_corpus(root)
    text = summary.render(_fixed_data(), problems=problems, root=root)
    assert "model-a" in text and "model-b" in text
    assert "## Table 1: Overall" in text
    table1 = text.split("## Table 1: Overall")[1].split("## Table 2")[0]
    assert "model-a" in table1 and "model-b" in table1


def test_header_states_no_scoreboards_without_crashing(tmp_path):
    root = write_corpus(tmp_path / "corpus", ENTRIES)
    problems = load_corpus(root)
    text = summary.render({"scoreboards_root": "evals/scoreboards", "models": []}, problems=problems, root=root)
    assert "No scoreboards were found" in text
    assert "evals/scoreboards" in text


# --- build(): real scoreboards, real pooling, real refusals -----------------


@pytest.fixture(autouse=True)
def _stub_environment_digest(monkeypatch):
    """As in `test_evals_pool.py`: reduce the environment digest to whatever
    a fixture's own `host` names, rather than fighting a real sha256 to
    produce a chosen literal. Patched on `outstanding.environment_digest_of_board`,
    which is what `pool.pool` (and so `summary.build`) actually calls.
    """
    monkeypatch.setattr(
        outstanding, "environment_digest_of_board",
        lambda board: (board.get("host") or {}).get("digest", ""),
    )


def _files(tmp_path: Path) -> tuple[Path, Path]:
    from hardy.evals import sweep
    from hardy.evals.corpus import manifest_digest

    problems = write_corpus(tmp_path / "corpus", ENTRIES)
    host = sweep.host_info()
    full_attempts = {name: sweep.Attempt(status="closed" if name == "simp" else "failed")
                     for name in sweep.SINGLES + sweep.CHAINS}
    failed_attempts = {name: sweep.Attempt(status="failed") for name in sweep.SINGLES + sweep.CHAINS}
    baseline = sweep.Baseline(
        created_at=datetime(2026, 9, 1, tzinfo=UTC), problems_sha256=manifest_digest(problems),
        environment=IDENTITY, environment_digest=sweep.environment_digest_of(IDENTITY, host),
        procedure_digest=sweep.procedure_digest_of(600.0), statement_digests={e.id: e.statement_digest() for e in ENTRIES},
        heartbeat_budget=200000, wall_backstop_seconds=600.0, singles=sweep.SINGLES, chains=sweep.CHAINS, host=host,
        problems=(),
        entries={
            "t": sweep.EntryBaseline(tier=0, elaborates=True, attempts=full_attempts, closed_by=("simp",)),
            "c": sweep.EntryBaseline(tier=0, elaborates=True, attempts=full_attempts, closed_by=("simp",)),
            "c-twin": sweep.EntryBaseline(tier=0, elaborates=True, attempts=full_attempts, closed_by=("simp",),
                                          negation=sweep.NegationBaseline(attempts=failed_attempts, closed_by=())),
        },
    )
    path = tmp_path / "baseline.json"
    path.write_text(json.dumps(baseline.model_dump(mode="json")), encoding="utf-8")
    return problems, path


def _board(path: Path, *, ids: list[str], problems: Path, baseline: Path, model: str = "fake-model@test",
          run_digest: str | None = "r", env_digest: str = "e") -> Path:
    """A genuinely-validating scoreboard recorded under `model`.

    `_Runtime.model` (test_recorded_runs.py) is a fixed class attribute
    ('fake-model@test'), so two boards recording two different models cannot
    both use it unpatched -- `_condition_issues` cross-checks the trajectory's
    own `model` against the condition's, and a mismatch fails the very audit
    `pool.pool` runs first. Patched for the duration of this one call only, so
    two `_board` calls in the same test can each name a different model.
    """
    from unittest.mock import patch

    with patch.object(_Runtime, "model", model):
        out = runner.run_set(
            label=path.name, problems_path=problems, baseline_path=baseline, scoreboards_root=path.parent,
            condition=_condition(model=model, run_procedure_digest=run_digest, selection={"only": ids, "tiers": None, "twins": False}),
            environment=IDENTITY, batch_runner=_batch_runner({id_: SOLVE for id_ in ids}),
            now=lambda: datetime(2026, 9, 1, tzinfo=UTC), report=lambda _: None,
        )
    board_path = out / "scoreboard.json"
    payload = json.loads(board_path.read_text(encoding="utf-8"))
    payload["host"] = {"digest": env_digest}
    board_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return out


def test_two_boards_for_different_models_produce_two_model_rows(tmp_path):
    problems, baseline = _files(tmp_path)
    _board(tmp_path / "boards" / "a", ids=["t"], problems=problems, baseline=baseline, model="model-a", run_digest="ra")
    _board(tmp_path / "boards" / "b", ids=["c"], problems=problems, baseline=baseline, model="model-b", run_digest="rb")
    data = summary.build(tmp_path / "boards", problems_path=problems, baseline_path=baseline)
    assert [m["model"] for m in data["models"]] == ["model-a", "model-b"]
    assert [r["id"] for r in data["models"][0]["rows"]] == ["t"]
    assert [r["id"] for r in data["models"][1]["rows"]] == ["c"]


def test_two_boards_for_one_model_with_different_pooling_keys_is_refused(tmp_path):
    problems, baseline = _files(tmp_path)
    _board(tmp_path / "boards" / "a", ids=["t"], problems=problems, baseline=baseline, model="model-a", run_digest="r1")
    _board(tmp_path / "boards" / "b", ids=["c"], problems=problems, baseline=baseline, model="model-a", run_digest="r2")
    with pytest.raises(summary.SummaryRefused) as caught:
        summary.build(tmp_path / "boards", problems_path=problems, baseline_path=baseline)
    assert "run_procedure_digest" in str(caught.value)
    assert "model-a" in str(caught.value)


def test_no_scoreboards_at_all_produces_a_valid_file_not_a_crash(tmp_path):
    problems, baseline = _files(tmp_path)
    empty_root = tmp_path / "boards"
    data = summary.build(empty_root, problems_path=problems, baseline_path=baseline)
    assert data["models"] == []
    loaded = load_corpus(problems)
    text = summary.render(data, problems=loaded, root=problems)
    assert "No scoreboards were found" in text


def test_write_produces_the_file_end_to_end_and_is_stable_on_regeneration(tmp_path):
    problems, baseline = _files(tmp_path)
    _board(tmp_path / "boards" / "a", ids=["t"], problems=problems, baseline=baseline, model="model-a", run_digest="ra")
    out_path = tmp_path / "EVALS.md"
    summary.write(tmp_path / "boards", problems_path=problems, baseline_path=baseline, out_path=out_path)
    first_bytes = out_path.read_bytes()
    assert b"\r\n" not in first_bytes
    summary.write(tmp_path / "boards", problems_path=problems, baseline_path=baseline, out_path=out_path)
    second_bytes = out_path.read_bytes()
    assert first_bytes == second_bytes
