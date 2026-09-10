"""Scripted reuse runs test exposure accounting, not model performance."""
from __future__ import annotations

import importlib
import json
from datetime import UTC, datetime
from hashlib import sha256

import pytest
import test_evals_runner as fixtures

from hardy.evals import compare, identity, outstanding, runner, scoreboard
from hardy.foundation.values import json_digest
from hardy.workflows import retrieval
from hardy.workflows.ledger.contracts import ProjectItem, Scope
from hardy.workflows.ledger.store import LedgerStore


@pytest.fixture
def exposure():
    try:
        return importlib.import_module("hardy.evals.exposure")
    except ModuleNotFoundError:
        pytest.fail("H2 exposure accounting is not implemented")


@pytest.fixture
def source(tmp_path):
    store = LedgerStore(tmp_path / "memory")
    concept = ProjectItem(id="prior-concept", kind="concept", name="Recorded concept",
                          statement="An explicitly recorded prior concept.", origin="human_authored")
    scope = Scope(id="scope")
    store.append((concept, scope), expected_revision=0)
    index = retrieval.build_index((retrieval.ledger_source("project", store),))
    retriever = retrieval.ProjectRetriever(index, read_source=lambda _: retrieval.ledger_source("project", store))
    query = retrieval.RetrievalQuery(project_source="project", text=concept.id, scope=scope.ref,
                                    environment=fixtures.IDENTITY)
    return store, concept, index, retriever, query


def plan(exposure, source, *, cohort="declared_local_heldout", enabled=True, complete=True):
    _, concept, index, _, _ = source
    prior_family = "unrelated" if cohort == "declared_local_heldout" else "target-family"
    relation = () if cohort == "declared_local_heldout" else (exposure.PriorRelation(
        source_id="project", subject=concept.ref, kind=cohort, reason="Explicit scripted split relationship"),)
    return exposure.ExposurePlan(index=index, enabled=enabled, source_universe_complete=complete,
        query_settings=exposure.QuerySettings.model_validate(source[-1].model_dump(exclude={"text"})),
        other_context_complete=True, provider_context="fresh", split_author="scripted fixture author",
        families=(exposure.IndexFamily(source_id="project", subject=concept.ref, family=prior_family),),
        assignments=tuple(exposure.ExposureAssignment(problem_id=e.id, statement_sha256=e.statement_digest(),
            family="target-family", intended=cohort, prior=relation) for e in fixtures.ENTRIES[:2]))


def run_board(tmp_path, monkeypatch, exposure, source, treatment, *, label="run", query=True, deliver=True, detached=False):
    problems, baseline = fixtures._files(tmp_path)
    condition = fixtures._condition(selection={"only": ["t", "u"], "twins": False}, exposure=treatment)
    digest = identity.run_procedure_digest_of(model=condition.model, mode=condition.mode,
        limits=condition.limits, repeats=condition.repeats, exposure=treatment)
    condition = condition.model_copy(update={"run_procedure_digest": digest})
    base = fixtures._batch_runner({"t": fixtures.SOLVE, "u": fixtures.GIVE_UP})
    original = fixtures._Runtime

    def run_exposed(entry, row_dir, mode, recorder):
        assert mode == "batch"
        if not query or not treatment.enabled:
            base(entry, row_dir, 3, 300)
            return
        number = recorder.query(source[-1], source[-2])
        if not deliver:
            base(entry, row_dir, 3, 300)
            return

        def consume(payload):
            receipts = []

            class Runtime(original):
                def ask(self, text):
                    if detached:
                        (row_dir / "provider-input.txt").write_text(payload, encoding="utf-8")
                        return super().ask(text)
                    response, receipt = recorder.forward(number, text, super().ask, self.context["observe"])
                    receipts.append(receipt)
                    return response

            monkeypatch.setattr(fixtures, "_Runtime", Runtime)
            base(entry, row_dir, 3, 300)
            if detached:
                artifact = row_dir / "provider-input.txt"
                return exposure.DeliveryReceipt(path=artifact.name, sha256=sha256(artifact.read_bytes()).hexdigest())
            return receipts[0]

        recorder.deliver(number, consume)

    path = runner.run_set(label=label, problems_path=problems, baseline_path=baseline,
        scoreboards_root=tmp_path / "boards", condition=condition, environment=fixtures.IDENTITY,
        batch_runner=base, run_exposed=run_exposed,
        now=lambda: datetime(2026, 9, 1, tzinfo=UTC), report=lambda _: None)
    return path, {"problems_path": problems, "baseline_path": baseline}


