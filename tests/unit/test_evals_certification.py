"""Scripted workflow receipts test certification mechanics, not performance."""
from __future__ import annotations

import importlib
import json

import pytest
from test_evals_treatments import _paired_staged_boards


@pytest.fixture
def certification():
    return importlib.import_module("hardy.evals.certification")


def run_certified(tmp_path, monkeypatch, module, *, repeats=2, stop_at=None, budget=None, reused_ids=False, workers=1, unsolved=False):
    from uuid import uuid4

    import test_workflow

    from hardy.evals import runner
    from hardy.formal.verifier import FinalVerifier

    if unsolved:
        initialize = FinalVerifier.__init__
        def fail_verification(self, *args, **kwargs):
            process = kwargs["runner"]
            def failed(spec):
                return process(spec).model_copy(update={"returncode": 1,
                    "stdout": json.dumps({"severity": "error", "data": "scripted proof rejected"})})
            initialize(self, *args, **{**kwargs, "runner": failed})
        monkeypatch.setattr(FinalVerifier, "__init__", fail_verification)
    controller = test_workflow._scripted_controller
    def distinct(*args, **kwargs):
        result = controller(*args, **kwargs)
        if not reused_ids:
            result[2]._uuid_factory = uuid4
        return result
    monkeypatch.setattr(test_workflow, "_scripted_controller", distinct)
    original = runner.run_set
    def run(**kwargs):
        kwargs["condition"] = kwargs["condition"].model_copy(update={"repeats": repeats})
        kwargs["certification"] = budget or module.CertificationBudget(independent_verifier_calls=3, ks=(1, 2))
        kwargs["workers"] = workers
        executor = kwargs["staged_runner"]
        calls = 0
        def observed(*args):
            nonlocal calls
            calls += 1
            parent = args[1].parents[2]
            assert (parent / module.DECLARATION).exists()
            assert "start_attempt" in (parent / module.JOURNAL).read_text()
            if int(args[1].name.rsplit("-", 1)[1]) + 1 == stop_at:
                raise RuntimeError("scripted interrupted attempt")
            executor(*args)
        kwargs["staged_runner"] = observed
        return original(**kwargs)
    monkeypatch.setattr(runner, "run_set", run)
    return _paired_staged_boards(tmp_path, (("best-first", "full"),))


def test_prospective_fixed_verifier_budget_certifies_actual_staged_artifacts(tmp_path, monkeypatch, certification):
    boards, _, inputs = run_certified(tmp_path, monkeypatch, certification)
    result = certification.certify(boards[0], **inputs)
    assert result["issues"] == []
    assert result["prospective"]
    assert result["statistics"]["2"]["certified_value"] == 1
    assert result["statistics"]["2"]["problems"] == 1
    assert len(result["attempts"]) == 2
    assert all(a["independent_verifier_calls"] == 2 and a["eligible"] for a in result["attempts"])
    assert result["measurements"]["lean_cpu_seconds"] is None
    assert result["measurements"]["scheduler_makespan_seconds"] > 0
    assert 0 < result["measurements"]["scheduler_worker_utilization"] <= 1
    assert result["domains"]["11"]["2"]["certified_value"] == 1
    assert result["family_counts"] == {"unknown": 1}


def test_authentic_completed_unsolved_attempts_certify_zero(tmp_path, monkeypatch, certification):
    boards, _, inputs = run_certified(tmp_path, monkeypatch, certification, unsolved=True)
    report = certification.certify(boards[0], **inputs)
    assert report["issues"] == []
    assert report["statistics"]["2"]["certified_value"] == 0
    # Best-first stops after exhausting its two distinct proposals; the cap
    # is three, but duplicate candidates do not spend a third verifier call.
    assert all(a["outcome"] == "unsolved" and a["eligible"] and a["independent_verifier_calls"] == 2 for a in report["attempts"])
    assert report["measurements"]["proof_usage"]["cost_usd"]["value"] > 0


