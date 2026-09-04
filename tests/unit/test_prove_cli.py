import dataclasses
import importlib
import json
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID, uuid4


def test_prove_accepts_an_ordinary_language_claim_and_exact_model() -> None:
    cli = importlib.import_module('hardy.cli')

    args = cli.build_parser().parse_args(
        ['prove', '--model', 'gpt-test', 'For every n, n plus zero is n.']
    )

    assert args.model == 'gpt-test'
    assert args.claim == 'For every n, n plus zero is n.'


def test_console_terminal_requires_exact_unsafe_ack_and_labels_elaboration() -> None:
    cli = importlib.import_module('hardy.cli')
    answers = iter(['almost', 'I UNDERSTAND'])
    output = []
    terminal = cli.ConsoleTerminal(input_fn=lambda _: next(answers), output=output.append)

    assert not terminal.acknowledge_unsafe_execution()
    assert terminal.acknowledge_unsafe_execution()
    terminal.show_formalization(
        SimpleNamespace(
            restatement='Two equals two.',
            domains=(),
            quantifiers=(),
            assumptions=(),
            interpretation_choices=(),
            theorem_name='two_eq_two',
            binders='',
            proposition='2 = 2',
        ),
        SimpleNamespace(success=True),
    )

    rendered = '\n'.join(output)
    assert 'statement elaborates; this is not proof evidence' in rendered
    assert 'theorem two_eq_two : 2 = 2' in rendered


def test_run_prove_dispatches_the_exact_claim_and_model_to_the_workflow(
    tmp_path,
) -> None:
    cli = importlib.import_module('hardy.cli')
    config_module = importlib.import_module('hardy.config')
    domain = importlib.import_module('hardy.domain')
    config_path = tmp_path / 'config.toml'
    config_module.write_setting(config_path, 'runs_root', str(tmp_path / 'runs'))
    seen = []

    class Workflow:
        def run(self, request, terminal):
            seen.append(request)
            return domain.RunManifest(
                run_id=UUID('12345678-1234-5678-1234-567812345678'),
                created_at=datetime(2026, 7, 24, tzinfo=UTC),
                phase=domain.RunPhase.COMPLETED,
                model=request.model,
                prompt_set_sha256='p' * 64,
            )

    result = cli.run_prove(
        SimpleNamespace(
            config=str(config_path),
            model='gpt-test',
            claim='For every n, n plus zero is n.',
        ),
        workflow_factory=lambda config, path, backend='claude': Workflow(),
        input_fn=lambda _: 'unused',
    )

    assert result == 0
    assert seen[0].model == 'gpt-test'
    assert seen[0].text == 'For every n, n plus zero is n.'


def _staged_config(tmp_path, **overrides):
    """A `Config` complete enough for `build_prove_workflow`.

    `environment_identity` reads `lake-manifest.json` off disk and asks the
    configured `lake` what Lean it runs, so the project carries a minimal
    manifest and `lake` is a stub that answers `--version` the way real Lean
    does -- enough to build the staged workflow hermetically.
    """
    config_module = importlib.import_module('hardy.config')
    lean_project = tmp_path / 'lean'
    lean_project.mkdir(exist_ok=True)
    manifest = lean_project / 'lake-manifest.json'
    if not manifest.exists():
        manifest.write_text(
            json.dumps({'packages': [{'name': 'mathlib', 'rev': 'deadbeefcafe'}]}),
            encoding='utf-8',
        )
    lake = tmp_path / 'bin' / 'lake'
    if not lake.exists():
        lake.parent.mkdir(parents=True, exist_ok=True)
        lake.write_text(
            '#!/bin/sh\n'
            "printf 'Lean (version 4.32.0, x86_64-unknown-linux-gnu, "
            "commit 8c9756b28d64dab099da31a4c09229a9e6a2ef35, Release)\\n'\n"
            'exit 0\n',
            encoding='utf-8',
        )
        lake.chmod(0o755)
    settings = {
        'model': 'claude-opus-5',
        'lean_command': (str(lake), 'env', 'lean'),
        'lean_project': lean_project,
        'lean_timeout': 30.0,
        'latex_command': ('pdflatex',),
        'root': tmp_path,
        'project': 'workspace',
        'runs_root': tmp_path / 'runs',
        'lake': lake,
    }
    settings.update(overrides)
    return config_module.Config(**settings)


