"""Staged mode: an approving stand-in for the user, and a reader of two Lean statements."""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from uuid import uuid4

import pytest
from corpus_helpers import write_corpus
from pydantic import ValidationError

from hardy import acceptance, prompts
from hardy.config import Config
from hardy.domain import EnvironmentIdentity, FormalizationProposal, RunPhase, freeze_claim
from hardy.evals import runner, scoreboard, staged, sweep
from hardy.evals.corpus import load_corpus, manifest_digest
from hardy.evals.problems import Entry, sha256_of
from hardy.storage import RunStore

ENTRY = Entry(id="odd-sum", input="...", name="OddSum", binders="(n : ℕ)", conclusion="∑ i ∈ Finset.range n, (2 * i + 1) = n ^ 2", expected="true", source="textbook", msc=("11A",), difficulty="routine", rationale="test fixture", witness=None, witness_note="test fixture")
HOST = sweep.host_info()
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


def _verdict_kwargs(**overrides) -> dict:
    review = staged.CanonicalReview(equivalent=True, canonical_entails_model=True, model_entails_canonical=True)
    base = dict(claim_sha256="c" * 64, entry_id="odd-sum", canonical_declaration=ENTRY.declaration(), model_signature="theorem SumOdd : True",
               reviewer_model="reader@test", reviewer_backend="claude", prompt_sha256="p" * 64, response_schema_sha256="s" * 64,
               outcome="agreed", review=review, usage={})
    base.update(overrides)
    return base


@pytest.mark.parametrize("field", ["claim_sha256", "model_signature", "prompt_sha256", "response_schema_sha256"])
@pytest.mark.parametrize("outcome", ["agreed", "disputed"])
def test_an_agreed_or_disputed_verdict_requires_every_identity_field(field, outcome):
    """An `agreed`/`disputed` verdict binds one specific review to one
    specific claim, prompt and schema (item 3); leaving any of these `None`
    -- the shape only the no-formalization `unavailable` path is allowed to
    take -- would let a reader trajectory copied from comparing a *different*
    formalization supply the agreeing review here, unbound from this row's
    frozen statement.
    """
    review = staged.CanonicalReview(equivalent=(outcome == "agreed"), canonical_entails_model=True, model_entails_canonical=True,
                                    notes="" if outcome == "agreed" else "differs")
    kwargs = _verdict_kwargs(outcome=outcome, review=review)
    kwargs[field] = None
    with pytest.raises(ValidationError, match=field):
        staged.CanonicalVerdict(**kwargs)


