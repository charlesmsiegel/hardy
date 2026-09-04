import dataclasses
import importlib
import json
from datetime import UTC, datetime
from pathlib import PurePosixPath
from types import SimpleNamespace
from uuid import UUID

NOW = datetime(2026, 7, 24, tzinfo=UTC)
RUN_ID = UUID('12345678-1234-5678-1234-567812345678')


def _config(config_module, domain, runs_root, limits=None):
    """Hardy's resolved settings, with only the fields a run varies."""
    return config_module.Config(
        model='test-model',
        lean_command=('lake', 'env', 'lean'),
        lean_project=None,
        lean_timeout=30.0,
        latex_command=('tectonic',),
        root=runs_root,
        project='workspace',
        runs_root=runs_root,
        limits=limits or domain.RunLimits(),
    )


def _proposal(domain, proposition='2 = 2'):
    return domain.FormalizationProposal(
        restatement='Two equals two.',
        domains=(),
        quantifiers=(),
        assumptions=(),
        interpretation_choices=(),
        theorem_name='two_eq_two',
        binders='',
        proposition=proposition,
    )



def _review(domain, agrees=True, divergences=(), notes=''):
    """What the independent faithfulness reader answers.

    `notes` defaults to empty because an agreement is silent: a reservation
    written there rather than listed as a divergence is read as a refusal, so
    a fixture that chatted while agreeing would never agree.
    """
    return domain.FaithfulnessReview(
        formalization_entails_claim=agrees,
        claim_entails_formalization=True,
        divergences=tuple(divergences),
        notes=notes,
    )

def _environment(domain):
    return domain.EnvironmentIdentity(
        lean_version='4.32.0',
        lean_commit='8c9756b',
        mathlib_revision='81a5d257',
        lake_manifest_sha256='b' * 64,
        imports=('Mathlib',),
    )


def _accept(verifier_module, domain, store, claim, source):
    """Record an acceptance the way the real verifier does — digest derived."""
    identity = store.write_text(PurePosixPath('lean/Main.lean'), source)
    evidence = domain.VerificationEvidence(
        claim_sha256=claim.content_hash,
        source_sha256=identity.sha256,
        axioms=(),
        toolchain=claim.environment,
    )
    result = verifier_module.VerificationResult(
        verified=True,
        reason=None,
        axioms=(),
        diagnostics=(),
        source_sha256=identity.sha256,
        verification_sha256=evidence.digest,
        evidence=evidence,
    )
    store.write_json(PurePosixPath('lean/verification.json'), result)
    return result


def _lean_result(domain, lean, process, success=True):
    child = process.ProcessResult(
        argv=('lake', 'env', 'lean'),
        cwd='.',
        returncode=0 if success else 1,
        stdout='',
        stderr='',
        timed_out=False,
        output_overflow=False,
        duration_ms=1,
    )
    return lean.LeanCheckResult(
        success=success,
        diagnostics=(),
        open_goals=(),
        process=child,
        source_sha256='s' * 64,
        toolchain=_environment(domain),
    )


class Terminal:
    def __init__(self, decisions=('approve',), revisions=(), acknowledge=True):
        self.decisions = list(decisions)
        self.revisions = list(revisions)
        self.acknowledge = acknowledge
        self.shown = []
        self.verdicts = []
        self.result = None

    def show_formalization(self, proposal, elaboration):
        self.shown.append((proposal, elaboration))

    def choose_approval(self):
        return self.decisions.pop(0)

    def revision_text(self):
        return self.revisions.pop(0)

    def show_faithfulness(self, verdict):
        self.verdicts.append(verdict)

    def acknowledge_unsafe_execution(self):
        return self.acknowledge

    def show_result(self, manifest):
        self.result = manifest