@pytest.mark.parametrize("cohort", ["exact_repeat", "related_transfer", "declared_local_heldout"])
def test_real_runner_and_reader_separate_explicit_exposure_cohorts(tmp_path, monkeypatch, exposure, source, cohort):
    treatment = plan(exposure, source, cohort=cohort)
    path, kwargs = run_board(tmp_path, monkeypatch, exposure, source, treatment)
    assert scoreboard.scoreboard_self_issues(path, **kwargs) == ()
    result = compare.compare(path, path, varying=(), **kwargs)
    assert result["sides"]["left"]["exposure_cohorts"][cohort]["rows"] == 2
    assert result["sides"]["left"]["exposure_cohorts"][cohort]["solved"] == 1
    assert all(row["exposure"]["cohort"] == cohort for row in result["rows"]["left"])
    assert all(row["exposure"]["provider_pretraining"] == "unknown" for row in result["rows"]["left"])
    assert source[2].digest == treatment.index.digest
    assert source[0].read().revision == 1  # Evaluation outputs did not enter retrieval.


@pytest.mark.parametrize("incomplete", ["disabled", "inventory", "query", "delivery", "empty", "source", "truncated"])
def test_incomplete_exposure_cannot_establish_heldout(tmp_path, monkeypatch, exposure, source, incomplete):
    treatment = plan(exposure, source, enabled=incomplete != "disabled", complete=incomplete != "inventory")
    if incomplete == "empty":
        source = (*source[:-1], source[-1].model_copy(update={"text": "no matching concept"}))
    if incomplete == "source":
        source[0].append((source[1].model_copy(update={"statement": "Changed prior work"}),), expected_revision=1)
    if incomplete == "truncated":
        source = (*source[:-1], source[-1].model_copy(update={"max_characters": 1}))
        treatment = plan(exposure, source)
    path, kwargs = run_board(tmp_path, monkeypatch, exposure, source, treatment,
                            query=incomplete != "query", deliver=incomplete != "delivery")
    result = compare.compare(path, path, varying=(), **kwargs)
    assert all(row["exposure"]["cohort"] == "unknown" for row in result["rows"]["left"])
    assert all(row["exposure"]["reasons"] for row in result["rows"]["left"])


@pytest.mark.parametrize("attack", ["missing", "truncated", "tampered", "delivery"])
def test_reader_rejects_damaged_exposure_and_todo_does_not_count_it(tmp_path, monkeypatch, exposure, source, attack):
    path, kwargs = run_board(tmp_path, monkeypatch, exposure, source, plan(exposure, source))
    board = runner.Scoreboard.model_validate_json((path / "scoreboard.json").read_text())
    row_dir = path / board.rows[0].run_dir
    journal = row_dir / "exposure.jsonl"
    if attack == "missing":
        journal.unlink()
    elif attack == "truncated":
        journal.write_text(journal.read_text().splitlines()[0] + "\n")
    elif attack == "tampered":
        journal.write_text(journal.read_text().replace("target-family", "different-family"))
    else:
        next(row_dir.glob("exposure-input-*.txt")).write_text("A different provider input")
    assert any("exposure" in issue for issue in scoreboard.scoreboard_self_issues(path, **kwargs))
    key = (board.condition.run_procedure_digest, outstanding.environment_digest_of_board(board.model_dump(mode="json")))
    assert outstanding.matching_boards(path.parent, key=key) == []
    result = compare.compare(path, path, varying=(), **kwargs)
    assert all(row["exposure"]["cohort"] == "unknown" for row in result["rows"]["left"])


