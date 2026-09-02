"""Staged mode: an approving stand-in for the user, and a reader of two Lean statements."""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from uuid import uuid4

from hardy import acceptance, prompts
from hardy.config import Config
from hardy.domain import EnvironmentIdentity, FormalizationProposal, freeze_claim
from hardy.evals import runner, scoreboard, staged, sweep
from hardy.evals.problems import Entry, ProblemSet, sha256_of
from hardy.storage import RunStore

ENTRY = Entry(id="odd-sum", input="...", name="OddSum", binders="(n : ℕ)", conclusion="∑ i ∈ Finset.range n, (2 * i + 1) = n ^ 2", expected="true", source="textbook", area="sums")
IDENTITY = EnvironmentIdentity(lean_version="4.33.1", lean_commit="8", mathlib_revision="v", lake_manifest_sha256="m" * 64)


def _claim():
    proposal = FormalizationProposal(restatement="", domains=(), quantifiers=(), assumptions=(), interpretation_choices=(),
                                     theorem_name="SumOdd", binders="(n : ℕ)", proposition="∑ k ∈ Finset.range n, (2 * k + 1) = n ^ 2")
    return freeze_claim("the sum of odds", proposal, IDENTITY, datetime(2026, 9, 1, tzinfo=UTC))


def _run_dir(tmp_path: Path) -> Path:
    store = RunStore.create(tmp_path / "runs", "odd-sum", now=datetime(2026, 9, 1, tzinfo=UTC), run_id=uuid4())
    store.write_json(PurePosixPath("formalization.json"), _claim())
    return store.path


class _Runtime:
    backend = "claude"
    isolation_guarantee = "tools-refused"

    def __init__(self, answer):
        self.answer, self.started = answer, []
        self.usage = {"exchanges": 1, "cost_usd": 0.02, "input_tokens": 1, "output_tokens": 1, "cache_write_tokens": None, "cache_read_tokens": None}

    def start(self, **kw):
        self.started.append(kw)
        return object()

    def run_structured(self, thread, stage, prompt, output_type):
        self.prompt = prompt
        if isinstance(self.answer, Exception):
            raise self.answer
        return output_type(**self.answer)


def test_the_canonical_prompt_carries_both_statements_and_lives_outside_the_staged_hash():
    text = prompts.canonical_prompt("theorem A : P", "theorem B : Q")
    assert "theorem A : P" in text and "theorem B : Q" in text and "CANONICAL" in text and "MODEL" in text
    assert "canonical" not in prompts._prompt_set_payload()


def test_an_agreeing_reader_writes_an_agreed_verdict_beside_the_run(tmp_path):
    run_dir = _run_dir(tmp_path)
    runtime = _Runtime({"equivalent": True, "canonical_entails_model": True, "model_entails_canonical": True})
    verdict = staged.compare_canonical(ENTRY, run_dir, tmp_path, runtime_factory=lambda store: runtime, model="reader@test", wall_seconds=60.0)
    assert verdict.outcome == "agreed" and verdict.claim_sha256 == _claim().content_hash
    assert verdict.model_signature == "theorem SumOdd (n : ℕ) : ∑ k ∈ Finset.range n, (2 * k + 1) = n ^ 2"
    assert verdict.canonical_declaration == ENTRY.declaration()
    written = json.loads((tmp_path / "canonical.json").read_text(encoding="utf-8"))
    assert written["outcome"] == "agreed" and written["usage"]["cost_usd"] == 0.02
    import hashlib
    assert hashlib.sha256((tmp_path / "canonical-prompt.md").read_bytes()).hexdigest() == verdict.prompt_sha256
    assert hashlib.sha256((tmp_path / "canonical-schema.json").read_bytes()).hexdigest() == verdict.response_schema_sha256
    assert (tmp_path / "canonical-prompt.md").read_text(encoding="utf-8") == runtime.prompt.split("\n\nRespond", 1)[0]
    assert runtime.started[0]["isolated"] is True and runtime.started[0]["claim"] is None