def _scripted_controller(
    tmp_path,
    *,
    proposals=None,
    elaborations=None,
    proof_results=None,
    healthy=True,
    authenticated=True,
    document_status='tex_compiled',
    runtime_error=False,
    limits=None,
    monotonic=None,
    interrupt_stage=None,
    cancel_stage=None,
    cancel_quietly_at=None,
    reviews=None,
):
    config_module = importlib.import_module('hardy.config')
    codex_runtime = importlib.import_module('hardy.codex_runtime')
    domain = importlib.import_module('hardy.domain')
    lean = importlib.import_module('hardy.lean')
    process = importlib.import_module('hardy.process')
    verifier_module = importlib.import_module('hardy.verifier')
    workflow = importlib.import_module('hardy.workflow')
    writeup = importlib.import_module('hardy.writeup')
    proposals = list(proposals or [_proposal(domain)])
    elaborations = list(elaborations or [True] * len(proposals))
    proof_results = list(proof_results or [True])
    # What the independent faithfulness reader answers, in order. An entry may
    # be an exception to raise, which is how an unreachable reader is scripted.
    reviews = list(reviews or [])
    state = SimpleNamespace(
        starts=[], isolation=[], deadlines=[], handles=[], prompts=[],
        verifier_calls=0, cancelled=None, controller=None
    )

    def _cancel_from_elsewhere(stage):
        """What `/prove`'s Esc does: `cancel()` from another thread, and the
        stage in flight then fails because its client was interrupted.

        Deliberately not `KeyboardInterrupt`: that is the Ctrl+C path, which
        only exists inside the workflow's own thread. The whole point of
        `ProveWorkflow.cancel` is that the terminal cannot raise there.
        """
        if cancel_quietly_at == stage:
            # Cancelled while a stage was between calls: it answers normally,
            # and nothing after it may begin.
            state.controller.cancel()
            return
        if cancel_stage == stage:
            state.controller.cancel()
            raise RuntimeError('the provider client was interrupted')

    class Runtime:
        backend = 'fixture-backend'

        def start(self, *, model, run_dir, claim, isolated=False, phase=None, wall_seconds=None):
            if runtime_error:
                raise RuntimeError('runtime fixture failure')
            state.starts.append(claim)
            state.isolation.append(isolated)
            state.deadlines.append(wall_seconds)
            # Kept so a test can say *which* thread a cancellation reached, not
            # merely that one did: each stage has its own.
            state.handles.append(SimpleNamespace(claim=claim))
            return state.handles[-1]

        def run_structured(self, thread, stage, prompt, output_type):
            state.prompts.append((stage, prompt))
            _cancel_from_elsewhere(stage)
            if stage == interrupt_stage:
                raise KeyboardInterrupt
            if stage == 'faithfulness':
                answer = reviews.pop(0) if reviews else _review(domain)
                if isinstance(answer, Exception):
                    raise answer
                return answer
            if stage == 'formalization':
                return proposals.pop(0) if proposals else _proposal(domain)
            return writeup.WriteupContent(
                title='Fixture writeup',
                theorem_text='Fixture theorem.',
                proof_text='Fixture proof.',
                known_gaps=(),
            )

        def run_proof(self, thread, prompt):
            state.prompts.append(('proof', prompt))
            _cancel_from_elsewhere('proof')
            if interrupt_stage == 'proof':
                raise KeyboardInterrupt
            return codex_runtime.ProofSubmission(
                proof_body='by rfl', informal_proof='Reflexivity.'
            )

        def cancel(self, thread):
            state.cancelled = thread

    class Lean:
        def check_proof(self, claim, proof):
            success = elaborations.pop(0) if elaborations else False
            return _lean_result(domain, lean, process, success)

    class Verifier:
        def verify(self, claim, proof_body, store):
            state.verifier_calls += 1
            success = proof_results.pop(0) if proof_results else False
            if success:
                return _accept(
                    verifier_module,
                    domain,
                    store,
                    claim,
                    'theorem two_eq_two : 2 = 2 :=\nby rfl\n#print axioms two_eq_two\n',
                )
            store.write_text(PurePosixPath('lean/last-attempt.lean'), proof_body)
            result = verifier_module.VerificationResult(
                verified=False,
                reason=domain.TerminalReason.LEAN_ELABORATION_FAILURE,
                axioms=(),
                diagnostics=(),
                source_sha256='f' * 64,
                verification_sha256=None,
            )
            store.write_json(PurePosixPath('lean/verification.json'), result)
            return result

    def build_document(claim, content, grades, verification, identities, store, **kwargs):
        status = domain.DocumentStatus(document_status)
        tex = store.write_text(PurePosixPath('writeup/paper.tex'), status.value)
        pdf = (
            store.write_bytes(PurePosixPath('writeup/paper.pdf'), b'%PDF-fixture')
            if status is domain.DocumentStatus.TEX_COMPILED
            else None
        )
        log = store.write_text(PurePosixPath('writeup/compile.log'), status.value)
        child = process.ProcessResult(
            argv=('tectonic',),
            cwd=store.path,
            returncode=0 if pdf else 1,
            stdout='',
            stderr='',
            timed_out=False,
            output_overflow=False,
            duration_ms=1,
        )
        return writeup.DocumentResult(
            status=status,
            tex_artifact=tex,
            pdf_artifact=pdf,
            log_artifact=log,
            process=child,
        )

    controller = workflow.ProveWorkflow(
        config=_config(config_module, domain, tmp_path, limits),
        environment=_environment(domain),
        doctor=lambda _: SimpleNamespace(
            healthy=healthy, authenticated=authenticated
        ),
        lean=Lean(),
        runtime_factory=lambda store: Runtime(),
        verifier=Verifier(),
        writeup_builder=build_document,
        identities_factory=lambda run_id, model: SimpleNamespace(
            run_id=run_id, model=model
        ),
        now=lambda: NOW,
        monotonic=monotonic or (lambda: 0.0),
        uuid_factory=lambda: RUN_ID,
    )
    state.controller = controller
    return workflow, domain, controller, state


