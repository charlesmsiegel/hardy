"""Historical observations reuse authenticated recorded runs, never board aggregates."""
import json
from datetime import UTC, datetime

import pytest
from test_evals_compare import _boards, _edit
from test_evals_runner import GIVE_UP, IDENTITY, SOLVE, _batch_runner, _condition, _files

from hardy.evals import runner


@pytest.fixture
def history():
    import importlib

    return importlib.import_module("hardy.evals.history")


def date(path, start, end):
    _edit(path / "scoreboard.json", lambda board: board.update(started_at=start, finished_at=end))


def test_chronological_views_preserve_unknown_controls_and_authenticated_observed_losses(tmp_path, history):
    paths, kwargs = _boards(tmp_path)
    date(paths[0], "2026-09-01T10:00:00+00:00", "2026-09-01T11:00:00+00:00")
    date(paths[1], "2026-09-03T10:00:00+00:00", "2026-09-03T11:00:00+00:00")
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    result = history.timeline(list(reversed(paths)), varying=(), **kwargs)
    assert [point["summary"]["label"] for point in result["boards"]] == ["left", "right"]
    change = result["transitions"][0]
    assert change["comparability"] == "unknown"
    assert "source_sha256" in change["comparison"]["comparability"]["unknown"]
    assert change["observed"]["proof"] == {"pairs": 2, "gains": 0, "losses": 2, "unchanged": 0, "delta": -2}
    assert change["comparable_delta"] is None
    assert change["timing"] == "nonoverlapping"
    assert result["boards"][1]["summary"]["proof_usage"]["cost_usd"]["complete_rows"] == 2
    assert all(path.read_bytes() == content for path, content in before.items())
    assert not set(tmp_path.rglob("*")) - set(before) - {p for p in tmp_path.rglob("*") if p.is_dir()}


def test_unmatched_and_invalid_rows_remain_visible_without_inferred_zeroes(tmp_path, history):
    paths, kwargs = _boards(tmp_path, right_ids=("t",))
    (paths[1] / "runs/t/batch-0/proof.lean").write_text("tampered", encoding="utf-8")
    result = history.timeline(paths, varying=("selection",), **kwargs)
    change = result["transitions"][0]
    assert change["observed"]["proof"]["delta"] is None
    assert change["observed"]["unmatched"] == 1
    assert change["observed"]["unauthenticated"] == 1
    assert change["comparison"]["pairs"][1]["right"] is None
    assert change["comparison"]["rows"]["right"][0]["recorded_outcome"] == "unsolved"
    assert result["boards"][1]["summary"]["proof_usage"]["cost_usd"]["value"] is None


def test_explicit_vary_fields_do_not_hide_unknown_controls_or_claim_causality(tmp_path, history):
    paths, kwargs = _boards(tmp_path)
    _edit(paths[0] / "scoreboard.json", lambda board: board["condition"].update(source_revision="original"))
    _edit(paths[1] / "scoreboard.json", lambda board: board["condition"].update(source_revision="changed"))
    without = history.timeline(paths, varying=(), **kwargs)
    with_vary = history.timeline(paths, varying=("source_revision",), **kwargs)
    assert with_vary["transitions"][0]["comparable_delta"] is None
    assert without["transitions"][0]["comparable_delta"] is None
    assert without["transitions"][0]["comparability"] == "incomparable"
    assert with_vary["transitions"][0]["comparability"] == "unknown"
    assert without["transitions"][0]["observed"]["proof"]["losses"] == 2
    assert with_vary["varying"] == ["source_revision"]
    assert any("causal" in note for note in with_vary["notes"])


def test_timestamps_are_metadata_and_overlap_is_explicit(tmp_path, history):
    paths, kwargs = _boards(tmp_path)
    date(paths[0], "2026-09-01T10:00:00+00:00", "2026-09-01T12:00:00+00:00")
    date(paths[1], "2026-09-01T11:00:00+00:00", "2026-09-01T13:00:00+00:00")
    assert history.timeline(paths, varying=(), **kwargs)["transitions"][0]["timing"] == "overlap"
    date(paths[1], "2026-09-01T11:00:00", "2026-09-01T13:00:00")
    result = history.timeline(paths, varying=(), **kwargs)
    assert len(result["unplaced"]) == 1
    assert result["transitions"] == []
    assert "timezone" in result["unplaced"][0]["chronology_issues"][0]


def test_missing_and_malformed_boards_are_retained_as_unplaced_findings(tmp_path, history):
    paths, kwargs = _boards(tmp_path)
    malformed = tmp_path / "malformed"
    malformed.mkdir()
    (malformed / "scoreboard.json").write_text("{torn", encoding="utf-8")
    result = history.timeline([paths[0], malformed, tmp_path / "missing"], varying=(), **kwargs)
    assert len(result["boards"]) == 1
    assert len(result["unplaced"]) == 2
    assert all(point["issues"] for point in result["unplaced"])


def test_duplicate_paths_and_excessive_inputs_are_refused(tmp_path, history, monkeypatch):
    paths, kwargs = _boards(tmp_path)
    with pytest.raises(history.HistoryRefused, match="duplicate"):
        history.timeline([paths[0], paths[0] / ".." / paths[0].name], varying=(), **kwargs)
    monkeypatch.setattr(history, "MAX_BOARDS", 1)
    with pytest.raises(history.HistoryRefused, match="at most"):
        history.timeline(paths, varying=(), **kwargs)
    monkeypatch.setattr(history, "MAX_BOARDS", 64)
    monkeypatch.setattr(history, "MAX_BOARD_BYTES", 10)
    with pytest.raises(history.HistoryRefused, match="bytes"):
        history.timeline(paths, varying=(), **kwargs)