def test_concurrent_middle_failure_retains_later_completed_attempt(tmp_path, monkeypatch, certification):
    with pytest.raises(RuntimeError):
        run_certified(tmp_path, monkeypatch, certification, repeats=3, workers=3, stop_at=2,
            budget=certification.CertificationBudget(independent_verifier_calls=3, ks=(1, 2, 3)))
    board = tmp_path / "boards/arm-0"
    assert len(json.loads((board / "scoreboard.json").read_text())["rows"]) == 1
    report = certification.certify(board, problems_path=tmp_path / "corpus", baseline_path=tmp_path / "baseline.json")
    assert [a["execution"] for a in report["attempts"]] == ["complete", "failed", "complete"]
    assert report["attempts"][2]["eligible"]
    assert report["statistics"]["1"]["certified_value"] == 1
    assert report["statistics"]["2"]["certified_value"] is None
    assert report["statistics"]["3"]["certified_value"] is None
    assert report["measurements"]["proof_usage"]["cost_usd"]["complete_rows"] == 2


def test_reused_attempt_identity_is_not_two_independent_attempts(tmp_path, monkeypatch, certification):
    boards, _, inputs = run_certified(tmp_path, monkeypatch, certification, reused_ids=True)
    report = certification.certify(boards[0], **inputs)
    assert report["statistics"]["2"]["certified_value"] is None
    assert all("attempt identity is reused" in a["provisional_reasons"] for a in report["attempts"])


def test_copied_old_run_cannot_acquire_a_prospective_certificate(tmp_path, monkeypatch, certification):
    import shutil

    from hardy.evals import runner

    old_boards, _, _ = _paired_staged_boards(tmp_path / "old", (("best-first", "full"),))
    old = old_boards[0] / "runs/scripted/staged-0"
    original = runner.run_set
    def copying(**kwargs):
        kwargs["certification"] = certification.CertificationBudget(independent_verifier_calls=3)
        kwargs["staged_runner"] = lambda entry, directory, model: shutil.copytree(old, directory, dirs_exist_ok=True)
        return original(**kwargs)
    monkeypatch.setattr(runner, "run_set", copying)
    boards, _, inputs = _paired_staged_boards(tmp_path / "new", (("best-first", "full"),))
    report = certification.certify(boards[0], **inputs)
    assert report["attempts"][0]["authenticated"]
    assert report["statistics"]["1"]["certified_value"] is None
    assert "run lacks its authenticated prospective attempt context" in report["attempts"][0]["provisional_reasons"]


def test_context_does_not_leak_to_later_ordinary_runs(tmp_path, monkeypatch, certification):
    from hardy.workflows.attempt_context import current_attempt

    run_certified(tmp_path, monkeypatch, certification)
    assert current_attempt() is None


def test_read_only_report_preserves_all_artifacts(tmp_path, monkeypatch, certification):
    boards, _, inputs = run_certified(tmp_path, monkeypatch, certification)
    board = boards[0]
    before = {p.relative_to(board): p.read_bytes() for p in board.rglob("*") if p.is_file()}
    certification.certify(board, **inputs)
    assert {p.relative_to(board): p.read_bytes() for p in board.rglob("*") if p.is_file()} == before


@pytest.mark.parametrize("ks", [(True,), (0,), (1, 1), ()])
def test_invalid_attempt_counts_are_refused(certification, ks):
    with pytest.raises(ValueError):
        certification.CertificationBudget(independent_verifier_calls=3, ks=ks)


def test_declared_cap_must_equal_the_actual_manifest_cap(tmp_path, monkeypatch, certification):
    budget = certification.CertificationBudget(independent_verifier_calls=4, ks=(1, 2))
    boards, _, inputs = run_certified(tmp_path, monkeypatch, certification, budget=budget)
    report = certification.certify(boards[0], **inputs)
    assert report["statistics"]["2"]["certified_value"] is None
    assert any("budget lacks matching" in reason for reason in report["attempts"][0]["provisional_reasons"])


def test_changed_artifact_seal_blocks_scheduler_measurements(tmp_path, monkeypatch, certification):
    boards, states, inputs = run_certified(tmp_path, monkeypatch, certification)
    # A non-authoritative extra artifact still invalidates the sealed timing
    # association; its run's independently checked mathematical outcome survives.
    (states[0][0] / "after-run.txt").write_text("added later")
    report = certification.certify(boards[0], **inputs)
    assert report["attempts"][0]["authenticated"]
    assert not report["attempts"][0]["eligible"]
    assert report["measurements"]["scheduler_worker_utilization"] is None


