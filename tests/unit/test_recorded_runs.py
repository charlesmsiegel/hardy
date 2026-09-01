"""The audit a kept run has to pass with no model, network, or toolchain present.

`hardy accept --recorded` reads what a paid run left on disk and refuses the
records that could not have happened as described. The runs it is written for
live under `acceptance/recorded/`; these tests build small ones with the fake
Lean and then break them one field at a time.
"""

from __future__ import annotations

import importlib
import json
import shutil
import sys
from pathlib import Path

FAKE_LEAN = Path(__file__).resolve().parents[1] / 'fake_lean.py'
IDENTITY = {
    'lean_version': '4.33.1',
    'lean_commit': '819816b2e0a3bf405af45ae5c7af2491d8f5bee6',
    'mathlib_revision': '0df444a360eaa60ab8c11dca51a86af692955474',
    'lake_manifest_sha256': 'm' * 64,
    'imports': ['Mathlib'],
}


class _Runtime:
    """The agent SDK, scripted: it calls the tools it is told to and reports a result."""

    model = 'fake-model@test'
    backend = 'claude'
    endpoint = 'fake'
    turns = 2

    def __init__(self, script, **context):
        self.script, self.context = list(script), context

    def ask(self, text):
        observe = self.context['observe']
        for name, arguments in self.script:
            self.context['dispatch'](name, arguments)
        observe({'type': 'result', 'cost_usd': 0.1, 'usage': {'input_tokens': 5, 'output_tokens': 2}, 'session_id': 's'})
        return 'done'


def _batch(tmp_path: Path, script, *, wall_seconds: float = 300.0, name: str = 'run') -> Path:
    models = importlib.import_module('hardy.models')
    lean_module = importlib.import_module('hardy.lean')
    runner = importlib.import_module('hardy.runner')
    request = models.Request.from_dict(
        {'declaration': 'theorem HardyTarget : True', 'informal_claim': 'True is true.'}
    )
    lean = lean_module.LeanTools(request, (sys.executable, str(FAKE_LEAN)))
    output = tmp_path / name
    runner.run(
        request,
        lambda model=None, **context: _Runtime(script, **context),
        lean,
        output,
        max_turns=3,
        wall_seconds=wall_seconds,
        toolchain=IDENTITY,
    )
    return output


def _verified(tmp_path: Path) -> Path:
    return _batch(tmp_path, [('submit_proof', {'proof': 'by exact True.intro'})])


def _rewrite(path: Path, **fields) -> None:
    payload = json.loads(path.read_text(encoding='utf-8'))
    payload.update(fields)
    path.write_text(json.dumps(payload, indent=2) + '\n', encoding='utf-8')


def test_a_verified_batch_run_is_self_consistent(tmp_path) -> None:
    acceptance = importlib.import_module('hardy.acceptance')

    assert acceptance.validate_batch_consistency(_verified(tmp_path)) == ()
    assert acceptance.validate_recorded_run(_verified(tmp_path / 'again')) == ()


def test_an_honest_failure_is_self_consistent_too(tmp_path) -> None:
    acceptance = importlib.import_module('hardy.acceptance')
    output = _batch(tmp_path, [('check_proof', {'proof': 'by exact True.intro'})])

    assert json.loads((output / 'result.json').read_text())['terminal_reason'] == 'no_proof_submitted'
    assert acceptance.validate_recorded_run(output) == ()


def test_a_proof_lean_the_result_does_not_describe_is_refused(tmp_path) -> None:
    """The file a reader rechecks must be the request's declaration, the
    result's proof, and the audit line -- byte for byte."""
    acceptance = importlib.import_module('hardy.acceptance')
    output = _verified(tmp_path)
    proof = output / 'proof.lean'
    proof.write_text(proof.read_text(encoding='utf-8').replace('True.intro', 'trivial'), encoding='utf-8')

    issues = acceptance.validate_batch_consistency(output)

    assert any("proof.lean is not the request's declaration" in issue for issue in issues)


def test_a_verdict_the_lean_output_does_not_support_is_refused(tmp_path) -> None:
    """The audit verdict in `result.json` must match the axiom line Lean
    printed, as the trajectory kept it. A verdict is the model's run's own
    account; the line is Lean's."""
    acceptance = importlib.import_module('hardy.acceptance')
    output = _verified(tmp_path)
    result = json.loads((output / 'result.json').read_text(encoding='utf-8'))
    result['axioms']['declarations'][0]['axioms'] = ['propext']
    (output / 'result.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')

    issues = acceptance.validate_batch_consistency(output)

    assert any('axiom line Lean printed differs' in issue for issue in issues)