def test_false_statement_outcomes_are_separate_from_proof_losses(tmp_path, history):
    problems, baseline = _files(tmp_path)
    paths = []
    for label, script in [("old", GIVE_UP), ("new", SOLVE)]:
        paths.append(runner.run_set(label=label, problems_path=problems, baseline_path=baseline,
            scoreboards_root=tmp_path / "boards", condition=_condition(selection={"only": ["f"], "tiers": None, "twins": True}),
            environment=IDENTITY, batch_runner=_batch_runner({"f": script}),
            now=lambda: datetime(2026, 9, 1, tzinfo=UTC), report=lambda _: None))
    result = history.timeline(paths, varying=(), problems_path=problems, baseline_path=baseline)
    observed = result["transitions"][0]["observed"]
    assert observed["proof"]["pairs"] == 0
    assert observed["proof"]["delta"] is None
    assert sum(observed["negative_outcomes"].values()) == 1


def test_cli_uses_explicit_board_baselines_and_reports_json_without_writes(tmp_path, history, capsys):
    from hardy.app import evals
    from hardy.app.cli import build_parser

    paths, kwargs = _boards(tmp_path)
    args = build_parser().parse_args(["evals", "history", *map(str, reversed(paths)), "--vary", "model",
        "--problems", str(kwargs["problems_path"]), "--baseline", str(kwargs["baseline_path"]),
        "--board-baseline", str(paths[0]), str(kwargs["baseline_path"])])
    assert evals.main(args, None) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["varying"] == ["model"]
    assert result["boards"][0]["baseline"] == str(kwargs["baseline_path"].resolve())


def test_gain_uses_chronological_order_and_records_per_board_baseline_alignment(tmp_path, history):
    paths, kwargs = _boards(tmp_path, right_ids=("u",))
    date(paths[1], "2026-08-01T10:00:00Z", "2026-08-01T11:00:00Z")
    distinct_baseline = tmp_path / "later-baseline.json"
    distinct_baseline.write_bytes(kwargs["baseline_path"].read_bytes())
    _edit(kwargs["baseline_path"], lambda baseline: baseline["entries"].pop("t"))
    wrong = history.timeline(paths, varying=("selection",), **kwargs)
    assert wrong["transitions"][0]["observed"]["proof"]["delta"] is None
    aligned = history.timeline(paths, varying=("selection",), board_baselines=[(paths[0], distinct_baseline)], **kwargs)
    assert not any(point["issues"] for point in aligned["boards"])
    assert aligned["transitions"][0]["observed"]["proof"]["gains"] == 1
    assert aligned["transitions"][0]["observed"]["proof"]["delta"] == 1
    assert aligned["boards"][1]["baseline"] == str(distinct_baseline.resolve())


def test_copied_scoreboards_and_ambiguous_baseline_mappings_are_refused(tmp_path, history):
    paths, kwargs = _boards(tmp_path)
    copy = tmp_path / "copy"
    copy.mkdir()
    (copy / "scoreboard.json").write_bytes((paths[0] / "scoreboard.json").read_bytes())
    with pytest.raises(history.HistoryRefused, match="duplicate scoreboard bytes"):
        history.timeline([paths[0], copy], **kwargs)
    with pytest.raises(history.HistoryRefused, match="duplicate or unselected"):
        history.timeline(paths, board_baselines=[(copy, kwargs["baseline_path"])], **kwargs)


@pytest.mark.parametrize("number", ["NaN", "Infinity", "1e999"])
def test_nonfinite_json_is_an_explicit_finding_not_a_cli_serialization_crash(tmp_path, history, number):
    paths, kwargs = _boards(tmp_path)
    _edit(paths[1] / "scoreboard.json", lambda board: board.update(started_at=float("nan")))
    board = paths[1] / "scoreboard.json"
    board.write_text(board.read_text(encoding="utf-8").replace("NaN", number), encoding="utf-8")
    result = history.timeline(paths, **kwargs)
    assert len(result["unplaced"]) == 1
    assert "nonfinite" in result["unplaced"][0]["issues"][0]
    json.dumps(result, allow_nan=False)


def test_board_changed_between_audits_is_refused(tmp_path, history, monkeypatch):
    paths, kwargs = _boards(tmp_path)
    real = history.compare

    def changed(*args, **options):
        result = real(*args, **options)
        _edit(paths[0] / "scoreboard.json", lambda board: board.update(label="changed during read"))
        return result

    monkeypatch.setattr(history, "compare", changed)
    with pytest.raises(history.HistoryRefused, match="changed while reading"):
        history.timeline(paths, **kwargs)


def test_history_import_cannot_load_a_run_launcher():
    import subprocess
    import sys

    script = "import sys; import hardy.evals.history; " + (
        "assert not {'hardy.evals.runner', 'hardy.workflows.prove', 'hardy.workflows.batch', "
        "'hardy.app.evals'} & sys.modules.keys()"
    )
    subprocess.run([sys.executable, "-c", script], check=True, capture_output=True, timeout=15)
