import importlib
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID


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