def test_success_requires_approval_repairs_a_failed_candidate_and_finalizes(tmp_path) -> None:
    config_module = importlib.import_module('hardy.config')
    domain = importlib.import_module('hardy.domain')
    lean = importlib.import_module('hardy.lean')
    process = importlib.import_module('hardy.process')
    verifier_module = importlib.import_module('hardy.verifier')
    workflow = importlib.import_module('hardy.workflow')
    writeup = importlib.import_module('hardy.writeup')
    environment = _environment(domain)
    starts = []
    prompts = []

    class Runtime:
        backend = 'fixture-backend'

        def start(self, *, model, run_dir, claim, isolated=False, phase=None, wall_seconds=None):
            starts.append((claim, isolated))
            return object()

        def run_structured(self, thread, stage, prompt, output_type):
            prompts.append((stage, prompt))
            if stage == 'faithfulness':
                return _review(domain)
            if stage == 'formalization':
                return _proposal(domain)
            return writeup.WriteupContent(
                title='Two equals two',
                theorem_text='Two equals two.',
                proof_text='Reflexivity.',
                known_gaps=(),
            )

        def run_proof(self, thread, prompt):
            prompts.append(('proof', prompt))
            proof_count = len([item for item in prompts if item[0] == 'proof'])
            body = 'by exact True.intro' if proof_count == 1 else 'by rfl'
            return importlib.import_module('hardy.codex_runtime').ProofSubmission(
                proof_body=body,
                informal_proof='Reflexivity.',
            )

        def cancel(self, thread):
            pass

    class Verifier:
        def __init__(self):
            self.calls = 0

        def verify(self, claim, proof_body, store):
            self.calls += 1
            if self.calls == 1:
                store.write_text(PurePosixPath('lean/last-attempt.lean'), proof_body)
                return verifier_module.VerificationResult(
                    verified=False,
                    reason=domain.TerminalReason.LEAN_ELABORATION_FAILURE,
                    axioms=(),
                    diagnostics=(),
                    source_sha256='f' * 64,
                    verification_sha256=None,
                )
            return _accept(
                verifier_module,
                domain,
                store,
                claim,
                'theorem two_eq_two : 2 = 2 :=\nby rfl\n#print axioms two_eq_two\n',
            )

    def build_document(claim, content, grades, verification, identities, store, **kwargs):
        tex = store.write_text(PurePosixPath('writeup/paper.tex'), 'verified paper')
        pdf = store.write_bytes(PurePosixPath('writeup/paper.pdf'), b'%PDF-fixture')
        log = store.write_text(PurePosixPath('writeup/compile.log'), 'ok\n')
        child = process.ProcessResult(
            argv=('tectonic',),
            cwd=store.path,
            returncode=0,
            stdout='',
            stderr='',
            timed_out=False,
            output_overflow=False,
            duration_ms=1,
        )
        return writeup.DocumentResult(
            status=domain.DocumentStatus.TEX_COMPILED,
            tex_artifact=tex,
            pdf_artifact=pdf,
            log_artifact=log,
            process=child,
        )

    controller = workflow.ProveWorkflow(
        config=_config(config_module, importlib.import_module('hardy.domain'), tmp_path),
        environment=environment,
        doctor=lambda _: SimpleNamespace(healthy=True),
        lean=SimpleNamespace(
            check_proof=lambda claim, proof: _lean_result(domain, lean, process)
        ),
        runtime_factory=lambda store: Runtime(),
        verifier=Verifier(),
        writeup_builder=build_document,
        identities_factory=lambda run_id, model: SimpleNamespace(
            run_id=run_id, model=model
        ),
        now=lambda: NOW,
        uuid_factory=lambda: RUN_ID,
    )
    terminal = Terminal()

    manifest = controller.run(
        workflow.ProveRequest(
            text='Two equals two.', model='gpt-test', problem_slug='two-equals-two'
        ),
        terminal,
    )

    # Formalizing, then the faithfulness reader, then proving. The first two
    # are started with no claim, so neither is offered Lean tools: the reader
    # is comparing two texts, and one that can elaborate the statement can be
    # persuaded it is fine because Lean accepted it.
    assert [claim for claim, _ in starts][:2] == [None, None]
    # Only the reader is isolated: no tools at all, and no sight of the run
    # directory on a backend whose agent has its own file access.
    assert [isolated for _, isolated in starts] == [False, True, False]
    assert starts[2][0] is not None
    assert starts[2][0].content_hash == manifest.claim_sha256
    assert manifest.phase is domain.RunPhase.COMPLETED
    assert manifest.grades.formal is domain.FormalStatus.KERNEL_VERIFIED
    assert manifest.grades.faithfulness is domain.FaithfulnessStatus.USER_APPROVED
    assert manifest.grades.document is domain.DocumentStatus.TEX_COMPILED
    assert manifest.terminal_reason is None
    assert terminal.result == manifest
    assert any(
        'LEAN_ELABORATION_FAILURE' in prompt
        for stage, prompt in prompts
        if stage == 'proof'
    )
    run_dir = next(tmp_path.iterdir())
    assert (run_dir / 'formalization.json').exists()
    assert (run_dir / 'lean' / 'Main.lean').exists()
    assert (run_dir / 'writeup' / 'paper.pdf').exists()
    saved = domain.RunManifest.model_validate_json(
        (run_dir / 'manifest.json').read_text(encoding='utf-8')
    )
    assert saved == manifest
    events = [json.loads(line) for line in (run_dir / 'trajectory.jsonl').read_text().splitlines()]
    phases = [event['phase'] for event in events if event['kind'] == 'workflow.transition']
    assert phases == [
        'formalizing',
        'awaiting_approval',
        'proving',
        'final_verification',
        'proving',
        'final_verification',
        'writeup',
        'completed',
    ]
    assert events[-1]['kind'] == 'workflow.terminal'


def test_transition_table_is_exact_and_never_skips_user_approval() -> None:
    domain = importlib.import_module('hardy.domain')
    workflow = importlib.import_module('hardy.workflow')

    expected = {
        domain.RunPhase.SETUP: {domain.RunPhase.FORMALIZING},
        domain.RunPhase.FORMALIZING: {domain.RunPhase.AWAITING_APPROVAL},
        domain.RunPhase.AWAITING_APPROVAL: {
            domain.RunPhase.FORMALIZING,
            domain.RunPhase.PROVING,
            domain.RunPhase.CANCELLED,
        },
        domain.RunPhase.PROVING: {domain.RunPhase.FINAL_VERIFICATION},
        domain.RunPhase.FINAL_VERIFICATION: {
            domain.RunPhase.PROVING,
            domain.RunPhase.WRITEUP,
        },
        domain.RunPhase.WRITEUP: {domain.RunPhase.COMPLETED},
    }
    assert expected == workflow.ALLOWED