def test_identity_changes_with_index_split_and_retrieval_treatment(exposure, source):
    value = plan(exposure, source)
    options = [None, value, value.model_copy(update={"enabled": False}),
               value.model_copy(update={"query_settings": value.query_settings.model_copy(update={"limit": 1})}),
               value.model_copy(update={"split_author": "different attributed split"})]
    source[0].append((source[1].model_copy(update={"statement": "Another prior concept"}),), expected_revision=1)
    changed = retrieval.build_index((retrieval.ledger_source("project", source[0]),))
    options.append(value.model_copy(update={"index": changed, "families": (), "assignments": ()}))
    keys = {identity.run_procedure_digest_of(model="m", mode="batch", limits={}, repeats=1, exposure=option)
            for option in options}
    assert len(keys) == len(options)


def test_explicit_index_cannot_change_between_rows(tmp_path, monkeypatch, exposure, source):
    treatment = plan(exposure, source)
    recorder = exposure.ExposureRecorder(tmp_path / "row", treatment, problem_id="t", repeat=0,
                                        statement_sha256=fixtures.ENTRIES[0].statement_digest())
    source[0].append((source[1].model_copy(update={"statement": "Earlier evaluation output"}),), expected_revision=1)
    changed = retrieval.build_index((retrieval.ledger_source("project", source[0]),))
    source[-2].index = changed
    with pytest.raises(ValueError, match="frozen index"):
        recorder.query(source[-1], source[-2])


def test_actual_query_budget_must_match_recorded_treatment(tmp_path, exposure, source):
    recorder = exposure.ExposureRecorder(tmp_path / "row", plan(exposure, source), problem_id="t", repeat=0,
                                        statement_sha256=fixtures.ENTRIES[0].statement_digest())
    with pytest.raises(ValueError, match="query settings"):
        recorder.query(source[-1].model_copy(update={"limit": 1}), source[-2])


def test_detached_input_file_cannot_claim_provider_delivery(tmp_path, monkeypatch, exposure, source):
    with pytest.raises(ValueError, match="provider"):
        run_board(tmp_path, monkeypatch, exposure, source, plan(exposure, source), detached=True)


def test_run_identity_cannot_be_relabelled_for_pooling(tmp_path, monkeypatch, exposure, source):
    path, kwargs = run_board(tmp_path, monkeypatch, exposure, source, plan(exposure, source))
    board_path = path / "scoreboard.json"
    board = json.loads(board_path.read_text())
    board["condition"]["run_procedure_digest"] = "f" * 64
    board_path.write_text(json.dumps(board), encoding="utf-8")
    assert any("exposure" in issue for issue in scoreboard.scoreboard_self_issues(path, **kwargs))
    key = ("f" * 64, outstanding.environment_digest_of_board(board))
    assert outstanding.matching_boards(path.parent, key=key) == []


def test_removed_exposure_identity_does_not_hide_existing_journal_from_todo(tmp_path, monkeypatch, exposure, source):
    path, _ = run_board(tmp_path, monkeypatch, exposure, source, plan(exposure, source))
    board_path = path / "scoreboard.json"
    board = json.loads(board_path.read_text())
    board["condition"].pop("exposure")
    for row in board["rows"]:
        row.pop("exposure_sha256")
    board_path.write_text(json.dumps(board), encoding="utf-8")
    key = (board["condition"]["run_procedure_digest"], outstanding.environment_digest_of_board(board))
    assert outstanding.matching_boards(path.parent, key=key) == []


