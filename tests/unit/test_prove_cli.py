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

    `_environment_identity` only reads `lake-manifest.json` off disk -- it
    never shells out to `lake` -- so a minimal manifest is enough to build the
    staged workflow hermetically.
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
    settings = {
        'model': 'claude-opus-5',
        'lean_command': ('lake', 'env', 'lean'),
        'lean_project': lean_project,
        'lean_timeout': 30.0,
        'latex_command': ('pdflatex',),
        'workspace': tmp_path / 'workspace',
        'runs_root': tmp_path / 'runs',
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

    def fake_checks(value, *, deep=False):
        return [
            doctor_module.Check('python', True, 'ok'),
            doctor_module.Check('lean', True, 'ok'),
            doctor_module.Check('cas', False, 'sympy driver not found', required=False),
        ]

    monkeypatch.setattr(cli.doctor, 'run_checks', fake_checks)
    workflow = cli.build_prove_workflow(config, tmp_path / 'config.toml')

    report = workflow._doctor(config)

    assert report.healthy is True


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