def test_revision_feedback_is_used_and_cancellation_finalizes(tmp_path) -> None:
    workflow, domain, controller, state = _scripted_controller(
        tmp_path,
        proposals=[_proposal(domain := importlib.import_module('hardy.domain'))] * 2,
    )
    terminal = Terminal(
        decisions=('revise', 'cancel'), revisions=('Use an explicit Nat domain.',)
    )

    manifest = controller.run(
        workflow.ProveRequest(text='Two equals two.', model='gpt-test'), terminal
    )

    assert manifest.phase is domain.RunPhase.CANCELLED
    assert manifest.terminal_reason is domain.TerminalReason.USER_REJECTION
    assert manifest.claim_sha256 is None
    assert 'Use an explicit Nat domain.' in state.prompts[1][1]
    assert len(state.starts) == 1
    assert (next(tmp_path.iterdir()) / 'manifest.json').exists()


def test_ctrl_c_while_formalizing_still_reaches_the_runtime(tmp_path) -> None:
    """Cancellation is the boundary the staged runtime waits on before this
    method hashes the run directory. A phase whose handle the handler never sees
    is a phase whose provider thread is still running while that happens -- and
    the formalizing handle used to be kept in a local of its own.
    """
    workflow, domain, controller, state = _scripted_controller(
        tmp_path, interrupt_stage='formalization'
    )

    manifest = controller.run(
        workflow.ProveRequest(text='Two equals two.', model='gpt-test'), Terminal()
    )

    assert manifest.terminal_reason is domain.TerminalReason.USER_CANCELLATION
    assert state.starts == [None], 'proving never began, so only one thread exists'
    assert state.cancelled is state.handles[0], 'the formalizing thread was never stopped'


def test_invalid_proposals_exhaust_budget_without_starting_proof(tmp_path) -> None:
    domain_module = importlib.import_module('hardy.domain')
    limits = domain_module.RunLimits(formalization_proposals=2)
    workflow, domain, controller, state = _scripted_controller(
        tmp_path,
        proposals=[_proposal(domain_module, 'bad syntax')] * 2,
        elaborations=[False, False],
        limits=limits,
    )

    manifest = controller.run(
        workflow.ProveRequest(text='A claim.', model='gpt-test'), Terminal(decisions=())
    )

    assert manifest.terminal_reason is domain.TerminalReason.MALFORMED_MODEL_OUTPUT
    assert manifest.grades.formal is domain.FormalStatus.NOT_FORMALIZED
    assert len(state.starts) == 1
    assert manifest.claim_sha256 is None


def test_setup_and_runtime_failures_finalize_without_false_progress(tmp_path) -> None:
    workflow, domain, unhealthy, unhealthy_state = _scripted_controller(
        tmp_path / 'unhealthy', healthy=False
    )
    setup_manifest = unhealthy.run(
        workflow.ProveRequest(text='A claim.', model='gpt-test'), Terminal()
    )
    assert setup_manifest.phase is domain.RunPhase.SETUP
    assert setup_manifest.terminal_reason is domain.TerminalReason.SETUP_FAILURE
    assert unhealthy_state.starts == []

    workflow, domain, unauthenticated, _ = _scripted_controller(
        tmp_path / 'unauthenticated', healthy=False, authenticated=False
    )
    auth_manifest = unauthenticated.run(
        workflow.ProveRequest(text='A claim.', model='gpt-test'), Terminal()
    )
    assert auth_manifest.terminal_reason is domain.TerminalReason.AUTHENTICATION_FAILURE

    workflow, domain, broken, _ = _scripted_controller(
        tmp_path / 'broken', runtime_error=True
    )
    broken_manifest = broken.run(
        workflow.ProveRequest(text='A claim.', model='gpt-test'), Terminal()
    )
    assert broken_manifest.phase is domain.RunPhase.FORMALIZING
    assert broken_manifest.terminal_reason is domain.TerminalReason.AGENT_RUNTIME_FAILURE
    assert (next((tmp_path / 'broken').iterdir()) / 'manifest.json').exists()


def test_budget_exhaustion_keeps_last_attempt_and_honest_partial_pdf(tmp_path) -> None:
    domain_module = importlib.import_module('hardy.domain')
    limits = domain_module.RunLimits(official_checks=2)
    workflow, domain, controller, state = _scripted_controller(
        tmp_path,
        proof_results=[False, False],
        limits=limits,
    )

    manifest = controller.run(
        workflow.ProveRequest(text='Two equals two.', model='gpt-test'), Terminal()
    )

    run_dir = next(tmp_path.iterdir())
    assert state.verifier_calls == 2
    assert manifest.phase is domain.RunPhase.COMPLETED
    assert manifest.terminal_reason is domain.TerminalReason.TIMEOUT_BUDGET_EXHAUSTED
    assert manifest.grades.formal is domain.FormalStatus.PARTIAL
    assert (run_dir / 'lean' / 'last-attempt.lean').exists()
    assert not (run_dir / 'lean' / 'Main.lean').exists()
    assert (run_dir / 'writeup' / 'paper.pdf').exists()


