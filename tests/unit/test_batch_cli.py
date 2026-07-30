import importlib
import json

import pytest


def _config(module, tmp_path):
    return module.Config(
        model='fake-model',
        lean_command=('true',),
        lean_project=None,
        lean_timeout=5.0,
        latex_command=('true',),
        workspace=tmp_path / 'workspace',
        runs_root=tmp_path / 'runs',
    )


def _request(tmp_path, declaration):
    path = tmp_path / 'request.json'
    path.write_text(
        json.dumps({'declaration': declaration, 'informal_claim': 'True is true.'}),
        encoding='utf-8',
    )
    return path


def test_batch_refuses_an_anonymous_example_before_spending_a_model_run(tmp_path) -> None:
    """Nothing can print an `example`'s axioms, so the run could only ever end
    `axioms_rejected` — after paying for every turn it took to get there."""
    cli = importlib.import_module('hardy.cli')
    config_module = importlib.import_module('hardy.config')
    parser = cli.build_parser()
    args = parser.parse_args(['batch', str(_request(tmp_path, 'example : True'))])

    with pytest.raises(SystemExit):
        cli._batch(args, _config(config_module, tmp_path), parser)


def test_batch_still_runs_a_named_theorem(tmp_path, monkeypatch, capsys) -> None:
    """The guard must not refuse the shape `examples/true.json` actually uses."""
    cli = importlib.import_module('hardy.cli')
    config_module = importlib.import_module('hardy.config')
    models = importlib.import_module('hardy.models')
    reached = []

    def fake_run(request, *_args, **_kwargs):
        reached.append(request.declaration)
        return models.RunResult(
            'verified', 'kernel verified', 'not assessed', 'by trivial', '', {'status': 'clean'}, 1
        )

    monkeypatch.setattr(cli, 'run', fake_run)
    parser = cli.build_parser()
    args = parser.parse_args(['batch', str(_request(tmp_path, 'theorem HardyTarget : True'))])

    assert cli._batch(args, _config(config_module, tmp_path), parser) == 0
    assert reached == ['theorem HardyTarget : True']
    assert json.loads(capsys.readouterr().out)['axioms'] == {'status': 'clean'}
