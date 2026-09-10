"""Read-only, artifact-backed comparisons; scripted fixtures assert accounting only."""
from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from test_evals_runner import GIVE_UP, IDENTITY, SOLVE, _batch_runner, _condition, _files

from hardy.evals import compare, runner


def _boards(tmp_path, *, right_ids=("t", "u")):
    problems, baseline = _files(tmp_path)
    paths = []
    for label, ids, script in (("left", ("t", "u"), SOLVE), ("right", right_ids, GIVE_UP)):
        paths.append(runner.run_set(
            label=label, problems_path=problems, baseline_path=baseline,
            scoreboards_root=tmp_path / "boards",
            condition=_condition(selection={"only": list(ids), "tiers": None, "twins": False}),
            environment=IDENTITY, batch_runner=_batch_runner(dict.fromkeys(ids, script)),
            now=lambda: datetime(2026, 9, 1, tzinfo=UTC), report=lambda _: None,
        ))
    return paths, {"problems_path": problems, "baseline_path": baseline}


def _edit(path, transform):
    data = json.loads(path.read_text(encoding="utf-8"))
    transform(data)
    path.write_text(json.dumps(data), encoding="utf-8")


def test_pairs_exact_slots_and_counts_unsolved_spend(tmp_path):
    paths, kwargs = _boards(tmp_path)
    before = {p: p.read_bytes() for path in paths for p in path.rglob("*") if p.is_file()}
    result = compare.compare(*paths, varying=(), **kwargs)
    assert [pair["id"] for pair in result["pairs"]] == ["t", "u"]
    assert result["pairs"][0]["left"]["outcome"] == "solved"
    assert result["pairs"][0]["right"]["outcome"] == "unsolved"
    assert result["sides"]["right"]["proof_usage"]["cost_usd"]["value"] == pytest.approx(.2)
    assert result["sides"]["right"]["proof_usage"]["cost_usd"]["complete_rows"] == 2
    assert result["sides"]["right"]["proof_usage"]["cache_read_tokens"]["missing_rows"] == 2
    assert "source_sha256" in result["comparability"]["unknown"]
    assert not result["comparability"]["recorded_controls_match"]
    assert all(p.read_bytes() == content for p, content in before.items())


def test_authenticated_run_with_one_unreported_exchange_has_partial_cost(tmp_path, monkeypatch):
    from test_recorded_runs import _Runtime

    original = _Runtime.ask

    def ask(self, text):
        reply = original(self, text)
        self.context["observe"]({"type": "result", "session_id": "s"})
        return reply

    monkeypatch.setattr(_Runtime, "ask", ask)
    paths, kwargs = _boards(tmp_path)
    result = compare.compare(*paths, varying=(), **kwargs)
    assert result["sides"]["left"]["audit_issues"] == []
    cost = result["pairs"][0]["left"]["proof_usage"]["cost_usd"]
    assert cost == {"value": .1, "coverage": "partial", "reported_exchanges": 1}
    total = result["sides"]["left"]["proof_usage"]["cost_usd"]
    assert total["value"] == .2 and total["partial_rows"] == 2 and total["complete_rows"] == 0


def test_selection_difference_preserves_unmatched(tmp_path):
    paths, kwargs = _boards(tmp_path, right_ids=("t",))
    result = compare.compare(*paths, varying=("selection",), **kwargs)
    assert result["pairs"][1]["right"] is None
    assert result["pairs"][1]["status"] == "left_only"
    assert result["comparability"]["differences"] == ["selection"]
    assert result["comparability"]["unintended_differences"] == []


def test_unknown_intended_field_is_refused(tmp_path):
    paths, kwargs = _boards(tmp_path)
    with pytest.raises(compare.ComparisonRefused, match="typo"):
        compare.compare(*paths, varying=("typo",), **kwargs)


def test_changed_prompt_is_explicit_and_opaque_run_digests_are_not_pooled(tmp_path):
    paths, kwargs = _boards(tmp_path)
    _edit(paths[1] / "scoreboard.json", lambda board: board["condition"].update(
        batch_prompt_set_sha256="different", run_procedure_digest="different",
        strategy="best-first", history_mode="full",
    ))
    result = compare.compare(*paths, varying=(), **kwargs)
    assert result["comparability"]["unintended_differences"] == ["batch_prompt_set_sha256"]
    assert "strategy" in result["comparability"]["unknown"]
    result = compare.compare(*paths, varying=("batch_prompt_set_sha256",), **kwargs)
    assert result["comparability"]["unintended_differences"] == []