def test_an_unavailable_verdict_may_leave_every_identity_field_null():
    verdict = staged.CanonicalVerdict(**dict(_verdict_kwargs(outcome="unavailable", review=None), claim_sha256=None, model_signature=None,
                                             prompt_sha256=None, response_schema_sha256=None, detail="no formalization.json"))
    assert verdict.outcome == "unavailable"


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

    problems_path = write_corpus(tmp_path / "corpus", (ENTRY,))
    baseline = sweep.Baseline(
        created_at=datetime(2026, 9, 1, tzinfo=UTC), problems_sha256=manifest_digest(problems_path), environment=IDENTITY,
        environment_digest=sweep.environment_digest_of(IDENTITY, HOST), procedure_digest=sweep.procedure_digest_of(),
        statement_digests={ENTRY.id: ENTRY.statement_digest()},
        heartbeat_budget=200000, wall_backstop_seconds=600.0, singles=sweep.SINGLES, chains=sweep.CHAINS, host=HOST, problems=(),
        # `elaborates=False`, not `True`: these fixtures never actually swept
        # "odd-sum" against any tactic, and `sweep.Baseline`'s completeness
        # check (item 4) now requires an `elaborates=True` entry's `attempts`
        # to name every one of the baseline's own singles+chains -- honest
        # only for an entry a real sweep produced.
        entries={"odd-sum": sweep.EntryBaseline(tier=3, elaborates=False, attempts={}, closed_by=())},
    )
    baseline_path = tmp_path / "baseline.json"
    baseline_path.write_text(json.dumps(baseline.model_dump(mode="json")), encoding="utf-8")

    condition = runner.Condition(model="reader@test", backend="claude", mode="staged", staged_prompt_set_sha256="p" * 64, batch_prompt_set_sha256="q" * 64, hardy_version="0.1.0",
                                 limits={"max_turns": 3, "wall_seconds": 300.0}, repeats=1, selection={"only": None, "tiers": None, "twins": True})
    board = runner.Scoreboard(
        label="x", condition=condition, environment=IDENTITY, baseline_sha256=sha256_of(baseline_path), problems_sha256=manifest_digest(problems_path),
        rows=(row,), aggregates=scoreboard.aggregate([row], baseline, active_ids=scoreboard.active_ids(load_corpus(problems_path))), started_at=datetime(2026, 9, 1, tzinfo=UTC),
        finished_at=datetime(2026, 9, 1, tzinfo=UTC), interrupted=False,
    )
    (scoreboard_dir / "scoreboard.json").write_text(json.dumps(board.model_dump(mode="json"), indent=2), encoding="utf-8")

    # It validates apart from the one audit finding this row's missing manifest.json always produces.
    issues = scoreboard.validate_scoreboard(scoreboard_dir, problems_path=problems_path, baseline_path=baseline_path)
    assert all("audit reports findings" in issue for issue in issues), issues
    assert not any("canonical-prompt.md" in issue for issue in issues)

    # A row the recorded-run audit already rejects skips every
    # artifact-dependent check, `_canonical_issues` included (item 3,
    # `validate_scoreboard`'s `continue` on `outcome == "invalid"`): tampering
    # `canonical-prompt.md` here adds no further finding, since nothing reads
    # it for a row this invalid.
    (row_dir / "canonical-prompt.md").write_text("tampered", encoding="utf-8")
    issues = scoreboard.validate_scoreboard(scoreboard_dir, problems_path=problems_path, baseline_path=baseline_path)
    assert all("audit reports findings" in issue for issue in issues), issues


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
    """A fake `ClaudeStagedRuntime` whose `run_structured` also records the
    reader's reply through the store, the way the real one does (item 2):
    `ClaudeStagedRuntime._observe` appends every provider event as
    `{"kind": "claude." + event["type"], "phase": ..., "payload": event}`, and
    a completed text block is observed as `{"type": "assistant", "message":
    {"role": "assistant", "content": <reply text>}}` (`claude_runtime.py:_note`).
    `scoreboard._canonical_issues` derives the review from exactly that
    payload shape, so a fixture that never wrote it could not exercise that
    check.
    """

    backend = "claude"
    isolation_guarantee = "tools-refused"

    def __init__(self, answer, store=None):
        self.answer, self.store = answer, store
        self.usage = {"exchanges": 1, "cost_usd": 0.02, "input_tokens": 1, "output_tokens": 1, "cache_write_tokens": None, "cache_read_tokens": None}

    def start(self, **kw):
        return object()

    def run_structured(self, thread, stage, prompt, output_type):
        if isinstance(self.answer, Exception):
            raise self.answer
        result = output_type(**self.answer)
        if self.store is not None:
            self.store.append(
                "claude.assistant",
                {"type": "assistant", "message": {"role": "assistant", "content": result.model_dump_json()}},
                phase=RunPhase.AWAITING_APPROVAL,
            )
        return result


AGREES = {"equivalent": True, "canonical_entails_model": True, "model_entails_canonical": True}


