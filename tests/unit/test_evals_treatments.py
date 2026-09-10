"""Treatment identities and scripted comparisons test controls, never performance."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

import pytest
from test_evals_run_command import _config, _minimal_corpus_and_baseline, _parse

from hardy.app import evals as commands
from hardy.evals.identity import run_procedure_digest_of


def test_strategy_and_history_change_the_pooling_key():
    base = dict(model="fixture", mode="staged", limits={"official_checks": 3}, repeats=1)
    default = run_procedure_digest_of(**base)
    assert default == run_procedure_digest_of(**base, strategy="iterative", history_mode="full")
    frontier = run_procedure_digest_of(**base, strategy="best-first", history_mode="full")
    compact = run_procedure_digest_of(**base, strategy="best-first", history_mode="compact")
    replay = run_procedure_digest_of(**base, strategy="best-first", history_mode="replay-full")
    assert len({default, frontier, compact, replay}) == 4
    assert default != run_procedure_digest_of(**base, reviewer_model="different-reader")


def test_canonical_template_changes_the_pooling_key(monkeypatch):
    from hardy import prompts

    base = dict(model="fixture", mode="staged", limits={"official_checks": 3}, repeats=1)
    before = run_procedure_digest_of(**base)
    original = prompts.canonical_prompt
    monkeypatch.setattr(prompts, "canonical_prompt", lambda *args: original(*args) + "changed reader instruction")
    assert run_procedure_digest_of(**base) != before


@pytest.mark.parametrize("verb", ["run", "todo"])
@pytest.mark.parametrize("flags", [
    ["--strategy", "best-first"],
    ["--history-mode", "full"],
    ["--mode", "staged", "--strategy", "iterative", "--history-mode", "compact"],
])
def test_inapplicable_treatment_refused_before_environment(tmp_path, monkeypatch, capsys, verb, flags):
    problems, baseline = _minimal_corpus_and_baseline(tmp_path)
    args = _parse("evals", verb, *(["--label", "x"] if verb == "run" else []), *flags)
    args.problems, args.baseline = problems, baseline
    args.acknowledge_unsafe_execution = True
    monkeypatch.setattr(commands, "_identity", lambda _: pytest.fail("no environment work"))
    assert commands.main(args, _config()) == 2
    assert "Refused:" in capsys.readouterr().err


def test_todo_and_run_use_identical_treatment_keys(tmp_path, monkeypatch, capsys):
    from test_evals_run_command import IDENTITY

    from hardy.evals import staged
    from hardy.formal import lean

    problems, baseline = _minimal_corpus_and_baseline(tmp_path)
    captured = {}
    monkeypatch.setattr(commands, "_identity", lambda _: IDENTITY)
    monkeypatch.setattr(lean, "environment_identity", lambda *_, **__: IDENTITY)
    monkeypatch.setattr(commands, "run_set", lambda **kw: captured.update(kw) or tmp_path / "board")
    monkeypatch.setattr(staged, "staged_runner", lambda config, **kw: captured.update(treatment=kw))
    flags = ["--mode", "staged", "--strategy", "best-first", "--history-mode", "compact"]
    todo = _parse("evals", "todo", *flags)
    run = _parse("evals", "run", "--label", "x", "--only", "a", "--acknowledge-unsafe-execution", *flags)
    run.model = None
    for args in (todo, run):
        args.problems, args.baseline, args.scoreboards = problems, baseline, tmp_path / "boards"
    config = _config(faithfulness_model="configured-reader")
    assert commands.main(todo, config) == 0
    predicted = json.loads(capsys.readouterr().out)["pooling_key"]["run_procedure_digest"]
    assert commands.main(run, config) == 0
    condition = captured["condition"]
    assert condition.run_procedure_digest == predicted
    assert (condition.strategy, condition.history_mode) == ("best-first", "compact")
    assert condition.reviewer_model == "configured-reader"
    assert len(condition.canonical_template_sha256) == 64
    assert captured["treatment"] == {"backend": "claude", "strategy": "best-first", "history_mode": "compact"}


def _scripted_staged_run(row_dir, *, strategy, history_mode):
    """Run real workflow/strategy/verifier owners with scripted provider/Lean IO.

    Nothing here invokes Lean or a provider. The recorded process responses
    exercise artifact authentication, not mathematical or performance evidence.
    """
    from test_workflow import Terminal, _scripted_controller

    from hardy.agents.usage import Usage
    from hardy.formal.verifier import FinalVerifier
    from hardy.foundation.process import ProcessResult
    from hardy.workflows.contracts import ProofSubmission, RunLimits, RunPhase

    workflow, _, controller, state = _scripted_controller(
        row_dir, limits=RunLimits(official_checks=3), document_status="tex_failed",
    )
    original = controller._runtime_factory

    class Runtime:
        backend = "claude"

        def __init__(self, store):
            self.inner, self.store, self.spend = original(store), store, Usage()
            self.attempts, self.sessions = 0, 0

        @property
        def usage(self):
            return self.spend.summary()

        def bind_proof_budget(self, budget):
            self.budget = budget

        def start(self, **kw):
            self.sessions += 1
            thread = self.inner.start(**kw)
            thread.session = f"scripted-{self.sessions}"
            return thread

        def cancel(self, thread):
            self.inner.cancel(thread)

        def _record(self, thread, stage, answer):
            phase = {"formalization": RunPhase.FORMALIZING, "faithfulness": RunPhase.AWAITING_APPROVAL,
                     "writeup": RunPhase.WRITEUP}.get(stage, RunPhase.PROVING)
            self.store.append("claude.assistant", {"type": "assistant", "message": {
                "content": answer.model_dump_json(), "role": "assistant"}}, phase=phase)
            event = {"type": "result", "session_id": thread.session, "cost_usd": .01,
                     "usage": {"input_tokens": 10, "output_tokens": 5}}
            self.spend = self.spend.record(event)
            self.store.append("claude.result", event, phase=phase)
            return answer

        def _proof(self):
            self.attempts += 1
            return ProofSubmission(proof_body="by exact True.intro" if self.attempts == 1 else "by rfl",
                                   informal_proof="Scripted attempt.")

        def run_proof(self, thread, prompt):
            state.prompts.append(("proof", prompt))
            return self._record(thread, "proof", self._proof())

        def run_structured(self, thread, stage, prompt, output_type):
            if stage == "proof-candidates":
                state.prompts.append((stage, prompt))
                answer = output_type.model_validate({"candidates": [{"submission": self._proof().model_dump(), "priority": 0}]})
            else:
                answer = self.inner.run_structured(thread, stage, prompt, output_type)
            return self._record(thread, stage, answer)

    def lean_response(spec):
        source = Path(spec.argv[-1]).read_text(encoding="utf-8")
        accepted = "by rfl" in source
        # Keep a long failure to exercise real compact-history shortening.
        data = "'two_eq_two' does not depend on any axioms" if accepted else "type mismatch " + "expected equality; " * 60
        return ProcessResult(argv=spec.argv, cwd=spec.cwd, returncode=0 if accepted else 1,
                             stdout=json.dumps({"severity": "information" if accepted else "error", "data": data}),
                             stderr="", timed_out=False, output_overflow=False, duration_ms=1)

    original_document = controller._writeup_builder

    def document(claim, content, grades, verification, identities, store, **kw):
        result = original_document(claim, content, grades, verification, identities, store, **kw)
        # The intentionally failed document remains honestly graded and names
        # its frozen claim; no fake successful compiler result is needed.
        tex = store.write_text(PurePosixPath("writeup/paper.tex"), f"Scripted document for {claim.content_hash}\n")
        return result.model_copy(update={"tex_artifact": tex})

    controller._runtime_factory = Runtime
    controller._verifier = FinalVerifier(lake=Path("scripted-lake"), lean_project=row_dir,
                                         environment=controller._environment,
                                         limits=controller._config.limits, runner=lean_response)
    controller._writeup_builder = document
    manifest = controller.run(workflow.ProveRequest(text="Two equals two.", model="scripted-no-model",
                                                    strategy=strategy, history_mode=history_mode), Terminal())
    run_dir = next(row_dir.glob("*/manifest.json")).parent
    return run_dir, manifest, state


def _paired_staged_boards(tmp_path, treatments):
    from corpus_helpers import write_corpus
    from test_evals_staged import AGREES, _CanonicalRuntime
    from test_workflow import _environment

    from hardy import prompts
    from hardy.corpus.catalog import manifest_digest
    from hardy.corpus.problems import Entry
    from hardy.evals import runner, staged, sweep
    from hardy.evals.contracts import Condition
    from hardy.evals.identity import run_source_digest_of
    from hardy.workflows import contracts, recorded

    entry = Entry(id="scripted", input="Two equals two.", name="two_eq_two", conclusion="2 = 2",
                  expected="true", source="textbook", msc=("11Axx",), difficulty="routine",
                  rationale="scripted accounting fixture", witness=None, witness_note="not a live experiment")
    problems = write_corpus(tmp_path / "corpus", (entry,))
    environment = _environment(contracts)
    host = sweep.host_info()
    baseline = sweep.Baseline(
        created_at=datetime(2026, 9, 10, tzinfo=UTC), problems_sha256=manifest_digest(problems), environment=environment,
        environment_digest=sweep.environment_digest_of(environment, host), procedure_digest=sweep.procedure_digest_of(600.0),
        statement_digests={entry.id: entry.statement_digest()}, heartbeat_budget=200000, wall_backstop_seconds=600,
        singles=sweep.SINGLES, chains=sweep.CHAINS, host=host, problems=(),
        entries={entry.id: sweep.EntryBaseline(tier=3, elaborates=False, attempts={}, closed_by=())},
    )
    baseline_path = tmp_path / "baseline.json"
    baseline_path.write_text(baseline.model_dump_json(), encoding="utf-8")
    boards, states = [], []
    for index, (strategy, history_mode) in enumerate(treatments):
        def run_one(entry, row_dir, model, strategy=strategy, history_mode=history_mode):
            run_dir, _, state = _scripted_staged_run(row_dir, strategy=strategy, history_mode=history_mode)
            assert recorded.validate_recorded_run(run_dir) == ()
            staged.compare_canonical(entry, run_dir, row_dir,
                                     runtime_factory=lambda store: _CanonicalRuntime(AGREES, store),
                                     model=model, wall_seconds=10)
            states.append((run_dir, state))
        limits = {"active_seconds": 1800, "proof_seconds": 1200, "official_checks": 3}
        condition = Condition(
            model="scripted-no-model", backend="claude", mode="staged", limits=limits,
            strategy=strategy, history_mode=history_mode, repeats=1,
            reviewer_model="scripted-no-model",
            selection={"only": [entry.id], "tiers": None, "twins": False},
            staged_prompt_set_sha256=prompts.PROMPT_SET_SHA256, batch_prompt_set_sha256=prompts.BATCH_PROMPT_SET_SHA256,
            hardy_version="0.1.0", source_revision="scripted-contemporaneous", source_sha256=run_source_digest_of(),
            run_procedure_digest=run_procedure_digest_of(model="scripted-no-model", mode="staged", limits=limits,
                                                       repeats=1, strategy=strategy, history_mode=history_mode),
        )
        boards.append(runner.run_set(label=f"arm-{index}", problems_path=problems, baseline_path=baseline_path,
                                    scoreboards_root=tmp_path / "boards", condition=condition, environment=environment,
                                    batch_runner=lambda *_: pytest.fail("no batch rows"), staged_runner=run_one,
                                    now=lambda: datetime(2026, 9, 10, tzinfo=UTC), report=lambda _: None))
    return boards, states, {"problems_path": problems, "baseline_path": baseline_path}


@pytest.mark.parametrize(("treatments", "varying"), [
    ((("iterative", "full"), ("best-first", "full")), "strategy"),
    ((("best-first", "replay-full"), ("best-first", "compact")), "history_mode"),
])
def test_contemporaneous_scripted_staged_arms_authenticate_and_compare(tmp_path, treatments, varying):
    from hardy.evals.compare import compare

    boards, states, kwargs = _paired_staged_boards(tmp_path, treatments)
    result = compare(*boards, varying=(varying,), **kwargs)
    assert result["comparability"]["differences"] == [varying]
    assert result["comparability"]["recorded_controls_match"]
    assert result["comparability"]["unknown"] == []
    assert all(side["audit_issues"] == [] for side in result["sides"].values())
    pair = result["pairs"][0]
    assert pair["left"]["outcome"] == pair["right"]["outcome"] == "solved"
    assert pair["left"]["independent_verifier_calls"] == pair["right"]["independent_verifier_calls"] == 2
    assert pair["left"]["lean_checks"] == pair["right"]["lean_checks"] == 0
    claims = [json.loads((run_dir / "formalization.json").read_text())["content_hash"] for run_dir, _ in states]
    assert claims[0] == claims[1]
    if varying == "history_mode":
        assert all(len([claim for claim in state.starts if claim is not None]) == 2 for _, state in states)
        for (run_dir, _), (_, history) in zip(states, treatments, strict=True):
            replays = [json.loads(line)["payload"] for line in (run_dir / "trajectory.jsonl").read_text().splitlines()
                       if json.loads(line)["kind"] == "strategy.history_replay"]
            assert len(replays) == 2
            assert replays[-1]["mode"] == ("full" if history == "replay-full" else "compact")


def test_native_history_vs_compact_also_changes_context_policy(tmp_path):
    from hardy.evals.compare import compare

    boards, _, kwargs = _paired_staged_boards(tmp_path, (("best-first", "full"), ("best-first", "compact")))
    result = compare(*boards, varying=("history_mode",), **kwargs)
    assert result["comparability"]["unintended_differences"] == ["context_policy"]
    assert not result["comparability"]["recorded_controls_match"]
    assert result["comparability"]["fields"]["shared_tool_budget"]["left"] is True


@pytest.mark.parametrize("mutation", ["relabel_strategy", "unbound_strategy_file"])
def test_treatment_requires_manifest_and_matching_strategy_event(tmp_path, mutation):
    from hardy.evals.scoreboard import scoreboard_self_issues

    boards, states, kwargs = _paired_staged_boards(tmp_path, (("iterative", "full"), ("best-first", "full")))
    if mutation == "relabel_strategy":
        path = boards[1] / "scoreboard.json"
        payload = json.loads(path.read_text())
        payload["condition"]["strategy"] = "iterative"
    else:
        path = states[1][0] / "manifest.json"
        payload = json.loads(path.read_text())
        del payload["artifacts"]["strategy.json"]
    path.write_text(json.dumps(payload), encoding="utf-8")
    issues = scoreboard_self_issues(boards[1], **kwargs)
    assert any("strategy" in issue for issue in issues), issues


def test_canonical_reader_change_cannot_hide_behind_history_treatment(tmp_path, monkeypatch):
    from hardy.evals import staged
    from hardy.evals.compare import compare

    original, calls = staged.compare_canonical, []

    def changed_reader(*args, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            kwargs["model"] = "different-reader"
        return original(*args, **kwargs)

    monkeypatch.setattr(staged, "compare_canonical", changed_reader)
    boards, _, kwargs = _paired_staged_boards(tmp_path, (("best-first", "replay-full"), ("best-first", "compact")))
    result = compare(*boards, varying=("history_mode",), **kwargs)
    assert not result["comparability"]["recorded_controls_match"]
    assert any("reviewer_model" in issue for issue in result["sides"]["right"]["audit_issues"])
