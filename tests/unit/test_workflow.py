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
        workspace=runs_root / 'workspace',
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


def _environment(domain):
    return domain.EnvironmentIdentity(
        lean_version='4.32.0',
        lean_commit='8c9756b',
        mathlib_revision='81a5d257',
        lake_manifest_sha256='b' * 64,
        imports=('Mathlib',),
    )


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
        self.result = None

    def show_formalization(self, proposal, elaboration):
        self.shown.append((proposal, elaboration))

    def choose_approval(self):
        return self.decisions.pop(0)

    def revision_text(self):
        return self.revisions.pop(0)

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
    state = SimpleNamespace(starts=[], prompts=[], verifier_calls=0)

    class Runtime:
        def start(self, *, model, run_dir, claim):
            if runtime_error:
                raise RuntimeError('runtime fixture failure')
            state.starts.append(claim)
            return object()

        def run_structured(self, thread, stage, prompt, output_type):
            state.prompts.append((stage, prompt))
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
            return codex_runtime.ProofSubmission(
                proof_body='by rfl', informal_proof='Reflexivity.'
            )

        def cancel(self, thread):
            state.cancelled = True

    class Lean:
        def check_proof(self, claim, proof):
            success = elaborations.pop(0) if elaborations else False
            return _lean_result(domain, lean, process, success)

    class Verifier:
        def verify(self, claim, proof_body, store):
            state.verifier_calls += 1
            success = proof_results.pop(0) if proof_results else False
            if success:
                store.write_text(
                    PurePosixPath('lean/Main.lean'),
                    'theorem two_eq_two : 2 = 2 :=\nby rfl\n',
                )
                result = verifier_module.VerificationResult(
                    verified=True,
                    reason=None,
                    axioms=(),
                    diagnostics=(),
                    source_sha256='s' * 64,
                    verification_sha256='v' * 64,
                )
                store.write_json(PurePosixPath('lean/verification.json'), result)
                return result
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
        def start(self, *, model, run_dir, claim):
            starts.append(claim)
            return object()

        def run_structured(self, thread, stage, prompt, output_type):
            prompts.append((stage, prompt))
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
            source = 'theorem two_eq_two : 2 = 2 :=\nby rfl\n'
            store.write_text(PurePosixPath('lean/Main.lean'), source)
            result = verifier_module.VerificationResult(
                verified=True,
                reason=None,
                axioms=(),
                diagnostics=(),
                source_sha256='s' * 64,
                verification_sha256='v' * 64,
            )
            store.write_json(PurePosixPath('lean/verification.json'), result)
            return result

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

    assert starts[0] is None
    assert starts[1] is not None
    assert starts[1].content_hash == manifest.claim_sha256
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