def test_tex_failure_does_not_erase_verified_mathematical_grades(tmp_path) -> None:
    workflow, domain, controller, _ = _scripted_controller(
        tmp_path, document_status='tex_failed'
    )

    manifest = controller.run(
        workflow.ProveRequest(text='Two equals two.', model='gpt-test'), Terminal()
    )

    assert manifest.grades.formal is domain.FormalStatus.KERNEL_VERIFIED
    assert manifest.grades.faithfulness is domain.FaithfulnessStatus.USER_APPROVED
    assert manifest.grades.document is domain.DocumentStatus.TEX_FAILED
    assert manifest.terminal_reason is domain.TerminalReason.TEX_COMPILATION_FAILURE


def test_user_wait_is_excluded_from_active_time(tmp_path) -> None:
    class Clock:
        value = 0.0

        def now(self):
            return self.value

    clock = Clock()

    class WaitingTerminal(Terminal):
        def choose_approval(self):
            clock.value += 100.0
            return super().choose_approval()

    workflow, _, controller, _ = _scripted_controller(
        tmp_path, monotonic=clock.now
    )

    manifest = controller.run(
        workflow.ProveRequest(text='Two equals two.', model='gpt-test'),
        WaitingTerminal(),
    )

    assert manifest.timings_ms['user_wait_excluded'] == 100_000
    assert manifest.timings_ms['active'] == 0


def test_a_disputed_translation_stops_the_run_before_any_proof_search(tmp_path) -> None:
    """The gate the whole workflow is arranged around.

    Kernel acceptance would say a Lean statement was proved and nothing about
    whether it is the claim the user made, so a translation the independent
    reader will not accept stops here — before the proving budget is spent,
    and before any downstream signal can read green on the wrong theorem.
    """
    domain = importlib.import_module('hardy.domain')
    workflow, _, controller, state = _scripted_controller(
        tmp_path,
        reviews=[
            _review(
                domain,
                agrees=False,
                divergences=('the Lean states 2 = 2, the claim is about every prime',),
            )
        ],
    )
    terminal = Terminal()

    manifest = controller.run(
        workflow.ProveRequest(text='Every prime above two is odd.', model='gpt-test'),
        terminal,
    )

    assert state.verifier_calls == 0
    assert not [stage for stage, _ in state.prompts if stage == 'proof']
    assert manifest.phase is domain.RunPhase.CANCELLED
    assert manifest.terminal_reason is domain.TerminalReason.FAITHFULNESS_DISPUTED
    assert manifest.grades.formal is domain.FormalStatus.NOT_FORMALIZED
    assert manifest.grades.faithfulness is domain.FaithfulnessStatus.NOT_APPROVED
    assert manifest.grades.known_gaps == (
        'The independent faithfulness review disputed the translation: '
        'the Lean states 2 = 2, the claim is about every prime',
    )
    # Surfaced for the human, not merely logged: a mismatch nobody is shown is
    # a mismatch nobody can resolve.
    assert [verdict.outcome for verdict in terminal.verdicts] == [
        domain.FaithfulnessOutcome.DISPUTED
    ]
    run_dir = next(tmp_path.iterdir())
    assert not (run_dir / 'lean' / 'Main.lean').exists()
    assert not (run_dir / 'writeup').exists()


def test_a_disputed_run_records_the_verdict_beside_the_claim_it_read(tmp_path) -> None:
    domain = importlib.import_module('hardy.domain')
    workflow, _, controller, _ = _scripted_controller(
        tmp_path,
        reviews=[_review(domain, agrees=False, divergences=('the quantifier moved',))],
    )

    manifest = controller.run(
        workflow.ProveRequest(text='Two equals two.', model='gpt-test'), Terminal()
    )

    run_dir = next(tmp_path.iterdir())
    verdict = manifest.grades.faithfulness_review
    assert verdict is not None
    assert verdict.claim_sha256 == manifest.claim_sha256
    assert verdict.reviewer_model == 'gpt-test'
    saved = domain.FaithfulnessVerdict.model_validate_json(
        (run_dir / 'faithfulness.json').read_text(encoding='utf-8')
    )
    assert saved == verdict
    events = [
        json.loads(line)
        for line in (run_dir / 'trajectory.jsonl').read_text(encoding='utf-8').splitlines()
    ]
    kinds = [event['kind'] for event in events]
    assert 'faithfulness.verdict' in kinds
    # Before proving was ever entered, which is the ordering the gate is for.
    assert kinds.index('faithfulness.verdict') < len(kinds) - 1
    assert not any(
        event['payload'].get('to') == 'proving'
        for event in events
        if event['kind'] == 'workflow.transition'
    )


def test_a_reader_that_cannot_be_reached_stops_the_run_too(tmp_path) -> None:
    """Fail-closed: an unobtainable review is not a passed review."""
    workflow, domain, controller, state = _scripted_controller(
        tmp_path,
        reviews=[ValueError('faithfulness turn returned no structured final response')],
    )

    manifest = controller.run(
        workflow.ProveRequest(text='Two equals two.', model='gpt-test'), Terminal()
    )

    assert state.verifier_calls == 0
    assert manifest.terminal_reason is domain.TerminalReason.FAITHFULNESS_UNAVAILABLE
    assert manifest.grades.faithfulness_review.outcome is (
        domain.FaithfulnessOutcome.UNAVAILABLE
    )
    assert 'could not be obtained' in manifest.grades.known_gaps[0]