def test_a_reader_with_notes_or_divergences_disputes_and_an_error_is_unavailable(tmp_path):
    run_dir = _run_dir(tmp_path)
    noted = _Runtime({"equivalent": True, "canonical_entails_model": True, "model_entails_canonical": True, "notes": "index name differs"})
    assert staged.compare_canonical(ENTRY, run_dir, tmp_path / "a", runtime_factory=lambda s: noted, model="r", wall_seconds=60.0).outcome == "disputed"
    broken = _Runtime(ConnectionError("no provider"))
    verdict = staged.compare_canonical(ENTRY, run_dir, tmp_path / "b", runtime_factory=lambda s: broken, model="r", wall_seconds=60.0)
    assert verdict.outcome == "unavailable" and "ConnectionError" in verdict.detail
    assert (tmp_path / "b" / "canonical.json").exists()


def test_a_run_with_no_frozen_claim_is_unavailable_not_a_crash(tmp_path):
    run_dir = tmp_path / "empty"
    run_dir.mkdir()
    verdict = staged.compare_canonical(ENTRY, run_dir, tmp_path / "c", runtime_factory=lambda s: _Runtime({}), model="r", wall_seconds=60.0)
    assert verdict.outcome == "unavailable" and "formalization.json" in verdict.detail


def test_the_approving_terminal_approves_once_and_records_nothing_else():
    terminal = staged.ApprovingTerminal()
    assert terminal.acknowledge_unsafe_execution() is True
    assert terminal.choose_approval() == "approve" and terminal.revision_text() == ""


def test_validate_scoreboard_checks_the_canonical_hashes(tmp_path):
    """The staged branch of scoreboard check 5: canonical.json's hashes, via `validate_scoreboard`.

    `_run_dir` writes only `formalization.json`, no `manifest.json`, so the
    row this builds derives as `invalid` -- that's fine here, and expected:
    this test is about `_canonical_issues`, not a fully valid staged run. For
    a fully audited, `solved` staged row see `_solved_fixture` below.
    """
    scoreboard_dir = tmp_path / "board"
    row_dir = scoreboard_dir / "runs" / "odd-sum" / "staged-0"
    run_dir = _run_dir(row_dir)  # nested under the row directory, as a real staged row would have it

    runtime = _Runtime({"equivalent": True, "canonical_entails_model": True, "model_entails_canonical": True})
    staged.compare_canonical(ENTRY, run_dir, row_dir, runtime_factory=lambda store: runtime, model="reader@test", wall_seconds=60.0)

    row = scoreboard.staged_row(ENTRY, 3, row_dir, scoreboard_dir, repeat=0)
    assert row.outcome == "invalid" and row.mode == "staged"

    problems_path = tmp_path / "problems.json"
    problems_path.write_text(json.dumps(ProblemSet(entries=(ENTRY,)).model_dump(mode="json")), encoding="utf-8")
    baseline = sweep.Baseline(
        created_at=datetime(2026, 9, 1, tzinfo=UTC), problems_sha256=sha256_of(problems_path), environment=IDENTITY,
        heartbeat_budget=200000, wall_backstop_seconds=600.0, singles=sweep.SINGLES, chains=sweep.CHAINS, host={}, problems=(),
        entries={"odd-sum": sweep.EntryBaseline(tier=3, elaborates=True, attempts={}, closed_by=())},
    )
    baseline_path = tmp_path / "baseline.json"
    baseline_path.write_text(json.dumps(baseline.model_dump(mode="json")), encoding="utf-8")

    condition = runner.Condition(model="reader@test", backend="claude", mode="staged", staged_prompt_set_sha256="p" * 64, batch_prompt_set_sha256="q" * 64, hardy_version="0.1.0",
                                 limits={"max_turns": 3, "wall_seconds": 300.0}, repeats=1, selection={"only": None, "tiers": None, "twins": True})
    board = runner.Scoreboard(
        label="x", condition=condition, environment=IDENTITY, baseline_sha256=sha256_of(baseline_path), problems_sha256=sha256_of(problems_path),
        rows=(row,), aggregates=scoreboard.aggregate([row], baseline), started_at=datetime(2026, 9, 1, tzinfo=UTC),
        finished_at=datetime(2026, 9, 1, tzinfo=UTC), interrupted=False,
    )
    (scoreboard_dir / "scoreboard.json").write_text(json.dumps(board.model_dump(mode="json"), indent=2), encoding="utf-8")

    # It validates apart from the one audit finding this row's missing manifest.json always produces.
    issues = scoreboard.validate_scoreboard(scoreboard_dir, problems_path=problems_path, baseline_path=baseline_path)
    assert all("audit reports findings" in issue for issue in issues), issues
    assert not any("canonical-prompt.md" in issue for issue in issues)

    (row_dir / "canonical-prompt.md").write_text("tampered", encoding="utf-8")
    issues = scoreboard.validate_scoreboard(scoreboard_dir, problems_path=problems_path, baseline_path=baseline_path)
    assert any("canonical-prompt.md" in issue for issue in issues)