def test_staged_doctor_ignores_an_advisory_cas_failure(tmp_path, monkeypatch) -> None:
    """`doctor._cas_check` marks a failed default-backend probe
    `required=False`; the staged health calculation must honor that instead
    of failing every `hardy prove` run over an optional tool.
    """
    cli = importlib.import_module('hardy.cli')
    doctor_module = importlib.import_module('hardy.doctor')
    config = _staged_config(tmp_path)

    def fake_checks(value, *, deep=False, backend=None):
        return [
            doctor_module.Check('python', True, 'ok'),
            doctor_module.Check('lean', True, 'ok'),
            doctor_module.Check('cas', False, 'sympy driver not found', required=False),
        ]

    monkeypatch.setattr(cli.doctor, 'run_checks', fake_checks)
    workflow = cli.build_prove_workflow(config, tmp_path / 'config.toml')

    report = workflow._doctor(config)

    assert report.healthy is True


def test_the_staged_doctor_checks_the_backend_the_run_will_build(tmp_path, monkeypatch):
    """`hardy prove` takes a `--backend` of its own, and the global setting is
    for interactive and batch work. Reading the wrong one blocks a usable
    staged run on a missing API key -- and, worse, reports a machine ready on
    credentials the run is not going to use."""
    cli = importlib.import_module('hardy.cli')
    doctor_module = importlib.import_module('hardy.doctor')
    config = dataclasses.replace(_staged_config(tmp_path), backend='api')
    seen = {}

    def fake_checks(value, *, deep=False, backend=None):
        seen['backend'] = backend
        return [doctor_module.Check('python', True, 'ok')]

    monkeypatch.setattr(cli.doctor, 'run_checks', fake_checks)
    workflow = cli.build_prove_workflow(config, tmp_path / 'config.toml', backend='claude')

    workflow._doctor(config)

    assert seen['backend'] == 'claude'


def test_doctor_asked_for_a_backend_checks_that_one(tmp_path):
    doctor_module = importlib.import_module('hardy.doctor')
    config = dataclasses.replace(_staged_config(tmp_path), backend='api')

    names = [check.name for check in doctor_module.run_checks(config, backend='claude')]

    assert 'claude sdk' in names and 'anthropic key' not in names


def test_staged_runtime_factory_records_cas_tool_results_in_the_trajectory(
    tmp_path,
) -> None:
    """The factory must pass `build_runtime()` an `observe` callback so a
    completed `cas_run` shows up in `trajectory.jsonl`, not only in the
    separate CAS cell log.
    """
    cli = importlib.import_module('hardy.cli')
    storage_module = importlib.import_module('hardy.storage')
    config = _staged_config(tmp_path, cas_backend='sympy')
    workflow = cli.build_prove_workflow(config, tmp_path / 'config.toml')

    run_dir = tmp_path / 'run'
    run_dir.mkdir()
    store = storage_module.RunStore(run_dir, uuid4())

    runtime = workflow._runtime_factory(store)
    try:
        result = runtime._cas_dispatch('cas_run', {'source': '1 + 1'})
        assert result.ok is True
    finally:
        runtime.close()

    events = [
        json.loads(line)
        for line in store.trajectory_path.read_text(encoding='utf-8').splitlines()
    ]
    cas_events = [event for event in events if event['kind'].startswith('cas.')]
    assert cas_events, f'no cas.* trajectory event was recorded, saw {[e["kind"] for e in events]}'
    assert cas_events[0]['payload']['record']['status'] == 'ok'
    # The event's own `type` is already "cas", so prefixing it named the
    # subsystem twice and the thing recorded not at all.
    assert cas_events[0]['kind'] == 'cas.cell'