def test_a_verified_run_carries_the_review_that_let_it_start(tmp_path) -> None:
    workflow, domain, controller, _ = _scripted_controller(tmp_path)
    terminal = Terminal()

    manifest = controller.run(
        workflow.ProveRequest(text='Two equals two.', model='gpt-test'), terminal
    )

    assert manifest.grades.formal is domain.FormalStatus.KERNEL_VERIFIED
    assert manifest.grades.faithfulness is domain.FaithfulnessStatus.USER_APPROVED
    verdict = manifest.grades.faithfulness_review
    assert verdict is not None and verdict.agreed
    assert verdict.claim_sha256 == manifest.claim_sha256
    # Shown on a pass as well: a gate whose only visible output is a refusal
    # leaves a reader unable to tell a checked run from an unchecked one.
    assert [item.outcome for item in terminal.verdicts] == [
        domain.FaithfulnessOutcome.AGREED
    ]


def test_the_reviewer_model_can_be_configured_away_from_the_run_model(tmp_path) -> None:
    """Independent context is the default; independent weights are a setting."""
    config_module = importlib.import_module('hardy.config')
    domain = importlib.import_module('hardy.domain')
    workflow, _, controller, _ = _scripted_controller(tmp_path)
    controller._config = dataclasses.replace(
        _config(config_module, domain, tmp_path), faithfulness_model='a-second-model'
    )

    manifest = controller.run(
        workflow.ProveRequest(text='Two equals two.', model='gpt-test'), Terminal()
    )

    assert manifest.grades.faithfulness_review.reviewer_model == 'a-second-model'


def test_a_cancelled_proof_still_reports_that_the_translation_was_read(tmp_path) -> None:
    """The verdict is a fact about the claim, not about how the run ended."""
    workflow, domain, controller, state = _scripted_controller(
        tmp_path, interrupt_stage='proof'
    )

    manifest = controller.run(
        workflow.ProveRequest(text='Two equals two.', model='gpt-test'), Terminal()
    )

    assert manifest.terminal_reason is domain.TerminalReason.USER_CANCELLATION
    assert manifest.grades.faithfulness is domain.FaithfulnessStatus.USER_APPROVED
    assert manifest.grades.faithfulness_review.agreed


def test_a_cancellation_during_the_review_reaches_the_reader_s_own_thread(
    tmp_path,
) -> None:
    """The reader has a live provider thread behind it like any other stage.

    Cancelling the formalizing thread instead would leave the reader running
    while `_finalize` hashes the run directory, and an event appended after
    that leaves a manifest naming the hash of a file that changed after it was
    read.
    """
    workflow, domain, controller, state = _scripted_controller(
        tmp_path, interrupt_stage='faithfulness'
    )

    manifest = controller.run(
        workflow.ProveRequest(text='Two equals two.', model='gpt-test'), Terminal()
    )

    assert manifest.terminal_reason is domain.TerminalReason.USER_CANCELLATION
    assert state.cancelled is state.handles[-1]
    assert state.cancelled is not state.handles[0]


def test_a_terminal_that_fails_cannot_deny_the_verdict_on_disk(tmp_path) -> None:
    """The verdict is persisted before it is shown, so the grade is set before
    it is shown too.

    `review_translation` has already written `faithfulness.json` and the
    trajectory event by the time the terminal is called. A `show_faithfulness`
    that raises would otherwise finalize a manifest recording no review beside
    a run directory that plainly holds one — exactly the disagreement the
    release audit exists to report.
    """
    workflow, domain, controller, _ = _scripted_controller(tmp_path)

    class FailingTerminal(Terminal):
        def show_faithfulness(self, verdict):
            raise RuntimeError('the terminal went away')

    manifest = controller.run(
        workflow.ProveRequest(text='Two equals two.', model='gpt-test'),
        FailingTerminal(),
    )

    run_dir = next(tmp_path.iterdir())
    assert manifest.terminal_reason is domain.TerminalReason.AGENT_RUNTIME_FAILURE
    assert (run_dir / 'faithfulness.json').exists()
    assert manifest.grades.faithfulness_review is not None
    assert manifest.grades.faithfulness_review.agreed
    # The record has to hold together, not merely contain the verdict.
    acceptance = importlib.import_module('hardy.acceptance')
    issues = acceptance.validate_run_consistency(run_dir, manifest)
    assert not [issue for issue in issues if 'faithfulness' in issue], issues


def test_an_honest_gate_halt_passes_the_repositorys_own_consistency_audit(
    tmp_path,
) -> None:
    """The audit must not contradict the workflow it audits.

    A faithfulness halt finalizes before any writeup, deliberately. The audit
    demanded `writeup/paper.tex` unconditionally, so every honest halt — this
    one, a user cancellation, a failed setup — was reported as inconsistent,
    and the one case where a missing writeup really is a finding could not be
    told apart from the many where its absence is correct.
    """
    acceptance = importlib.import_module('hardy.acceptance')
    domain = importlib.import_module('hardy.domain')
    workflow, _, controller, _ = _scripted_controller(
        tmp_path,
        reviews=[_review(domain, agrees=False, divergences=('the quantifier moved',))],
    )

    manifest = controller.run(
        workflow.ProveRequest(text='Two equals two.', model='gpt-test'), Terminal()
    )

    run_dir = next(tmp_path.iterdir())
    assert manifest.terminal_reason is domain.TerminalReason.FAITHFULNESS_DISPUTED
    assert not (run_dir / 'writeup').exists()
    assert acceptance.validate_run_consistency(run_dir, manifest) == ()