def test_first_k_order_and_all_attempt_cost_are_observed_not_iid(tmp_path, certification):
    from datetime import UTC, datetime

    from test_evals_runner import GIVE_UP, IDENTITY, SOLVE, _batch_runner, _condition, _files

    from hardy.evals import runner

    problems, baseline = _files(tmp_path)
    scripts = iter((GIVE_UP, SOLVE))
    def execute(entry, directory, turns, seconds):
        _batch_runner({entry.id: next(scripts)})(entry, directory, turns, seconds)
    board = runner.run_set(label="ordered", problems_path=problems, baseline_path=baseline,
        scoreboards_root=tmp_path / "boards", condition=_condition(repeats=2,
            selection={"only": ["t"], "tiers": None, "twins": False}), environment=IDENTITY,
        batch_runner=execute, now=lambda: datetime(2026, 9, 10, tzinfo=UTC), report=lambda _: None,
        certification=certification.CertificationBudget(independent_verifier_calls=3, ks=(1, 2)))
    report = certification.certify(board, problems_path=problems, baseline_path=baseline)
    assert report["issues"] == []
    assert report["statistics"]["1"]["observed_lower"] == 0
    assert report["statistics"]["2"]["observed_lower"] == 1
    assert report["statistics"]["2"]["certified_value"] is None
    assert report["measurements"]["proof_usage"]["cost_usd"]["value"] == pytest.approx(.2)
    assert all(a["sealed"] for a in report["attempts"])


def test_actual_api_budget_receipt_keeps_derived_cost_separate_and_hard_cap_provisional(tmp_path, monkeypatch, certification):
    import sys
    from datetime import UTC, datetime

    from test_evals_runner import FAKE_LEAN, IDENTITY, RAW_IDENTITY, _condition, _files
    from test_spend_budget import Client, usage

    from hardy.agents.api import AnthropicProvider
    from hardy.agents.spend_budget import SpendPolicy
    from hardy.app.wiring import runtime_factory
    from hardy.evals import runner
    from hardy.formal.contracts import Request
    from hardy.formal.lean import LeanTools
    from hardy.workflows import batch

    problems, baseline = _files(tmp_path)
    policy = SpendPolicy(id="scripted", models=("fixture",), token_limit=100000, cost_limit_usd="1",
        tariff={"id": "scripted tariff", "input_per_million": "2", "output_per_million": "2",
                "cache_read_per_million": "2", "cache_write_per_million": "2"})
    client = Client([usage()])
    monkeypatch.setattr(AnthropicProvider, "client", lambda _: client)
    def execute(entry, directory, turns, seconds):
        request = Request(entry.declaration(), entry.input, entry.imports)
        batch.run(request, runtime_factory("fixture", "api", spend_policy=policy),
            LeanTools(request, (sys.executable, str(FAKE_LEAN))), directory,
            max_turns=turns, wall_seconds=seconds, toolchain=RAW_IDENTITY)
    board = runner.run_set(label="api", problems_path=problems, baseline_path=baseline,
        scoreboards_root=tmp_path / "boards", condition=_condition(model="fixture", backend="anthropic-api",
            selection={"only": ["t"], "tiers": None, "twins": False}), environment=IDENTITY,
        batch_runner=execute, now=lambda: datetime(2026, 9, 10, tzinfo=UTC), report=lambda _: None,
        certification=certification.CertificationBudget(independent_verifier_calls=3, hard_cost_usd="1"))
    report = certification.certify(board, problems_path=problems, baseline_path=baseline)
    assert report["issues"] == []
    assert report["measurements"]["derived_tariff_cost_usd"] == {"value": "0.000004", "complete_rows": 1, "missing_rows": 0}
    assert report["measurements"]["proof_usage"]["cost_usd"]["value"] is None
    assert report["statistics"]["1"]["certified_value"] is None
    assert "hard_cost_usd: existing receipts do not establish a hard provider cap" in report["attempts"][0]["provisional_reasons"]


def test_missing_baseline_entry_refuses_changed_inputs_without_reducing_denominator(tmp_path, monkeypatch, certification):
    boards, _, inputs = run_certified(tmp_path, monkeypatch, certification)
    path = inputs["baseline_path"]
    value = json.loads(path.read_text())
    del value["entries"]["scripted"]
    path.write_text(json.dumps(value))
    with pytest.raises(certification.CertificationInputsChanged, match="denominator"):
        certification.certify(boards[0], **inputs)


