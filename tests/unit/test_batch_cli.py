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
        root=tmp_path,
        project='workspace',
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
            'verified', 'kernel verified', 'not assessed', 'by trivial', '', {'status': 'clean'}, 1,
            importlib.import_module('hardy.usage').Usage().summary(),
        )

    monkeypatch.setattr(cli, 'run', fake_run)
    parser = cli.build_parser()
    args = parser.parse_args(['batch', str(_request(tmp_path, 'theorem HardyTarget : True'))])

    assert cli._batch(args, _config(config_module, tmp_path), parser) == 0
    assert reached == ['theorem HardyTarget : True']
    assert json.loads(capsys.readouterr().out)['axioms'] == {'status': 'clean'}


def test_a_tactic_with_a_comma_in_it_is_one_tactic() -> None:
    """`simp [Nat.add_comm, Nat.add_left_comm]` is a single Lean tactic.

    Splitting every comma turned it into `simp [Nat.add_comm` and
    `Nat.add_left_comm]` -- two tactics Lean cannot parse. The ladder then
    recorded two spurious failures and spent the model turn it existed to save,
    on a request whose tactic would have closed the theorem.
    """
    cli = importlib.import_module('hardy.cli')
    parser = cli.build_parser()

    args = parser.parse_args(
        ['batch', 'request.json', '--closers', 'simp [Nat.add_comm, Nat.add_left_comm]']
    )

    assert cli._closer_ladder(args.closers) == ('simp [Nat.add_comm, Nat.add_left_comm]',)


def test_the_flag_repeats_for_several_tactics_and_keeps_their_order() -> None:
    cli = importlib.import_module('hardy.cli')
    parser = cli.build_parser()

    args = parser.parse_args(['batch', 'request.json', '--closers', 'omega', '--closers', 'aesop'])

    assert cli._closer_ladder(args.closers) == ('omega', 'aesop')


def test_a_bare_flag_is_still_the_standard_ladder_and_no_flag_is_still_off() -> None:
    cli = importlib.import_module('hardy.cli')
    closers = importlib.import_module('hardy.closers')
    parser = cli.build_parser()

    bare = parser.parse_args(['batch', 'request.json', '--closers'])
    off = parser.parse_args(['batch', 'request.json'])

    assert cli._closer_ladder(bare.closers) == closers.CLOSERS
    assert cli._closer_ladder(off.closers) is None
    # And the sentinel is never mistaken for a tactic somebody asked for.
    mixed = parser.parse_args(['batch', 'request.json', '--closers', '--closers', 'omega'])
    assert cli._closer_ladder(mixed.closers) == (*closers.CLOSERS, 'omega')


def test_an_infinite_wall_clock_is_refused_rather_than_waited_for(tmp_path, monkeypatch) -> None:
    """`argparse` accepts `inf` as a float, and an infinite `Thread.join`
    raises `OverflowError` while the daemon provider request carries on in the
    background -- the run written as a `runtime_error` at once, for a request
    that may yet finish and be billed for. A bound nothing can wait for is not
    a bound."""
    cli = importlib.import_module('hardy.cli')
    config_module = importlib.import_module('hardy.config')
    parser = cli.build_parser()

    for value in ('inf', '-inf', 'nan'):
        # `--wall-seconds=-inf` rather than two arguments: argparse reads a
        # leading `-` as the start of another flag.
        args = parser.parse_args([
            'batch', str(_request(tmp_path, 'theorem T : True')), f'--wall-seconds={value}'
        ])
        with pytest.raises(SystemExit):
            cli._batch(args, _config(config_module, tmp_path), parser)

    # And a finite one still reaches the run, carrying the configured window
    # with it -- which is the other half of this: a batch aimed at a smaller
    # gateway used to keep appending messages until the endpoint refused.
    models = importlib.import_module('hardy.models')
    seen = {}

    def fake_run(request, *_args, **kwargs):
        seen.update(kwargs)
        return models.RunResult(
            'verified', 'kernel verified', 'not assessed', 'by trivial', '', {'status': 'clean'}, 1,
            importlib.import_module('hardy.usage').Usage().summary(),
        )

    monkeypatch.setattr(cli, 'run', fake_run)
    args = parser.parse_args([
        'batch', str(_request(tmp_path, 'theorem T : True')), '--wall-seconds', '30'
    ])
    cli._batch(args, _config(config_module, tmp_path), parser)
    assert seen['wall_seconds'] == 30.0
    assert seen['context_window'] == config_module.DEFAULT_CONTEXT_WINDOW