def test_a_reader_that_never_answered_is_not_a_refused_translation(tmp_path) -> None:
    """Two different facts, and automation reading `terminal_reason` acts on
    them differently: one says the translation was read and refused, the other
    that nobody read it."""
    domain = importlib.import_module('hardy.domain')
    workflow, _, controller, _ = _scripted_controller(
        tmp_path, reviews=[ConnectionError('the provider closed the connection')]
    )

    manifest = controller.run(
        workflow.ProveRequest(text='Two equals two.', model='gpt-test'), Terminal()
    )

    assert manifest.terminal_reason is domain.TerminalReason.FAITHFULNESS_UNAVAILABLE
    run_dir = next(tmp_path.iterdir())
    assert importlib.import_module('hardy.acceptance').validate_run_consistency(
        run_dir, manifest
    ) == ()


def test_an_exhausted_budget_does_not_buy_one_more_provider_call(tmp_path) -> None:
    """The budget check at the top of the loop runs before the formalization
    turn and the elaboration, either of which can begin inside the budget and
    finish outside it. Clamping the remainder to a one-second floor spent
    provider time the run did not have, and reported the result as a
    translation nobody read rather than as the budget running out.
    """
    domain = importlib.import_module('hardy.domain')

    class Clock:
        value = 0.0

        def now(self):
            return self.value

    clock = Clock()

    class SpendingLean:
        """Elaboration that begins inside the budget and finishes outside it."""

        def check_proof(self, claim, proof):
            clock.value += 2_000.0
            return _lean_result(domain, importlib.import_module('hardy.lean'),
                                importlib.import_module('hardy.process'), True)

    workflow, _, controller, state = _scripted_controller(
        tmp_path, limits=domain.RunLimits(active_seconds=1_800), monotonic=clock.now
    )
    controller._lean = SpendingLean()

    manifest = controller.run(
        workflow.ProveRequest(text='Two equals two.', model='gpt-test'), Terminal()
    )

    assert manifest.terminal_reason is domain.TerminalReason.TIMEOUT_BUDGET_EXHAUSTED
    assert not [stage for stage, _ in state.prompts if stage == 'faithfulness']
    assert manifest.grades.faithfulness_review is None
    assert 'budget expired' in manifest.grades.known_gaps[0]
    run_dir = next(tmp_path.iterdir())
    assert not (run_dir / 'faithfulness.json').exists()


def test_the_reader_is_given_the_budget_that_is_actually_left(tmp_path) -> None:
    domain = importlib.import_module('hardy.domain')

    class Clock:
        value = 0.0

        def now(self):
            return self.value

    clock = Clock()

    class SlowLean:
        def check_proof(self, claim, proof):
            clock.value += 1_000.0
            return _lean_result(domain, importlib.import_module('hardy.lean'),
                                importlib.import_module('hardy.process'), True)

    workflow, _, controller, state = _scripted_controller(
        tmp_path, limits=domain.RunLimits(active_seconds=1_800), monotonic=clock.now
    )
    controller._lean = SlowLean()

    controller.run(
        workflow.ProveRequest(text='Two equals two.', model='gpt-test'), Terminal()
    )

    # Threads in order: formalizing, the reader, proving. Only the reader is
    # bounded — 1800 declared, 1000 already spent by the elaboration above.
    assert state.deadlines == [None, 800.0, None]


def test_the_manifest_records_what_the_runtime_says_the_run_cost(tmp_path) -> None:
    """Version 4 typed `usage` as integers and every staged run left it empty,
    which read as a run that had spent nothing. The runtime's ledger is what
    the manifest now carries, `None` where the provider said nothing."""
    workflow, domain, controller, _ = _scripted_controller(tmp_path)
    spend = {
        'exchanges': 3,
        'cost_usd': 1.5,
        'input_tokens': 1000,
        'output_tokens': None,
        'cache_write_tokens': None,
        'cache_read_tokens': None,
        'total_tokens': 1000,
        'reported': {'cost_usd': 3, 'input_tokens': 3, 'output_tokens': 0, 'cache_write_tokens': 0, 'cache_read_tokens': 0},
    }
    original = controller._runtime_factory

    class Metered:
        def __init__(self, inner):
            self._inner = inner
            self.usage = spend

        def __getattr__(self, name):
            return getattr(self._inner, name)

    controller._runtime_factory = lambda store: Metered(original(store))

    manifest = controller.run(
        workflow.ProveRequest(text='Two equals two.', model='fixture', problem_slug='spend'),
        Terminal(),
    )

    assert manifest.usage == spend
    assert manifest.schema_version == 5


def test_a_run_that_opened_no_provider_thread_records_no_spend(tmp_path) -> None:
    workflow, domain, controller, _ = _scripted_controller(tmp_path, healthy=False)

    manifest = controller.run(
        workflow.ProveRequest(text='Two equals two.', model='fixture', problem_slug='unwell'),
        Terminal(),
    )

    assert manifest.terminal_reason is domain.TerminalReason.SETUP_FAILURE
    assert manifest.usage == {}


def test_cancel_from_another_thread_stops_the_run_and_grades_it_as_cancelled(tmp_path) -> None:
    """`/prove` runs the whole workflow on a worker, so its Esc cannot raise
    `KeyboardInterrupt` inside it. Without a handle from outside, the provider
    call went on billing and the run went on writing itself; and the failure
    the interrupted stage raises must not be graded as a runtime failure, which
    would put the wrong terminal reason in every abandoned run's manifest."""
    workflow, domain, controller, state = _scripted_controller(
        tmp_path, cancel_stage='proof'
    )
    terminal = Terminal()
    manifest = controller.run(
        workflow.ProveRequest(text='two equals two', model='test-model'), terminal
    )
    assert manifest.terminal_reason is domain.TerminalReason.USER_CANCELLATION
    # The proving thread, not the formalizing one: cancelling the wrong handle
    # leaves the live one appending after the manifest is hashed.
    assert state.cancelled is state.handles[-1]