def test_a_run_without_a_toolchain_identity_is_a_story_not_evidence(tmp_path) -> None:
    acceptance = importlib.import_module('hardy.acceptance')
    output = _verified(tmp_path)
    unrecorded = {'unrecorded': 'a pinned Lean environment needs lean_project set'}
    _rewrite(output / 'trajectory.json', toolchain=unrecorded)
    _rewrite(output / 'result.json', toolchain=unrecorded)

    issues = acceptance.validate_batch_consistency(output)

    assert any('toolchain identity is unrecorded' in issue for issue in issues)


def test_a_usage_field_that_is_absent_rather_than_null_is_refused(tmp_path) -> None:
    """A figure nobody reported is `None`. A key that is missing reads as free."""
    acceptance = importlib.import_module('hardy.acceptance')
    output = _verified(tmp_path)
    for name in ('result.json', 'trajectory.json'):
        payload = json.loads((output / name).read_text(encoding='utf-8'))
        del payload['usage']['cache_read_tokens']
        (output / name).write_text(json.dumps(payload, indent=2) + '\n', encoding='utf-8')

    issues = acceptance.validate_batch_consistency(output)

    assert any('does not state cache_read_tokens' in issue for issue in issues)


def test_a_wall_clock_cancelled_run_may_not_claim_a_turn_count(tmp_path) -> None:
    """The provider's count arrives with its final result, which a run Hardy's
    clock cancelled never receives -- a real-run behaviour the record encodes
    rather than rediscovers."""
    acceptance = importlib.import_module('hardy.acceptance')
    output = _batch(tmp_path, [('check_proof', {'proof': 'by exact True.intro'})])
    for name in ('result.json', 'trajectory.json'):
        _rewrite(output / name, terminal_reason='wall_clock_limit')
    writeup = output / 'writeup.md'
    writeup.write_text(
        writeup.read_text(encoding='utf-8').replace('`no_proof_submitted`', '`wall_clock_limit`'),
        encoding='utf-8',
    )
    _rewrite(output / 'result.json', turns=None)
    assert acceptance.validate_batch_consistency(output) == ()

    _rewrite(output / 'result.json', turns=3)
    issues = acceptance.validate_batch_consistency(output)

    assert any('turn count the provider never delivered' in issue for issue in issues)


def test_a_verified_grade_with_no_proof_file_is_refused(tmp_path) -> None:
    acceptance = importlib.import_module('hardy.acceptance')
    output = _verified(tmp_path)
    (output / 'proof.lean').unlink()

    issues = acceptance.validate_batch_consistency(output)

    assert any('no proof.lean' in issue for issue in issues)


def test_a_failed_run_that_left_a_proof_file_is_refused(tmp_path) -> None:
    acceptance = importlib.import_module('hardy.acceptance')
    output = _batch(tmp_path, [('check_proof', {'proof': 'by exact True.intro'})])
    shutil.copy(_verified(tmp_path / 'other') / 'proof.lean', output / 'proof.lean')

    issues = acceptance.validate_batch_consistency(output)

    assert any('left a proof.lean' in issue for issue in issues)


def test_the_deterministic_fixture_is_not_mistaken_for_a_recorded_run(tmp_path) -> None:
    """The no-model fixture is self-consistent and is not evidence: it opened
    no provider thread and elaborated nothing. A recorded run owes both."""
    acceptance = importlib.import_module('hardy.acceptance')
    config_module = importlib.import_module('hardy.config')
    config = config_module.Config(
        model='deterministic-no-model',
        lean_command=('lake', 'env', 'lean'),
        lean_project=None,
        lean_timeout=30.0,
        latex_command=('tectonic',),
        root=tmp_path,
        project='workspace',
        runs_root=tmp_path,
    )

    result = acceptance.run_deterministic_experiment(config, outcome='verified')

    assert acceptance.validate_run_consistency(result.run_dir, result.manifest) == ()
    issues = acceptance.validate_recorded_run(result.run_dir)
    assert any('no provider events' in issue for issue in issues)
    assert any('kept no axiom report from Lean' in issue for issue in issues)
    assert any('usage does not state' in issue for issue in issues)


def test_a_directory_that_is_not_a_run_says_so(tmp_path) -> None:
    acceptance = importlib.import_module('hardy.acceptance')

    assert acceptance.validate_recorded_run(tmp_path) == (
        'not a Hardy run directory: neither manifest.json nor result.json is here',
    )


def test_accept_recorded_audits_directories_and_runs_nothing(tmp_path, capsys) -> None:
    cli = importlib.import_module('hardy.cli')
    good = _verified(tmp_path)
    parser = cli.build_parser()

    assert cli.run_accept(parser.parse_args(['accept', '--recorded', str(good)])) == 0
    assert 'self-consistent' in capsys.readouterr().out

    (good / 'proof.lean').unlink()
    assert cli.run_accept(parser.parse_args(['accept', '--recorded', str(good)])) == 1
    assert 'CONSISTENCY ERROR' in capsys.readouterr().out