@pytest.mark.parametrize("field", ["start", "finish", "context"])
def test_finish_payload_cannot_overwrite_scheduler_or_context_state(tmp_path, monkeypatch, certification, field):
    from hardy.foundation.values import json_digest

    boards, _, inputs = run_certified(tmp_path, monkeypatch, certification)
    path = boards[0] / certification.JOURNAL
    events = [json.loads(line) for line in path.read_text().splitlines()]
    next(e for e in events if e["kind"] == "finish_attempt")["payload"][field] = 0
    previous = None
    for event in events:
        event["previous"] = previous
        event["digest"] = json_digest({key: value for key, value in event.items() if key != "digest"})
        previous = event["digest"]
    path.write_text("".join(json.dumps(event) + "\n" for event in events))
    report = certification.certify(boards[0], **inputs)
    assert report["issues"]
    assert report["statistics"]["2"]["certified_value"] is None
    assert report["measurements"]["scheduler_worker_utilization"] is None


@pytest.mark.parametrize("malformed", [[], None, "invalid"])
def test_malformed_declaration_is_provisional_not_a_reader_crash(tmp_path, monkeypatch, certification, malformed):
    boards, _, inputs = run_certified(tmp_path, monkeypatch, certification)
    (boards[0] / certification.DECLARATION).write_text(json.dumps(malformed))
    report = certification.certify(boards[0], **inputs)
    assert report["issues"] and report["statistics"]["1"]["certified_value"] is None


def test_changed_declared_universe_cannot_drop_missing_slots(tmp_path, monkeypatch, certification):
    with pytest.raises(RuntimeError):
        run_certified(tmp_path, monkeypatch, certification, repeats=3, stop_at=2)
    board = tmp_path / "boards/arm-0"
    path = board / certification.DECLARATION
    plan = json.loads(path.read_text())
    plan["slots"] = plan["slots"][:1]
    path.write_text(json.dumps(plan))
    report = certification.certify(board, problems_path=tmp_path / "corpus", baseline_path=tmp_path / "baseline.json")
    assert report["issues"] and len(report["attempts"]) == 3
    assert report["statistics"]["1"]["certified_value"] is None


def test_retrospective_rows_and_hard_cost_declarations_remain_provisional(tmp_path, monkeypatch, certification):
    boards, _, inputs = _paired_staged_boards(tmp_path / "legacy", (("best-first", "full"),))
    report = certification.certify(boards[0], **inputs)
    assert not report["prospective"] and report["statistics"]["1"]["certified_value"] is None
    budget = certification.CertificationBudget(independent_verifier_calls=3, hard_cost_usd="1", ks=(1, 2))
    boards, _, inputs = run_certified(tmp_path / "cost", monkeypatch, certification, budget=budget)
    report = certification.certify(boards[0], **inputs)
    assert report["statistics"]["2"]["certified_value"] is None
    assert any("hard_cost" in issue for issue in report["attempts"][0]["provisional_reasons"])


def test_interruption_keeps_every_declared_slot_in_denominator(tmp_path, monkeypatch, certification):
    with pytest.raises(RuntimeError, match="interrupted"):
        run_certified(tmp_path, monkeypatch, certification, repeats=3, stop_at=2)
    board = tmp_path / "boards/arm-0"
    report = certification.certify(board, problems_path=tmp_path / "corpus", baseline_path=tmp_path / "baseline.json")
    assert [a["execution"] for a in report["attempts"]] == ["complete", "failed", "not_started"]
    assert report["statistics"]["2"]["problems"] == 1
    assert report["statistics"]["2"]["certified_value"] is None
    assert report["statistics"]["1"]["certified_value"] == 1
    assert report["measurements"]["scheduler_worker_utilization"] is None


@pytest.mark.parametrize("target", ["declaration", "journal", "artifact"])
def test_tampered_receipts_or_kernel_artifacts_never_certify(tmp_path, monkeypatch, certification, target):
    boards, states, inputs = run_certified(tmp_path, monkeypatch, certification)
    board = boards[0]
    if target == "declaration":
        path = board / certification.DECLARATION
        value = json.loads(path.read_text())
        value["budget"]["independent_verifier_calls"] = 99
        path.write_text(json.dumps(value))
    elif target == "journal":
        path = board / certification.JOURNAL
        path.write_bytes(path.read_bytes()[:-3])
    else:
        path = states[0][0] / "manifest.json"
        value = json.loads(path.read_text())
        value["claim_sha256"] = "f" * 64
        path.write_text(json.dumps(value))
    report = certification.certify(board, **inputs)
    assert report["issues"]
    assert report["statistics"]["2"]["certified_value"] is None