def _verdict(domain, outcome, **overrides):
    values = dict(
        claim_sha256='a' * 64,
        reviewer_model='reviewer-model',
        prompt_sha256='d' * 64,
        outcome=outcome,
    )
    values.update(overrides)
    return domain.FaithfulnessVerdict(**values)


def test_console_terminal_shows_the_divergences_and_says_the_run_stops() -> None:
    """A mismatch nobody is shown is a mismatch nobody can resolve."""
    cli = importlib.import_module('hardy.cli')
    domain = importlib.import_module('hardy.domain')
    output = []
    terminal = cli.ConsoleTerminal(input_fn=lambda _: '', output=output.append)

    terminal.show_faithfulness(
        _verdict(
            domain,
            domain.FaithfulnessOutcome.DISPUTED,
            review=domain.FaithfulnessReview(
                formalization_entails_claim=False,
                claim_entails_formalization=True,
                divergences=('the Lean fixes n = 0 rather than quantifying over n',),
                notes='The claim is general.',
            ),
        )
    )

    rendered = '\n'.join(output)
    assert 'Independent faithfulness review by reviewer-model: disputed' in rendered
    assert 'the Lean fixes n = 0 rather than quantifying over n' in rendered
    assert 'The run stops here.' in rendered


def test_console_terminal_reports_an_agreement_too() -> None:
    """Otherwise a user cannot tell a checked run from one where the gate
    never ran: silence would look the same either way."""
    cli = importlib.import_module('hardy.cli')
    domain = importlib.import_module('hardy.domain')
    output = []
    terminal = cli.ConsoleTerminal(input_fn=lambda _: '', output=output.append)

    terminal.show_faithfulness(
        _verdict(
            domain,
            domain.FaithfulnessOutcome.AGREED,
            review=domain.FaithfulnessReview(
                formalization_entails_claim=True,
                claim_entails_formalization=True,
            ),
        )
    )

    rendered = '\n'.join(output)
    assert 'Independent faithfulness review by reviewer-model: agreed' in rendered
    assert 'The run stops here.' not in rendered


def test_console_terminal_says_why_a_review_could_not_be_obtained() -> None:
    cli = importlib.import_module('hardy.cli')
    domain = importlib.import_module('hardy.domain')
    output = []
    terminal = cli.ConsoleTerminal(input_fn=lambda _: '', output=output.append)

    terminal.show_faithfulness(
        _verdict(
            domain,
            domain.FaithfulnessOutcome.UNAVAILABLE,
            detail='ValueError: faithfulness turn returned no structured final response',
        )
    )

    rendered = '\n'.join(output)
    assert 'unavailable' in rendered
    assert 'no structured final response' in rendered
    assert 'The run stops here.' in rendered
    assert 'No reader assessed the translation' in rendered


def test_the_result_summary_says_whether_the_translation_was_read() -> None:
    cli = importlib.import_module('hardy.cli')
    domain = importlib.import_module('hardy.domain')
    output = []
    terminal = cli.ConsoleTerminal(input_fn=lambda _: '', output=output.append)

    terminal.show_result(
        domain.RunManifest(
            run_id=UUID('12345678-1234-5678-1234-567812345678'),
            created_at=datetime(2026, 8, 27, tzinfo=UTC),
            phase=domain.RunPhase.CANCELLED,
            model='gpt-test',
            prompt_set_sha256='p' * 64,
        )
    )

    assert 'Faithfulness: not_approved (no independent review)' in '\n'.join(output)


def test_an_unavailable_review_does_not_tell_the_user_to_restate_the_claim() -> None:
    """Nothing was read, so there is nothing to reword.

    Sending the user to rewrite a claim no reader ever saw points them at
    something that was never the problem.
    """
    cli = importlib.import_module('hardy.cli')
    domain = importlib.import_module('hardy.domain')
    output = []
    terminal = cli.ConsoleTerminal(input_fn=lambda _: '', output=output.append)

    terminal.show_faithfulness(
        _verdict(
            domain,
            domain.FaithfulnessOutcome.UNAVAILABLE,
            detail='ConnectionError: the provider closed the connection',
        )
    )

    rendered = '\n'.join(output)
    assert 'The run stops here.' in rendered
    assert 'No reader assessed the translation' in rendered
    assert 'Restate the claim' not in rendered