def test_a_cancelled_run_starts_no_further_stage(tmp_path) -> None:
    """Cancelled between calls, so nothing raises on its own: the stage loops
    have to notice, or the run spends its whole budget after the press."""
    workflow, domain, controller, state = _scripted_controller(
        tmp_path, cancel_quietly_at='formalization'
    )
    manifest = controller.run(
        workflow.ProveRequest(text='two equals two', model='test-model'), Terminal()
    )
    assert manifest.terminal_reason is domain.TerminalReason.USER_CANCELLATION
    assert 'proof' not in [stage for stage, _ in state.prompts]


def test_a_cancellation_arriving_before_the_run_starts_is_not_lost(tmp_path) -> None:
    """`/prove` publishes the workflow as soon as it is built, which is before
    `run` is called -- and building it identifies the Lean environment, so that
    window is a likely moment to press Esc. A `clear()` at the top of `run`
    threw that press away and started the provider run anyway."""
    workflow, domain, controller, state = _scripted_controller(tmp_path)
    controller.cancel()                       # as the terminal does, before `run`
    manifest = controller.run(
        workflow.ProveRequest(text='two equals two', model='test-model'), Terminal()
    )
    assert manifest.terminal_reason is domain.TerminalReason.USER_CANCELLATION
    assert state.starts == [], 'a provider thread was opened for an abandoned run'


def test_a_run_cancelled_during_verification_does_not_open_the_writeup_turn(tmp_path) -> None:
    """The verifier runs Lean over the whole claim and can take minutes, so a
    press very plausibly lands inside it -- and the verified branch leaves the
    proving loop for the writeup turn without passing its check again."""
    workflow, domain, controller, state = _scripted_controller(tmp_path)

    verify = controller._verifier.verify

    def cancelling(claim, proof_body, store):
        result = verify(claim, proof_body, store)
        controller.cancel()                   # the press, mid-verification
        return result

    controller._verifier = SimpleNamespace(verify=cancelling)
    manifest = controller.run(
        workflow.ProveRequest(text='two equals two', model='test-model'), Terminal()
    )
    assert manifest.terminal_reason is domain.TerminalReason.USER_CANCELLATION
    assert 'writeup' not in [stage for stage, _ in state.prompts]


def test_a_run_cancelled_during_a_rejecting_verification_is_not_graded_completed(
    tmp_path,
) -> None:
    """The check lived on the verified branch alone. A rejection on the last
    attempt then went to writeup with cancellation set: the runtime refused the
    turn, the writeup fallback caught that, compiled TeX, and called an
    abandoned run `completed`."""
    workflow, domain, controller, state = _scripted_controller(
        tmp_path, proof_results=[False]
    )
    verify = controller._verifier.verify

    def cancelling(claim, proof_body, store):
        result = verify(claim, proof_body, store)
        controller.cancel()
        return result

    controller._verifier = SimpleNamespace(verify=cancelling)
    manifest = controller.run(
        workflow.ProveRequest(text='two equals two', model='test-model'), Terminal()
    )
    assert manifest.terminal_reason is domain.TerminalReason.USER_CANCELLATION
    assert 'writeup' not in [stage for stage, _ in state.prompts]


def test_a_run_cancelled_while_the_document_compiled_is_not_a_tex_failure(
    tmp_path,
) -> None:
    """Tectonic is a tracked child, so Esc reaches it and the build comes back
    TEX_FAILED. That was the last thing to touch the terminal reason, so an
    abandoned run was recorded as a completed experiment whose document failed
    to compile -- a claim about the toolchain, for something the user did."""
    workflow, domain, controller, state = _scripted_controller(
        tmp_path, document_status='tex_failed'
    )
    build = controller._writeup_builder

    def cancelling(*args, **kwargs):
        controller.cancel()               # the press, mid-compilation
        return build(*args, **kwargs)

    controller._writeup_builder = cancelling
    manifest = controller.run(
        workflow.ProveRequest(text='two equals two', model='test-model'), Terminal()
    )

    assert manifest.terminal_reason is domain.TerminalReason.USER_CANCELLATION
    # Both facts, not one: the document really did not compile, and the reason
    # the run ended is still the user.
    assert manifest.grades.document is domain.DocumentStatus.TEX_FAILED


def test_a_run_cancelled_during_the_writeup_turn_compiles_nothing(tmp_path) -> None:
    """The writeup turn has a fallback catching `RuntimeError`, which is what
    the staged runtime raises to refuse a turn on a cancelled run -- so the
    refusal was swallowed and the run went on to build a document anyway."""
    workflow, domain, controller, state = _scripted_controller(
        tmp_path, cancel_quietly_at='writeup'
    )
    built = []
    build = controller._writeup_builder
    controller._writeup_builder = lambda *a, **k: built.append(a) or build(*a, **k)

    manifest = controller.run(
        workflow.ProveRequest(text='two equals two', model='test-model'), Terminal()
    )

    assert manifest.terminal_reason is domain.TerminalReason.USER_CANCELLATION
    assert built == [], 'a cancelled run compiled a document'