def test_a_writeup_naming_another_toolchain_is_refused(tmp_path) -> None:
    """Nothing hashes a batch writeup, so the human-facing copy is compared
    with the record rather than trusted beside it."""
    acceptance = importlib.import_module('hardy.acceptance')
    output = _verified(tmp_path)
    writeup = output / 'writeup.md'
    writeup.write_text(
        writeup.read_text(encoding='utf-8').replace('Lean: 4.33.1', 'Lean: 4.20.0'), encoding='utf-8'
    )

    issues = acceptance.validate_batch_consistency(output)

    assert any('writeup.md names a different toolchain' in issue for issue in issues)


def test_a_submission_accepted_after_the_deadline_is_read_as_discarded(tmp_path) -> None:
    """The runner appends the discard marker just before the late tool event,
    so an honest timed-out run with a late kernel-accepted submission passes."""
    import time

    acceptance = importlib.import_module('hardy.acceptance')
    models = importlib.import_module('hardy.models')
    lean_module = importlib.import_module('hardy.lean')
    runner = importlib.import_module('hardy.runner')
    request = models.Request.from_dict(
        {'declaration': 'theorem HardyTarget : True', 'informal_claim': 'True is true.'}
    )
    lean = lean_module.LeanTools(request, (sys.executable, str(FAKE_LEAN)))

    class Late(_Runtime):
        # No final result ever arrives on this path, so no count does either.
        turns = None

        def ask(self, text):
            time.sleep(0.25)
            self.context['dispatch']('submit_proof', {'proof': 'by exact True.intro'})
            raise TimeoutError('the run exceeded its 0.1s wall-clock budget')

    output = tmp_path / 'late'
    runner.run(
        request, lambda model=None, **context: Late([], **context), lean, output,
        wall_seconds=0.1, toolchain=IDENTITY,
    )

    trajectory = json.loads((output / 'trajectory.json').read_text(encoding='utf-8'))
    kinds = [event['type'] for event in trajectory['events']]
    assert 'discarded' in kinds
    assert acceptance.validate_recorded_run(output) == ()


def test_an_axiom_line_recorded_for_another_source_is_not_a_witness(tmp_path) -> None:
    """The runner records the hash of what each check elaborated. An accepted
    event about some other source cannot vouch for `proof.lean`."""
    acceptance = importlib.import_module('hardy.acceptance')
    output = _verified(tmp_path)
    trajectory = json.loads((output / 'trajectory.json').read_text(encoding='utf-8'))
    for event in trajectory['events']:
        if event.get('type') == 'tool' and event['name'] == 'submit_proof':
            event['result']['source_sha256'] = 'e' * 64
    (output / 'trajectory.json').write_text(json.dumps(trajectory, indent=2) + '\n', encoding='utf-8')

    issues = acceptance.validate_batch_consistency(output)

    assert any("over proof.lean's own bytes" in issue for issue in issues)


def test_a_record_that_is_json_but_not_an_object_is_a_finding(tmp_path) -> None:
    acceptance = importlib.import_module('hardy.acceptance')
    output = _verified(tmp_path)
    (output / 'result.json').write_text('[]\n', encoding='utf-8')

    assert acceptance.validate_batch_consistency(output) == (
        'a batch record is valid JSON but not an object',
    )


def test_a_writeup_about_another_statement_is_refused(tmp_path) -> None:
    acceptance = importlib.import_module('hardy.acceptance')
    output = _verified(tmp_path)
    writeup = output / 'writeup.md'
    writeup.write_text(
        writeup.read_text(encoding='utf-8').replace('theorem HardyTarget : True', 'theorem Other : 1 = 1'),
        encoding='utf-8',
    )

    issues = acceptance.validate_batch_consistency(output)

    assert any('does not state the recorded claim' in issue for issue in issues)


def test_a_directory_holding_one_run_is_audited_as_that_run(tmp_path) -> None:
    """A staged run lives one level below the directory a reader names, so
    `hardy accept --recorded acceptance/recorded/*` has to reach it."""
    acceptance = importlib.import_module('hardy.acceptance')
    parent = tmp_path / 'prove-verified'
    _batch(parent, [('submit_proof', {'proof': 'by exact True.intro'})], name='20260901-run')

    assert acceptance.validate_recorded_run(parent) == ()

    _batch(parent, [('submit_proof', {'proof': 'by exact True.intro'})], name='20260902-run')
    assert acceptance.validate_recorded_run(parent) == (f'{parent} holds 2 runs; name one of them',)