def test_duplicate_slots_remain_unpaired_and_retained(tmp_path):
    paths, kwargs = _boards(tmp_path)
    _edit(paths[1] / "scoreboard.json", lambda board: board["rows"].append(board["rows"][0]))
    result = compare.compare(*paths, varying=(), **kwargs)
    assert result["pairs"] == []
    assert len(result["rows"]["right"]) == 3
    assert result["sides"]["right"]["audit_issues"]


def test_tampered_artifacts_remain_visible_without_counted_cost_or_solve(tmp_path):
    paths, kwargs = _boards(tmp_path)
    (paths[1] / "runs/t/batch-0/proof.lean").write_text("tampered", encoding="utf-8")
    result = compare.compare(*paths, varying=(), **kwargs)
    assert result["sides"]["right"]["audit_issues"]
    assert result["pairs"][0]["right"]["outcome"] == "invalid"
    assert result["pairs"][0]["right"]["recorded_outcome"] == "unsolved"
    assert result["sides"]["right"]["proof_usage"]["cost_usd"]["value"] is None


def test_interruption_is_retained_with_unmatched_slot(tmp_path):
    paths, kwargs = _boards(tmp_path)
    def interrupt(board):
        board["rows"] = board["rows"][:1]
        board["interrupted"] = True
        board["finished_at"] = None
    _edit(paths[1] / "scoreboard.json", interrupt)
    result = compare.compare(*paths, varying=(), **kwargs)
    assert result["sides"]["right"]["interrupted"]
    assert result["sides"]["right"]["audit_issues"] == []
    assert result["pairs"][1]["right"] is None


@pytest.mark.parametrize(("value", "reports", "exchanges", "status"), [
    (.1, 1, 2, "partial"), (.1, 2, 2, "complete"),
    (None, 0, 2, "missing"), (.1, None, 2, "partial"),
    (.1, 3, 2, "partial"), (float("nan"), 1, 1, "missing"),
])
def test_usage_coverage_requires_report_counts(value, reports, exchanges, status):
    usage = {"cost_usd": value, "exchanges": exchanges}
    if reports is not None:
        usage["reported"] = {"cost_usd": reports}
    metric = compare.usage_measurements(usage)["cost_usd"]
    assert metric["coverage"] == status


def test_source_hash_identifies_paths_and_bytes(monkeypatch, tmp_path):
    from hardy.evals import identity
    first, second = tmp_path / "first.py", tmp_path / "second.py"
    first.write_text("same", encoding="utf-8")
    second.write_text("same", encoding="utf-8")
    monkeypatch.setattr(identity, "RUN_SOURCE_ROOT", tmp_path)
    monkeypatch.setattr(identity, "run_source_paths", lambda: (first,))
    original = identity.run_source_digest_of()
    monkeypatch.setattr(identity, "run_source_paths", lambda: (second,))
    assert identity.run_source_digest_of() != original
    monkeypatch.setattr(identity, "run_source_paths", lambda: (first,))
    first.write_text("changed", encoding="utf-8")
    assert identity.run_source_digest_of() != original


def test_cli_emits_json_without_writing_artifacts(tmp_path, capsys):
    from hardy.app import evals
    from hardy.app.cli import build_parser
    paths, kwargs = _boards(tmp_path)
    args = build_parser().parse_args([
        "evals", "compare", *map(str, paths), "--vary", "model",
        "--problems", str(kwargs["problems_path"]), "--baseline", str(kwargs["baseline_path"]),
    ])
    assert evals.main(args, None) == 0
    assert json.loads(capsys.readouterr().out)["comparability"]["intended_varying"] == ["model"]


def test_staged_review_verdict_cost_is_not_authenticated_usage(tmp_path):
    from test_evals_staged import _solved_fixture

    from hardy.evals import scoreboard
    board_dir, row_dir, _, entry, *_ = _solved_fixture(tmp_path)
    row = scoreboard.staged_row(entry, 3, row_dir, board_dir, repeat=0)
    data = compare._row(board_dir, row, authenticated=True)
    # Fixture records a review and a cost in canonical.json but no provider
    # result; that cost has no independent artifact support.
    assert data["canonical_review_usage"]["cost_usd"]["value"] is None
    event = {"kind": "claude.result", "payload": {
        "type": "result", "cost_usd": .03, "usage": {"input_tokens": 7}, "session_id": "review",
    }}
    with (row_dir / "canonical-trajectory.jsonl").open("a", encoding="utf-8") as sink:
        sink.write(json.dumps(event) + "\n")
    data = compare._row(board_dir, row, authenticated=True)
    assert data["canonical_review_usage"]["cost_usd"]["value"] == .03
    assert data["canonical_review_usage"]["cost_usd"]["coverage"] == "partial"