def _solved_fixture(tmp_path: Path):
    """A scoreboard directory holding one fully audited, agreeing staged row."""
    scoreboard_dir = tmp_path / "board"
    row_dir = scoreboard_dir / "runs" / "odd-sum" / "staged-0"
    row_dir.mkdir(parents=True)
    run_dir = _audit_clean_deterministic_run(row_dir)
    entry = Entry(id="odd-sum", input=(run_dir / "request.md").read_text(encoding="utf-8").strip(), name="OddSum",
                 binders="(n : ℕ)", conclusion="∑ i ∈ Finset.range n, (2 * i + 1) = n ^ 2", expected="true", source="textbook", msc=("11A",), difficulty="routine", rationale="test fixture", witness=None, witness_note="test fixture")
    staged.compare_canonical(entry, run_dir, row_dir, runtime_factory=lambda store: _CanonicalRuntime(AGREES, store), model="reader@test", wall_seconds=60.0)

    problems_path = write_corpus(tmp_path / "corpus", (entry,))
    baseline = sweep.Baseline(
        created_at=datetime(2026, 9, 1, tzinfo=UTC), problems_sha256=manifest_digest(problems_path), environment=DETERMINISTIC_IDENTITY,
        environment_digest=sweep.environment_digest_of(DETERMINISTIC_IDENTITY, HOST), procedure_digest=sweep.procedure_digest_of(),
        statement_digests={entry.id: entry.statement_digest()},
        heartbeat_budget=200000, wall_backstop_seconds=600.0, singles=sweep.SINGLES, chains=sweep.CHAINS, host=HOST, problems=(),
        # `elaborates=False`, not `True`: these fixtures never actually swept
        # "odd-sum" against any tactic, and `sweep.Baseline`'s completeness
        # check (item 4) now requires an `elaborates=True` entry's `attempts`
        # to name every one of the baseline's own singles+chains -- honest
        # only for an entry a real sweep produced.
        entries={"odd-sum": sweep.EntryBaseline(tier=3, elaborates=False, attempts={}, closed_by=())},
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
    staged.compare_canonical(entry, run_dir, row_dir, runtime_factory=lambda store: _CanonicalRuntime(disputes, store), model="reader@test", wall_seconds=60.0)
    row = scoreboard.staged_row(entry, 3, row_dir, scoreboard_dir, repeat=0)
    assert row.outcome == "solved_other" and row.canonical == "disputed"


def test_a_missing_canonical_file_leaves_the_row_solved_other(tmp_path):
    scoreboard_dir, row_dir, run_dir, entry, *_ = _solved_fixture(tmp_path)
    (row_dir / "canonical.json").unlink()
    row = scoreboard.staged_row(entry, 3, row_dir, scoreboard_dir, repeat=0)
    assert row.outcome == "solved_other" and row.canonical == "unavailable"


def test_a_canonical_json_that_is_a_json_array_leaves_the_row_unavailable_not_a_crash(tmp_path):
    """Reading `canonical.json` as raw JSON and indexing `["outcome"]` used to
    raise `JSONDecodeError`, `AttributeError`, or a pydantic `ValidationError`
    for a corrupt file -- invalid JSON, a JSON array, or an unknown `outcome`
    -- crashing `staged_row` (and, through it, `validate_scoreboard`) before
    ever reaching `_canonical_issues`'s own report of the same file as a
    finding (item 5). Parsed defensively instead: any failure to validate as
    a `CanonicalVerdict` leaves the row's `canonical` simply `"unavailable"`.
    """
    scoreboard_dir, row_dir, run_dir, entry, problems_path, baseline_path, baseline = _solved_fixture(tmp_path)
    (row_dir / "canonical.json").write_text("[]", encoding="utf-8")
    row = scoreboard.staged_row(entry, 3, row_dir, scoreboard_dir, repeat=0)
    assert row.outcome == "solved_other" and row.canonical == "unavailable"

    condition = _deterministic_condition()
    board = runner.Scoreboard(
        label="x", condition=condition, environment=DETERMINISTIC_IDENTITY, baseline_sha256=sha256_of(baseline_path), problems_sha256=manifest_digest(problems_path),
        rows=(row,), aggregates=scoreboard.aggregate([row], baseline, active_ids=scoreboard.active_ids(load_corpus(problems_path))), started_at=datetime(2026, 9, 1, tzinfo=UTC),
        finished_at=datetime(2026, 9, 1, tzinfo=UTC), interrupted=False,
    )
    (scoreboard_dir / "scoreboard.json").write_text(json.dumps(board.model_dump(mode="json"), indent=2), encoding="utf-8")

    # `_canonical_issues` is what actually names the malformed file, since
    # the row otherwise validates: `validate_scoreboard` must reach it rather
    # than crash first.
    issues = scoreboard.validate_scoreboard(scoreboard_dir, problems_path=problems_path, baseline_path=baseline_path)
    assert any("canonical.json" in i for i in issues), issues


def test_a_canonical_review_rewritten_to_agree_while_the_trajectory_disputes_is_a_finding(tmp_path):
    """The review is derived from the reader's own recorded reply, not read
    back from the editable verdict (item 2): `canonical.json` can otherwise be
    rewritten with a self-consistent agreeing `review` after a disputed
    comparison, and the row and aggregates updated to match, while
    `canonical-trajectory.jsonl` still carries the reader's actual disputing
    reply.
    """
    scoreboard_dir, row_dir, run_dir, entry, *_ = _solved_fixture(tmp_path)
    disputes = dict(AGREES, notes="the index convention differs")
    staged.compare_canonical(entry, run_dir, row_dir, runtime_factory=lambda store: _CanonicalRuntime(disputes, store), model="reader@test", wall_seconds=60.0)

    def rewrite_to_agree(payload):
        payload["outcome"] = "agreed"
        payload["review"]["notes"] = ""

    _edit_canonical(row_dir, rewrite_to_agree)
    issues = scoreboard._canonical_issues(entry, row_dir, "where")
    assert any("does not match the reader's reply" in i for i in issues), issues


def _deterministic_condition(**kw) -> runner.Condition:
    """A condition whose provenance fields actually match `_audit_clean_deterministic_run`'s
    manifest (item 2): `config.model="deterministic-no-model"` there, and its
    `identities_factory` records the real `PROMPT_SET_SHA256`, not a fixture
    stand-in -- so a "validates clean" fixture must use both, or the new
    condition-provenance cross-check would name a mismatch of its own making.
    """
    base = dict(model="deterministic-no-model", backend="claude", mode="staged", staged_prompt_set_sha256=prompts.PROMPT_SET_SHA256,
                batch_prompt_set_sha256="q" * 64, hardy_version="0.1.0",
                limits={"active_seconds": 1800, "proof_seconds": 1200, "official_checks": 40, "twin_max_turns": 60, "twin_wall_seconds": 1800.0},
                repeats=1, selection={"only": None, "tiers": None, "twins": True})
    base.update(kw)
    return runner.Condition(**base)


def test_a_rewritten_request_md_is_named_by_validator_check_3(tmp_path):
    scoreboard_dir, row_dir, run_dir, entry, problems_path, baseline_path, baseline = _solved_fixture(tmp_path)
    row = scoreboard.staged_row(entry, 3, row_dir, scoreboard_dir, repeat=0)
    condition = _deterministic_condition()
    board = runner.Scoreboard(
        label="x", condition=condition, environment=DETERMINISTIC_IDENTITY, baseline_sha256=sha256_of(baseline_path), problems_sha256=manifest_digest(problems_path),
        rows=(row,), aggregates=scoreboard.aggregate([row], baseline, active_ids=scoreboard.active_ids(load_corpus(problems_path))), started_at=datetime(2026, 9, 1, tzinfo=UTC),
        finished_at=datetime(2026, 9, 1, tzinfo=UTC), interrupted=False,
    )
    (scoreboard_dir / "scoreboard.json").write_text(json.dumps(board.model_dump(mode="json"), indent=2), encoding="utf-8")

    # Kept self-consistent with the manifest's own hash of it (as a real
    # attacker's swapped-in run would be): otherwise the recorded-run audit's
    # own hash check reports this row `invalid` first, and item 3 then skips
    # `_entry_issues` before ever reaching the check this test is about.
    new_request = b"Something else entirely.\n"
    (run_dir / "request.md").write_bytes(new_request)
    manifest_path = run_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["artifacts"]["request.md"] = hashlib.sha256(new_request).hexdigest()
    manifest_path.write_bytes((json.dumps(manifest, indent=2) + "\n").encode("utf-8"))

    issues = scoreboard.validate_scoreboard(scoreboard_dir, problems_path=problems_path, baseline_path=baseline_path)
    assert any("request.md is not the entry's input" in issue for issue in issues), issues


def test_a_tampered_canonical_prompt_is_a_canonical_issue(tmp_path):
    scoreboard_dir, row_dir, run_dir, entry, *_ = _solved_fixture(tmp_path)
    (row_dir / "canonical-prompt.md").write_text("tampered", encoding="utf-8")
    issues = scoreboard._canonical_issues(entry, row_dir, "where")
    assert any("canonical-prompt.md" in issue for issue in issues)


def _edit_canonical(row_dir: Path, mutate) -> None:
    path = row_dir / "canonical.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    mutate(payload)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def test_a_canonical_json_naming_a_different_entry_id_is_a_finding(tmp_path):
    """The comparison inputs are recomputed from the entry and the frozen
    claim, not read back from the editable verdict (item 1): a comparison of
    a different statement could otherwise retain an agreeing review.
    """
    scoreboard_dir, row_dir, run_dir, entry, *_ = _solved_fixture(tmp_path)
    _edit_canonical(row_dir, lambda c: c.__setitem__("entry_id", "some-other-entry"))
    issues = scoreboard._canonical_issues(entry, row_dir, "where")
    assert any("entry_id" in issue for issue in issues), issues


def test_a_canonical_json_naming_a_different_canonical_declaration_is_a_finding(tmp_path):
    scoreboard_dir, row_dir, run_dir, entry, *_ = _solved_fixture(tmp_path)
    _edit_canonical(row_dir, lambda c: c.__setitem__("canonical_declaration", "theorem SomeOtherClaim : False"))
    issues = scoreboard._canonical_issues(entry, row_dir, "where")
    assert any("canonical_declaration" in issue for issue in issues), issues


def test_a_canonical_json_naming_a_different_model_signature_is_a_finding(tmp_path):
    scoreboard_dir, row_dir, run_dir, entry, *_ = _solved_fixture(tmp_path)
    _edit_canonical(row_dir, lambda c: c.__setitem__("model_signature", "theorem SomeOtherModelClaim : False"))
    issues = scoreboard._canonical_issues(entry, row_dir, "where")
    assert any("model_signature" in issue for issue in issues), issues


def test_a_canonical_prompt_rendered_for_a_different_statement_is_a_finding(tmp_path):
    """`canonical-prompt.md` still hashes to whatever the verdict claims it
    does (a self-consistent tamper), but is not the prompt `canonical_prompt`
    actually renders from this entry and this row's frozen claim.
    """
    scoreboard_dir, row_dir, run_dir, entry, *_ = _solved_fixture(tmp_path)
    different = prompts.canonical_prompt("theorem SomeOtherClaim : False", "theorem SomeOtherClaim : False")
    _edit_canonical(row_dir, lambda c: c.__setitem__("prompt_sha256", hashlib.sha256(different.encode("utf-8")).hexdigest()))
    (row_dir / "canonical-prompt.md").write_bytes(different.encode("utf-8"))
    issues = scoreboard._canonical_issues(entry, row_dir, "where")
    assert any("canonical-prompt.md is not the prompt rendered from the entry and frozen claim" in issue for issue in issues), issues


def test_a_scoreboard_with_one_solved_staged_row_validates_clean(tmp_path):
    scoreboard_dir, row_dir, run_dir, entry, problems_path, baseline_path, baseline = _solved_fixture(tmp_path)
    row = scoreboard.staged_row(entry, 3, row_dir, scoreboard_dir, repeat=0)
    assert row.outcome == "solved"
    condition = _deterministic_condition()
    board = runner.Scoreboard(
        label="x", condition=condition, environment=DETERMINISTIC_IDENTITY, baseline_sha256=sha256_of(baseline_path), problems_sha256=manifest_digest(problems_path),
        rows=(row,), aggregates=scoreboard.aggregate([row], baseline, active_ids=scoreboard.active_ids(load_corpus(problems_path))), started_at=datetime(2026, 9, 1, tzinfo=UTC),
        finished_at=datetime(2026, 9, 1, tzinfo=UTC), interrupted=False,
    )
    (scoreboard_dir / "scoreboard.json").write_text(json.dumps(board.model_dump(mode="json"), indent=2), encoding="utf-8")

    assert scoreboard.validate_scoreboard(scoreboard_dir, problems_path=problems_path, baseline_path=baseline_path) == ()


def test_a_condition_model_mismatch_is_a_finding_for_a_staged_row(tmp_path):
    """A committed scoreboard whose `condition.model` was edited (or whose
    run directory was copied in from a different experiment) must not
    validate clean (item 2): nothing before this compared `condition` against
    the staged manifest each row actually carries.
    """
    scoreboard_dir, row_dir, run_dir, entry, problems_path, baseline_path, baseline = _solved_fixture(tmp_path)
    row = scoreboard.staged_row(entry, 3, row_dir, scoreboard_dir, repeat=0)
    assert row.outcome == "solved"
    condition = _deterministic_condition(model="a-different-model")
    board = runner.Scoreboard(
        label="x", condition=condition, environment=DETERMINISTIC_IDENTITY, baseline_sha256=sha256_of(baseline_path), problems_sha256=manifest_digest(problems_path),
        rows=(row,), aggregates=scoreboard.aggregate([row], baseline, active_ids=scoreboard.active_ids(load_corpus(problems_path))), started_at=datetime(2026, 9, 1, tzinfo=UTC),
        finished_at=datetime(2026, 9, 1, tzinfo=UTC), interrupted=False,
    )
    (scoreboard_dir / "scoreboard.json").write_text(json.dumps(board.model_dump(mode="json"), indent=2), encoding="utf-8")

    issues = scoreboard.validate_scoreboard(scoreboard_dir, problems_path=problems_path, baseline_path=baseline_path)
    assert any("model" in issue and "a-different-model" in issue for issue in issues), issues


def test_a_staged_environment_mismatch_is_a_finding(tmp_path):
    """A staged row's own recorded environment (its manifest's) must match
    the scoreboard's, not merely the baseline's (item 1): copying in a run
    made under a different Lean or Mathlib revision must not be credited to
    this board's toolchain.
    """
    scoreboard_dir, row_dir, run_dir, entry, problems_path, baseline_path, baseline = _solved_fixture(tmp_path)
    row = scoreboard.staged_row(entry, 3, row_dir, scoreboard_dir, repeat=0)
    assert row.outcome == "solved"
    condition = _deterministic_condition()
    board = runner.Scoreboard(
        label="x", condition=condition, environment=DETERMINISTIC_IDENTITY, baseline_sha256=sha256_of(baseline_path), problems_sha256=manifest_digest(problems_path),
        rows=(row,), aggregates=scoreboard.aggregate([row], baseline, active_ids=scoreboard.active_ids(load_corpus(problems_path))), started_at=datetime(2026, 9, 1, tzinfo=UTC),
        finished_at=datetime(2026, 9, 1, tzinfo=UTC), interrupted=False,
    )
    (scoreboard_dir / "scoreboard.json").write_text(json.dumps(board.model_dump(mode="json"), indent=2), encoding="utf-8")

    payload = json.loads((scoreboard_dir / "scoreboard.json").read_text(encoding="utf-8"))
    payload["environment"]["lean_commit"] = "some-other-commit"
    (scoreboard_dir / "scoreboard.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")

    issues = scoreboard.validate_scoreboard(scoreboard_dir, problems_path=problems_path, baseline_path=baseline_path)
    assert any("run's environment" in i for i in issues), issues


def test_a_staged_condition_backend_mismatch_is_a_finding(tmp_path):
    """The offline checker must not be able to certify an existing Claude run
    as Codex (item 5): `run_set_command` refuses `--backend codex` before any
    run starts, so a staged trajectory is always Claude-shaped, but nothing
    before this compared the recorded provider events against the condition's
    own backend.
    """
    scoreboard_dir, row_dir, run_dir, entry, problems_path, baseline_path, baseline = _solved_fixture(tmp_path)
    row = scoreboard.staged_row(entry, 3, row_dir, scoreboard_dir, repeat=0)
    assert row.outcome == "solved"
    condition = _deterministic_condition(backend="codex")
    board = runner.Scoreboard(
        label="x", condition=condition, environment=DETERMINISTIC_IDENTITY, baseline_sha256=sha256_of(baseline_path), problems_sha256=manifest_digest(problems_path),
        rows=(row,), aggregates=scoreboard.aggregate([row], baseline, active_ids=scoreboard.active_ids(load_corpus(problems_path))), started_at=datetime(2026, 9, 1, tzinfo=UTC),
        finished_at=datetime(2026, 9, 1, tzinfo=UTC), interrupted=False,
    )
    (scoreboard_dir / "scoreboard.json").write_text(json.dumps(board.model_dump(mode="json"), indent=2), encoding="utf-8")

    issues = scoreboard.validate_scoreboard(scoreboard_dir, problems_path=problems_path, baseline_path=baseline_path)
    assert any("codex" in i for i in issues), issues