def test_the_reviewer_model_can_be_overridden_for_one_invocation(tmp_path) -> None:
    """`faithfulness_model` is one global setting and the backends do not share
    model names, so a config naming a Claude reviewer would otherwise be handed
    to a `--backend codex` run — whose reader fails on an identity that backend
    cannot serve, halting every approved claim with no way to repair it, since
    `--model` sets the run's model and not the reviewer's.
    """
    cli = importlib.import_module('hardy.cli')
    config_module = importlib.import_module('hardy.config')
    domain = importlib.import_module('hardy.domain')
    config_path = tmp_path / 'config.toml'
    config_module.write_setting(config_path, 'runs_root', str(tmp_path / 'runs'))
    config_module.write_setting(config_path, 'faithfulness_model', 'claude-reviewer')
    seen = []

    def workflow_factory(config, path, *, backend):
        seen.append(config.faithfulness_model)

        class Workflow:
            def run(self, request, terminal):
                return domain.RunManifest(
                    run_id=UUID('12345678-1234-5678-1234-567812345678'),
                    created_at=datetime(2026, 8, 27, tzinfo=UTC),
                    phase=domain.RunPhase.COMPLETED,
                    model=request.model,
                    prompt_set_sha256='p' * 64,
                )

        return Workflow()

    for override in (None, 'gpt-reviewer'):
        cli.run_prove(
            SimpleNamespace(
                config=str(config_path),
                model='gpt-test',
                claim='Two equals two.',
                backend='codex',
                faithfulness_model=override,
            ),
            workflow_factory=workflow_factory,
        )

    assert seen == ['claude-reviewer', 'gpt-reviewer']


def test_prove_accepts_the_reviewer_model_flag() -> None:
    cli = importlib.import_module('hardy.cli')

    args = cli.build_parser().parse_args(
        ['prove', '--faithfulness-model', 'gpt-reviewer', 'Two equals two.']
    )

    assert args.faithfulness_model == 'gpt-reviewer'


def test_accept_takes_the_same_reviewer_override_as_prove() -> None:
    """`run_accept` builds the selected backend from the global config too, so
    a configured Claude reviewer would meet a `--backend codex` acceptance run
    and halt both problems as unavailable."""
    cli = importlib.import_module('hardy.cli')

    args = cli.build_parser().parse_args(
        ['accept', '--backend', 'codex', '--faithfulness-model', 'gpt-reviewer']
    )

    assert args.faithfulness_model == 'gpt-reviewer'


def test_a_lean_that_cannot_be_identified_is_a_recorded_setup_failure(tmp_path) -> None:
    """Not a traceback: the identity probe runs before the workflow exists,
    and a `lake` that answers `--version` with nothing used to escape
    `run_prove` uncaught, leaving no manifest and no trajectory behind."""
    cli = importlib.import_module('hardy.cli')
    domain = importlib.import_module('hardy.domain')
    workflow_module = importlib.import_module('hardy.workflow')
    config = _staged_config(tmp_path)
    config.lake.write_text('#!/bin/sh\nexit 0\n', encoding='utf-8')

    workflow = cli.build_prove_workflow(config, tmp_path / 'config.toml')

    class Terminal:
        def acknowledge_unsafe_execution(self):
            return True

        def show_result(self, manifest):
            pass

    manifest = workflow.run(
        workflow_module.ProveRequest(text='Two equals two.', model='claude-opus-5', problem_slug='unidentified'),
        Terminal(),
    )

    assert manifest.terminal_reason is domain.TerminalReason.SETUP_FAILURE
    assert manifest.environment is None
    run_dir = next(path for path in (tmp_path / 'runs').iterdir() if path.is_dir())
    events = [
        json.loads(line)
        for line in (run_dir / 'trajectory.jsonl').read_text(encoding='utf-8').splitlines()
    ]
    setup = next(event for event in events if event['kind'] == 'workflow.setup')
    assert 'named no Lean version' in setup['payload']['detail']