# --- a fully audited staged run, hermetically -------------------------------
#
# `acceptance.run_deterministic_experiment` gives a genuinely COMPLETED /
# KERNEL_VERIFIED manifest with no model, network, or Lean -- but by its own
# design that is not the same as a *recorded* run
# (`test_recorded_runs.test_the_deterministic_fixture_is_not_mistaken_for_a_recorded_run`):
# its trajectory carries no provider event and its manifest states no usage,
# so `acceptance.validate_recorded_run` finds it wanting, and `staged_row`
# would derive it as `invalid` before ever reaching the solved/solved_other/
# unsolved branches this exercises. The two functions below supply exactly
# the missing evidence -- one synthetic provider event ahead of the
# trajectory's terminal line, a stated usage, and the axiom line a fresh Lean
# would have printed for `two_eq_two` -- and update the two artifact hashes
# the manifest carries for the files that changed. Phase and grade are
# exactly what the deterministic experiment produced; nothing here overrides
# them by hand.
DETERMINISTIC_IDENTITY = acceptance._environment()


def _audit_clean_deterministic_run(row_dir: Path) -> Path:
    config = Config(model="deterministic-no-model", lean_command=("lake", "env", "lean"), lean_project=None, lean_timeout=30.0,
                    latex_command=("tectonic",), root=row_dir, project="workspace", runs_root=row_dir)
    result = acceptance.run_deterministic_experiment(config, outcome="verified")
    run_dir = result.run_dir
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))

    trajectory_path = run_dir / "trajectory.jsonl"
    lines = [json.loads(line) for line in trajectory_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert lines[-1]["kind"] == "workflow.terminal"  # the terminal event must stay last
    lines.insert(-1, {
        "schema_version": 1, "run_id": manifest["run_id"], "sequence": lines[-1]["sequence"], "timestamp": lines[-1]["timestamp"],
        "phase": "proving", "kind": "claude.result", "payload": {"session_id": "deterministic-proving-session"},
    })
    for index, line in enumerate(lines):
        line["sequence"] = index
    trajectory_path.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")

    verification_path = run_dir / "lean" / "verification.json"
    verification = json.loads(verification_path.read_text(encoding="utf-8"))
    verification["diagnostics"] = [{"severity": "information", "message": "'two_eq_two' does not depend on any axioms"}]
    verification_path.write_text(json.dumps(verification, indent=2) + "\n", encoding="utf-8")

    manifest["usage"] = {"exchanges": 1, "cost_usd": None, "input_tokens": None, "output_tokens": None, "cache_write_tokens": None, "cache_read_tokens": None}
    manifest["artifacts"]["trajectory.jsonl"] = hashlib.sha256(trajectory_path.read_bytes()).hexdigest()
    manifest["artifacts"]["lean/verification.json"] = hashlib.sha256(verification_path.read_bytes()).hexdigest()
    (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    assert acceptance.validate_recorded_run(run_dir) == (), acceptance.validate_recorded_run(run_dir)
    return run_dir


class _CanonicalRuntime:
    backend = "claude"
    isolation_guarantee = "tools-refused"

    def __init__(self, answer):
        self.answer = answer
        self.usage = {"exchanges": 1, "cost_usd": 0.02, "input_tokens": 1, "output_tokens": 1, "cache_write_tokens": None, "cache_read_tokens": None}

    def start(self, **kw):
        return object()

    def run_structured(self, thread, stage, prompt, output_type):
        if isinstance(self.answer, Exception):
            raise self.answer
        return output_type(**self.answer)


AGREES = {"equivalent": True, "canonical_entails_model": True, "model_entails_canonical": True}


def _solved_fixture(tmp_path: Path):
    """A scoreboard directory holding one fully audited, agreeing staged row."""
    scoreboard_dir = tmp_path / "board"
    row_dir = scoreboard_dir / "runs" / "odd-sum" / "staged-0"
    row_dir.mkdir(parents=True)
    run_dir = _audit_clean_deterministic_run(row_dir)
    entry = Entry(id="odd-sum", input=(run_dir / "request.md").read_text(encoding="utf-8").strip(), name="OddSum",
                 binders="(n : ℕ)", conclusion="∑ i ∈ Finset.range n, (2 * i + 1) = n ^ 2", expected="true", source="textbook", area="sums")
    staged.compare_canonical(entry, run_dir, row_dir, runtime_factory=lambda store: _CanonicalRuntime(AGREES), model="reader@test", wall_seconds=60.0)

    problems_path = tmp_path / "problems.json"
    problems_path.write_text(json.dumps(ProblemSet(entries=(entry,)).model_dump(mode="json")), encoding="utf-8")
    baseline = sweep.Baseline(
        created_at=datetime(2026, 9, 1, tzinfo=UTC), problems_sha256=sha256_of(problems_path), environment=DETERMINISTIC_IDENTITY,
        heartbeat_budget=200000, wall_backstop_seconds=600.0, singles=sweep.SINGLES, chains=sweep.CHAINS, host={}, problems=(),
        entries={"odd-sum": sweep.EntryBaseline(tier=3, elaborates=True, attempts={}, closed_by=())},
    )
    baseline_path = tmp_path / "baseline.json"
    baseline_path.write_text(json.dumps(baseline.model_dump(mode="json")), encoding="utf-8")
    return scoreboard_dir, row_dir, run_dir, entry, problems_path, baseline_path, baseline


def test_a_solved_staged_row_from_a_fully_audited_deterministic_run(tmp_path):
    scoreboard_dir, row_dir, run_dir, entry, *_ = _solved_fixture(tmp_path)
    row = scoreboard.staged_row(entry, 3, row_dir, scoreboard_dir, repeat=0)
    assert row.outcome == "solved" and row.approval == "automatic" and row.canonical == "agreed" and row.mode == "staged"


def test_a_disputing_reader_leaves_the_row_solved_other(tmp_path):
    scoreboard_dir, row_dir, run_dir, entry, *_ = _solved_fixture(tmp_path)
    disputes = dict(AGREES, notes="the index convention differs")
    staged.compare_canonical(entry, run_dir, row_dir, runtime_factory=lambda store: _CanonicalRuntime(disputes), model="reader@test", wall_seconds=60.0)
    row = scoreboard.staged_row(entry, 3, row_dir, scoreboard_dir, repeat=0)
    assert row.outcome == "solved_other" and row.canonical == "disputed"


def test_a_missing_canonical_file_leaves_the_row_solved_other(tmp_path):
    scoreboard_dir, row_dir, run_dir, entry, *_ = _solved_fixture(tmp_path)
    (row_dir / "canonical.json").unlink()
    row = scoreboard.staged_row(entry, 3, row_dir, scoreboard_dir, repeat=0)
    assert row.outcome == "solved_other" and row.canonical == "unavailable"


def test_a_rewritten_request_md_is_named_by_validator_check_3(tmp_path):
    scoreboard_dir, row_dir, run_dir, entry, problems_path, baseline_path, baseline = _solved_fixture(tmp_path)
    row = scoreboard.staged_row(entry, 3, row_dir, scoreboard_dir, repeat=0)
    condition = runner.Condition(model="reader@test", backend="claude", mode="staged", staged_prompt_set_sha256="p" * 64, batch_prompt_set_sha256="q" * 64, hardy_version="0.1.0",
                                 limits={"active_seconds": 1800, "proof_seconds": 1200, "official_checks": 40, "twin_max_turns": 60, "twin_wall_seconds": 1800.0},
                                 repeats=1, selection={"only": None, "tiers": None, "twins": True})
    board = runner.Scoreboard(
        label="x", condition=condition, environment=DETERMINISTIC_IDENTITY, baseline_sha256=sha256_of(baseline_path), problems_sha256=sha256_of(problems_path),
        rows=(row,), aggregates=scoreboard.aggregate([row], baseline), started_at=datetime(2026, 9, 1, tzinfo=UTC),
        finished_at=datetime(2026, 9, 1, tzinfo=UTC), interrupted=False,
    )
    (scoreboard_dir / "scoreboard.json").write_text(json.dumps(board.model_dump(mode="json"), indent=2), encoding="utf-8")

    (run_dir / "request.md").write_text("Something else entirely.\n", encoding="utf-8")
    issues = scoreboard.validate_scoreboard(scoreboard_dir, problems_path=problems_path, baseline_path=baseline_path)
    assert any("request.md is not the entry's input" in issue for issue in issues)


def test_a_tampered_canonical_prompt_is_a_canonical_issue(tmp_path):
    scoreboard_dir, row_dir, run_dir, entry, *_ = _solved_fixture(tmp_path)
    (row_dir / "canonical-prompt.md").write_text("tampered", encoding="utf-8")
    issues = scoreboard._canonical_issues(row_dir, "where")
    assert any("canonical-prompt.md" in issue for issue in issues)


def test_a_scoreboard_with_one_solved_staged_row_validates_clean(tmp_path):
    scoreboard_dir, row_dir, run_dir, entry, problems_path, baseline_path, baseline = _solved_fixture(tmp_path)
    row = scoreboard.staged_row(entry, 3, row_dir, scoreboard_dir, repeat=0)
    assert row.outcome == "solved"
    condition = runner.Condition(model="reader@test", backend="claude", mode="staged", staged_prompt_set_sha256="p" * 64, batch_prompt_set_sha256="q" * 64, hardy_version="0.1.0",
                                 limits={"active_seconds": 1800, "proof_seconds": 1200, "official_checks": 40, "twin_max_turns": 60, "twin_wall_seconds": 1800.0},
                                 repeats=1, selection={"only": None, "tiers": None, "twins": True})
    board = runner.Scoreboard(
        label="x", condition=condition, environment=DETERMINISTIC_IDENTITY, baseline_sha256=sha256_of(baseline_path), problems_sha256=sha256_of(problems_path),
        rows=(row,), aggregates=scoreboard.aggregate([row], baseline), started_at=datetime(2026, 9, 1, tzinfo=UTC),
        finished_at=datetime(2026, 9, 1, tzinfo=UTC), interrupted=False,
    )
    (scoreboard_dir / "scoreboard.json").write_text(json.dumps(board.model_dump(mode="json"), indent=2), encoding="utf-8")

    assert scoreboard.validate_scoreboard(scoreboard_dir, problems_path=problems_path, baseline_path=baseline_path) == ()