def test_split_cannot_claim_prior_relationship_to_unindexed_record(exposure, source):
    value = plan(exposure, source, cohort="related_transfer")
    data = value.model_dump(mode="json")
    data["assignments"][0]["prior"][0]["subject"]["digest"] = "f" * 64
    with pytest.raises(ValueError, match="indexed"):
        exposure.ExposurePlan.model_validate(data)


def test_runner_refuses_unbound_split_before_any_model_call(tmp_path, monkeypatch, exposure, source):
    value = plan(exposure, source).model_copy(update={"assignments": ()})

    class Forbidden(fixtures._Runtime):
        def ask(self, text):
            pytest.fail("An unbound exposure split reached the model")

    monkeypatch.setattr(fixtures, "_Runtime", Forbidden)
    with pytest.raises(runner.RefusedRun, match="exposure split"):
        run_board(tmp_path, monkeypatch, exposure, source, value)
    assert not (tmp_path / "boards" / "run").exists()


@pytest.mark.parametrize("attack", ["result-index", "delivery-text", "receipt-path", "run-binding", "duplicate-receipt", "disabled-query"])
def test_self_consistent_outer_hash_does_not_hide_bad_exposure_events(tmp_path, monkeypatch, exposure, source, attack):
    path, kwargs = run_board(tmp_path, monkeypatch, exposure, source, plan(exposure, source))
    board_path = path / "scoreboard.json"
    board = json.loads(board_path.read_text())
    row_dir = path / board["rows"][0]["run_dir"]
    journal = row_dir / "exposure.jsonl"
    events = [json.loads(line) for line in journal.read_text().splitlines()]
    if attack == "result-index":
        next(e for e in events if e["kind"] == "result")["payload"]["result"]["index_digest"] = "f" * 64
    elif attack == "delivery-text":
        next(e for e in events if e["kind"] == "delivery")["payload"]["text"] = "A different result"
    elif attack == "receipt-path":
        next(e for e in events if e["kind"] == "received")["payload"]["receipt"]["path"] = "../outside.txt"
    elif attack == "run-binding":
        events[-1]["payload"]["artifacts"].pop("trajectory.json")
    elif attack == "disabled-query":
        board["condition"]["exposure"]["enabled"] = False
        events[0]["payload"]["plan"]["enabled"] = False
        events[0]["payload"]["plan_digest"] = exposure.ExposurePlan.model_validate(events[0]["payload"]["plan"]).digest
    else:
        events.insert(-1, next(e.copy() for e in events if e["kind"] == "received"))
    previous = None
    for sequence, event in enumerate(events):
        event.update(sequence=sequence, previous=previous)
        event["digest"] = json_digest({key: value for key, value in event.items() if key != "digest"})
        previous = event["digest"]
    journal.write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")
    board["rows"][0]["exposure_sha256"] = sha256(journal.read_bytes()).hexdigest()
    board_path.write_text(json.dumps(board), encoding="utf-8")
    audit = exposure.read_exposure(row_dir, plan=exposure.ExposurePlan.model_validate(board["condition"]["exposure"]),
        problem_id=board["rows"][0]["id"], repeat=board["rows"][0]["repeat"],
        expected_digest=board["rows"][0]["exposure_sha256"], run_procedure_digest=board["condition"]["run_procedure_digest"])
    assert audit["issues"]
    assert any("exposure" in issue for issue in scoreboard.scoreboard_self_issues(path, **kwargs))


def test_unknown_assignment_is_not_inferred_heldout_from_family_absence(tmp_path, monkeypatch, exposure, source):
    value = plan(exposure, source)
    value = value.model_copy(update={"assignments": tuple(a.model_copy(update={"intended": "unknown"}) for a in value.assignments)})
    path, kwargs = run_board(tmp_path, monkeypatch, exposure, source, value)
    result = compare.compare(path, path, varying=(), **kwargs)
    assert all(row["exposure"]["cohort"] == "unknown" for row in result["rows"]["left"])
