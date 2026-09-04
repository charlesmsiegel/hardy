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
    trajectory = json.loads((output / 'trajectory.json').read_text(encoding='utf-8'))
    trajectory['events'].append({'type': 'error', 'error': 'TimeoutError: the run exceeded its budget'})
    (output / 'trajectory.json').write_text(json.dumps(trajectory, indent=2) + '\n', encoding='utf-8')
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


def test_a_discarded_acceptance_cannot_be_the_proof_a_verified_grade_rests_on(tmp_path) -> None:
    """The runner would not have graded a submission it discarded, so a record
    that grades one is inconsistent however sound the axiom line beside it."""
    acceptance = importlib.import_module('hardy.acceptance')
    output = _verified(tmp_path)
    trajectory = json.loads((output / 'trajectory.json').read_text(encoding='utf-8'))
    index = next(
        i for i, event in enumerate(trajectory['events'])
        if event.get('type') == 'tool' and event['name'] == 'submit_proof'
    )
    trajectory['events'].insert(index, {'type': 'discarded', 'name': 'submit_proof', 'why': 'late'})
    (output / 'trajectory.json').write_text(json.dumps(trajectory, indent=2) + '\n', encoding='utf-8')

    issues = acceptance.validate_batch_consistency(output)

    assert any("over proof.lean's own bytes" in issue for issue in issues)


def test_a_staged_manifest_cannot_state_fewer_exchanges_than_the_provider_reported(tmp_path) -> None:
    """The manifest is covered by no hash of its own; the provider's result
    events in the trajectory are what its spend is held to."""
    acceptance = importlib.import_module('hardy.acceptance')
    domain = importlib.import_module('hardy.domain')
    from datetime import UTC, datetime
    from uuid import UUID

    run_dir = tmp_path / 'run'
    run_dir.mkdir()
    (run_dir / 'trajectory.jsonl').write_text(
        '\n'.join(json.dumps({'kind': kind, 'payload': {}}) for kind in ('claude.result', 'claude.result'))
        + '\n',
        encoding='utf-8',
    )
    manifest = domain.RunManifest(
        run_id=UUID(int=1),
        created_at=datetime(2026, 9, 1, tzinfo=UTC),
        phase=domain.RunPhase.CANCELLED,
        model='claude-opus-5',
        prompt_set_sha256='p' * 64,
        usage={'exchanges': 0, 'cost_usd': None, 'input_tokens': None, 'output_tokens': None, 'cache_write_tokens': None, 'cache_read_tokens': None},
    )

    issues = acceptance._live_staged_issues(run_dir, manifest)

    assert any('states 0 exchanges but the trajectory holds 2' in issue for issue in issues)


def test_a_discard_marker_condemns_only_the_submission_it_precedes(tmp_path) -> None:
    """`tool(on-time), discarded, tool(late)` is what the runner writes when a
    valid acceptance is followed by a late one; the valid one still stands."""
    acceptance = importlib.import_module('hardy.acceptance')
    output = _verified(tmp_path)
    trajectory = json.loads((output / 'trajectory.json').read_text(encoding='utf-8'))
    index = next(
        i for i, event in enumerate(trajectory['events'])
        if event.get('type') == 'tool' and event['name'] == 'submit_proof'
    )
    late = dict(trajectory['events'][index])
    trajectory['events'].insert(index + 1, {'type': 'discarded', 'name': 'submit_proof', 'why': 'late'})
    trajectory['events'].insert(index + 2, late)
    (output / 'trajectory.json').write_text(json.dumps(trajectory, indent=2) + '\n', encoding='utf-8')

    assert acceptance.validate_batch_consistency(output) == ()


def test_a_failure_reason_needs_the_event_that_caused_it(tmp_path) -> None:
    """A completed run relabelled as starved would otherwise pass on its
    labels alone, with a trajectory that shows no deadline ever fired."""
    acceptance = importlib.import_module('hardy.acceptance')
    output = _batch(tmp_path, [('check_proof', {'proof': 'by exact True.intro'})])
    for name in ('result.json', 'trajectory.json'):
        _rewrite(output / name, terminal_reason='wall_clock_limit')
    _rewrite(output / 'result.json', turns=None)
    writeup = output / 'writeup.md'
    writeup.write_text(
        writeup.read_text(encoding='utf-8').replace('`no_proof_submitted`', '`wall_clock_limit`'),
        encoding='utf-8',
    )

    issues = acceptance.validate_batch_consistency(output)

    assert any('records no TimeoutError event' in issue for issue in issues)


def _staged_record(tmp_path, kinds_with_phase, log_text: str | None = None):
    domain = importlib.import_module('hardy.domain')
    from datetime import UTC, datetime
    from uuid import UUID

    run_dir = tmp_path / 'run'
    run_dir.mkdir()
    (run_dir / 'trajectory.jsonl').write_text(
        '\n'.join(
            json.dumps({'kind': kind, 'phase': phase, 'payload': {'session_id': session}})
            for kind, phase, session in kinds_with_phase
        )
        + '\n',
        encoding='utf-8',
    )
    if log_text is not None:
        (run_dir / 'writeup').mkdir()
        (run_dir / 'writeup' / 'compile.log').write_text(log_text, encoding='utf-8')
    manifest = domain.RunManifest(
        run_id=UUID(int=1),
        created_at=datetime(2026, 9, 1, tzinfo=UTC),
        phase=domain.RunPhase.CANCELLED,
        model='claude-opus-5',
        prompt_set_sha256='p' * 64,
        grades=domain.Grades(document=domain.DocumentStatus.TEX_COMPILED if log_text is not None else domain.DocumentStatus.NOT_ATTEMPTED),
        usage={'exchanges': 3, 'cost_usd': None, 'input_tokens': None, 'output_tokens': None, 'cache_write_tokens': None, 'cache_read_tokens': None},
    )
    return run_dir, manifest


def test_a_reader_on_the_formalizers_provider_session_is_not_independent(tmp_path) -> None:
    """One session id across the formalizer and the reader leaves open that
    the reader inherited the conversation which wrote the translation."""
    acceptance = importlib.import_module('hardy.acceptance')
    run_dir, manifest = _staged_record(
        tmp_path,
        [('claude.result', 'proving', 'shared'), ('claude.result', 'awaiting_approval', 'shared'), ('claude.result', 'proving', 'shared')],
    )

    issues = acceptance._live_staged_issues(run_dir, manifest)

    assert any('faithfulness reader shares provider session shared' in issue for issue in issues)


def test_a_reader_on_its_own_session_passes(tmp_path) -> None:
    acceptance = importlib.import_module('hardy.acceptance')
    run_dir, manifest = _staged_record(
        tmp_path,
        [('claude.result', 'proving', 'one'), ('claude.result', 'awaiting_approval', 'two'), ('claude.result', 'proving', 'three')],
    )

    issues = acceptance._live_staged_issues(run_dir, manifest)

    assert not any('shares provider session' in issue for issue in issues)


def test_a_compiled_document_that_dropped_glyphs_is_refused_by_the_audit(tmp_path) -> None:
    acceptance = importlib.import_module('hardy.acceptance')
    run_dir, manifest = _staged_record(
        tmp_path,
        [('claude.result', 'proving', 'one')],
        log_text='Missing character: There is no ∀ ("2200) in font ec-lmtt10!\n',
    )

    issues = acceptance._live_staged_issues(run_dir, manifest)

    assert any('dropped characters the font lacked' in issue for issue in issues)


def test_a_compiled_document_that_read_host_files_is_refused_by_the_audit(tmp_path) -> None:
    acceptance = importlib.import_module('hardy.acceptance')
    run_dir, manifest = _staged_record(
        tmp_path,
        [('claude.result', 'proving', 'one')],
        log_text='warning: accessing absolute path `/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf`; build may not be reproducible in other environments\n',
    )

    issues = acceptance._live_staged_issues(run_dir, manifest)

    assert any('read files outside the pinned bundle' in issue for issue in issues)


def test_a_reader_result_without_a_session_is_not_independence_on_record(tmp_path) -> None:
    acceptance = importlib.import_module('hardy.acceptance')
    run_dir, manifest = _staged_record(
        tmp_path,
        [('claude.result', 'proving', 'one'), ('claude.result', 'awaiting_approval', None)],
    )

    issues = acceptance._live_staged_issues(run_dir, manifest)

    assert any("reader's result records no provider session" in issue for issue in issues)


def test_a_credited_review_with_no_reader_result_is_refused(tmp_path) -> None:
    """The comparison of sessions has nothing to compare when the reader left
    no result event, and silence must not pass as independence."""
    acceptance = importlib.import_module('hardy.acceptance')
    domain = importlib.import_module('hardy.domain')

    run_dir, manifest = _staged_record(tmp_path, [('claude.result', 'proving', 'one')])
    review = domain.FaithfulnessVerdict(
        claim_sha256='a' * 64,
        reviewer_model='claude-opus-5',
        prompt_sha256='d' * 64,
        reviewer_backend='claude',
        reviewer_isolation='tools-refused',
        outcome=domain.FaithfulnessOutcome.AGREED,
        review=domain.FaithfulnessReview(
            formalization_entails_claim=True, claim_entails_formalization=True
        ),
    )
    credited = manifest.model_copy(
        update={'grades': domain.Grades(faithfulness=domain.FaithfulnessStatus.USER_APPROVED, faithfulness_review=review)}
    )

    issues = acceptance._live_staged_issues(run_dir, credited)

    assert any('holds no reader result' in issue for issue in issues)


# -- the closer ladder, cross-checked against the events --------------------


def _with_closers(
    tmp_path: Path,
    tactic: str = 'exact True.intro',
    name: str = 'ladder',
    tactics: tuple[str, ...] | None = None,
) -> Path:
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
        lambda model=None, **context: _Runtime([], **context),
        lean,
        output,
        max_turns=3,
        wall_seconds=300.0,
        toolchain=IDENTITY,
        closers=tactics if tactics is not None else (tactic,),
    )
    return output


def test_a_ladder_that_really_ran_is_self_consistent(tmp_path) -> None:
    acceptance = importlib.import_module('hardy.acceptance')

    assert acceptance.validate_batch_consistency(_with_closers(tmp_path)) == ()


def test_a_forged_closed_by_is_refused(tmp_path) -> None:
    """The field exists to say which experimental condition a run was. A field
    nothing cross-checks is a field a record can simply assert."""
    acceptance = importlib.import_module('hardy.acceptance')
    output = _with_closers(tmp_path)
    trajectory = json.loads((output / 'trajectory.json').read_text(encoding='utf-8'))
    trajectory['closers']['closed_by'] = 'omega'
    (output / 'trajectory.json').write_text(json.dumps(trajectory, indent=2) + '\n', encoding='utf-8')

    issues = acceptance.validate_batch_consistency(output)

    assert any('`omega` closed the statement' in issue for issue in issues)


def test_removing_the_ladders_attempts_is_refused(tmp_path) -> None:
    acceptance = importlib.import_module('hardy.acceptance')
    output = _with_closers(tmp_path)
    trajectory = json.loads((output / 'trajectory.json').read_text(encoding='utf-8'))
    trajectory['closers']['attempts'] = []
    (output / 'trajectory.json').write_text(json.dumps(trajectory, indent=2) + '\n', encoding='utf-8')

    issues = acceptance.validate_batch_consistency(output)

    assert any('differs from the event the runner recorded' in issue for issue in issues)


def test_claiming_no_model_was_needed_beside_a_model_exchange_is_refused(tmp_path) -> None:
    acceptance = importlib.import_module('hardy.acceptance')
    output = _with_closers(tmp_path)
    trajectory = json.loads((output / 'trajectory.json').read_text(encoding='utf-8'))
    trajectory['events'].append({'type': 'result', 'turns': 2, 'cost_usd': 0.1, 'usage': None})
    (output / 'trajectory.json').write_text(json.dumps(trajectory, indent=2) + '\n', encoding='utf-8')

    issues = acceptance.validate_batch_consistency(output)

    assert any('ladder closed records a provider exchange' in issue for issue in issues)


def test_deleting_the_decline_does_not_hide_the_model_exchange(tmp_path) -> None:
    """Asked only as "if a turn was declined, does the rest agree", the check
    could be disarmed by deleting the decline itself."""
    acceptance = importlib.import_module('hardy.acceptance')
    output = _with_closers(tmp_path)
    trajectory = json.loads((output / 'trajectory.json').read_text(encoding='utf-8'))
    trajectory['events'] = [
        event for event in trajectory['events'] if event.get('type') != 'declined_turn'
    ]
    trajectory['events'].append({'type': 'result', 'turns': 2, 'cost_usd': 0.1, 'usage': None})
    (output / 'trajectory.json').write_text(json.dumps(trajectory, indent=2) + '\n', encoding='utf-8')

    issues = acceptance.validate_batch_consistency(output)

    assert any('records 0 declined turns' in issue for issue in issues)
    assert any('ladder closed records a provider exchange' in issue for issue in issues)


def test_editing_the_hole_count_out_of_the_writeup_is_refused(tmp_path) -> None:
    """The writeup is the artifact a reader opens, so it is where a partial
    result would most usefully conceal its remaining work."""
    acceptance = importlib.import_module('hardy.acceptance')
    output = _sketched(tmp_path)
    writeup = output / 'writeup.md'
    writeup.write_text(
        writeup.read_text(encoding='utf-8').replace('1 hole(s)', '0 hole(s)'), encoding='utf-8'
    )

    issues = acceptance.validate_batch_consistency(output)

    assert any('sketch section is not the one the record implies' in issue for issue in issues)


def test_a_disabled_ladder_beside_a_ladder_that_ran_is_refused(tmp_path) -> None:
    acceptance = importlib.import_module('hardy.acceptance')
    output = _with_closers(tmp_path)
    trajectory = json.loads((output / 'trajectory.json').read_text(encoding='utf-8'))
    trajectory['closers']['enabled'] = False
    (output / 'trajectory.json').write_text(json.dumps(trajectory, indent=2) + '\n', encoding='utf-8')

    issues = acceptance.validate_batch_consistency(output)

    assert any('disabled beside a' in issue for issue in issues)


def test_a_record_from_before_closers_existed_still_validates(tmp_path) -> None:
    """The runs this audit is written for are kept evidence from paid
    experiments. A cross-check that cannot be made on them is skipped, not
    faked."""
    acceptance = importlib.import_module('hardy.acceptance')
    output = _verified(tmp_path)
    trajectory = json.loads((output / 'trajectory.json').read_text(encoding='utf-8'))
    del trajectory['closers']
    del trajectory['sketch']
    (output / 'trajectory.json').write_text(json.dumps(trajectory, indent=2) + '\n', encoding='utf-8')
    result = json.loads((output / 'result.json').read_text(encoding='utf-8'))
    del result['sketch']
    (output / 'result.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')

    assert acceptance.validate_batch_consistency(output) == ()


# -- the kept sketch, in all three places it appears ------------------------


def _sketched(tmp_path: Path, name: str = 'sketch') -> Path:
    return _batch(tmp_path, [('sketch_proof', {'proof': 'by sorry'})], name=name)


def test_a_kept_sketch_is_self_consistent(tmp_path) -> None:
    acceptance = importlib.import_module('hardy.acceptance')

    assert acceptance.validate_batch_consistency(_sketched(tmp_path)) == ()


def test_editing_the_holes_out_of_one_copy_is_refused(tmp_path) -> None:
    """A partial result is valid only when its remaining holes are explicit,
    so the three copies have to agree with each other and with Lean."""
    acceptance = importlib.import_module('hardy.acceptance')
    output = _sketched(tmp_path)
    result = json.loads((output / 'result.json').read_text(encoding='utf-8'))
    result['sketch']['holes'] = []
    (output / 'result.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')

    issues = acceptance.validate_batch_consistency(output)

    assert any('differs between result.json and trajectory.json' in issue for issue in issues)


def test_a_sketch_lean_never_accepted_is_refused(tmp_path) -> None:
    acceptance = importlib.import_module('hardy.acceptance')
    output = _sketched(tmp_path)
    for name in ('result.json', 'trajectory.json'):
        payload = json.loads((output / name).read_text(encoding='utf-8'))
        payload['sketch']['proof'] = 'by omega'
        (output / name).write_text(json.dumps(payload, indent=2) + '\n', encoding='utf-8')

    issues = acceptance.validate_batch_consistency(output)

    assert any('not the last skeleton Lean accepted' in issue for issue in issues)


def test_a_writeup_with_its_sketch_section_removed_is_refused(tmp_path) -> None:
    acceptance = importlib.import_module('hardy.acceptance')
    output = _sketched(tmp_path)
    writeup = output / 'writeup.md'
    writeup.write_text(
        writeup.read_text(encoding='utf-8').split(importlib.import_module('hardy.runner').SKETCH_HEADING)[0], encoding='utf-8'
    )

    issues = acceptance.validate_batch_consistency(output)

    assert any('does not carry the sketch the record kept' in issue for issue in issues)


def test_a_verified_run_may_not_record_a_sketch(tmp_path) -> None:
    acceptance = importlib.import_module('hardy.acceptance')
    output = _verified(tmp_path)
    _rewrite(output / 'result.json', sketch={'proof': 'by sorry', 'holes': []})
    _rewrite(output / 'trajectory.json', sketch={'proof': 'by sorry', 'holes': []})

    issues = acceptance.validate_batch_consistency(output)

    assert any('verified run records a sketch' in issue for issue in issues)


def test_a_harness_counted_timeout_may_report_its_turns(tmp_path) -> None:
    """The *provider's* count rides on a final result a cancelled run never
    receives. A harness-owned loop counts its own provider calls and publishes
    them however the exchange ended, so refusing a count there would fail every
    truthful API-backed timeout."""
    acceptance = importlib.import_module('hardy.acceptance')
    output = _batch(tmp_path, [('check_proof', {'proof': 'by exact True.intro'})])
    for name in ('result.json', 'trajectory.json'):
        _rewrite(output / name, terminal_reason='wall_clock_limit')
    trajectory = json.loads((output / 'trajectory.json').read_text(encoding='utf-8'))
    trajectory['events'].append({'type': 'error', 'error': 'TimeoutError: the run exceeded its budget'})
    trajectory['limits']['turns_enforced_by'] = 'hardy'
    del trajectory['limits']['note']
    (output / 'trajectory.json').write_text(json.dumps(trajectory, indent=2) + '\n', encoding='utf-8')
    writeup = output / 'writeup.md'
    writeup.write_text(
        writeup.read_text(encoding='utf-8').replace('`no_proof_submitted`', '`wall_clock_limit`'),
        encoding='utf-8',
    )
    _rewrite(output / 'result.json', turns=2)

    assert acceptance.validate_batch_consistency(output) == ()


def test_a_provider_counted_timeout_still_may_not(tmp_path) -> None:
    acceptance = importlib.import_module('hardy.acceptance')
    output = _batch(tmp_path, [('check_proof', {'proof': 'by exact True.intro'})])
    for name in ('result.json', 'trajectory.json'):
        _rewrite(output / name, terminal_reason='wall_clock_limit')
    trajectory = json.loads((output / 'trajectory.json').read_text(encoding='utf-8'))
    trajectory['events'].append({'type': 'error', 'error': 'TimeoutError: the run exceeded its budget'})
    (output / 'trajectory.json').write_text(json.dumps(trajectory, indent=2) + '\n', encoding='utf-8')
    writeup = output / 'writeup.md'
    writeup.write_text(
        writeup.read_text(encoding='utf-8').replace('`no_proof_submitted`', '`wall_clock_limit`'),
        encoding='utf-8',
    )
    _rewrite(output / 'result.json', turns=2)

    issues = acceptance.validate_batch_consistency(output)

    assert any('the provider never delivered' in issue for issue in issues)


def test_a_hole_list_that_agrees_with_itself_but_not_with_the_proof_is_refused(tmp_path) -> None:
    """Edited consistently everywhere -- all three artifacts and the event --
    the copies agree with each other and conceal the hole from every one of
    them. Lean's own rule is the only thing outside that agreement."""
    acceptance = importlib.import_module('hardy.acceptance')
    runner = importlib.import_module('hardy.runner')
    output = _sketched(tmp_path)
    empty = {'proof': 'by sorry', 'holes': []}
    for name in ('result.json', 'trajectory.json'):
        _rewrite(output / name, sketch=empty)
    trajectory = json.loads((output / 'trajectory.json').read_text(encoding='utf-8'))
    for event in trajectory['events']:
        if event.get('name') == 'sketch_proof':
            event['result']['holes'] = []
    (output / 'trajectory.json').write_text(json.dumps(trajectory, indent=2) + '\n', encoding='utf-8')
    writeup = output / 'writeup.md'
    text = writeup.read_text(encoding='utf-8')
    writeup.write_text(text[: text.index(runner.SKETCH_HEADING)] + runner.sketch_section(empty), encoding='utf-8')

    issues = acceptance.validate_batch_consistency(output)

    assert any('not the ones its own skeleton contains' in issue for issue in issues)


def test_a_closer_whose_submission_was_refused_may_not_be_credited(tmp_path) -> None:
    """Matching the submission's text alone let a refused attempt stand behind
    a `closed_by`."""
    acceptance = importlib.import_module('hardy.acceptance')
    output = _with_closers(tmp_path, tactic='nonsense_tactic')
    trajectory = json.loads((output / 'trajectory.json').read_text(encoding='utf-8'))
    # The ladder ran and closed nothing; forge the block to claim it did.
    trajectory['closers']['closed_by'] = 'nonsense_tactic'
    trajectory['closers']['attempts'][0]['ok'] = True
    for event in trajectory['events']:
        if event.get('type') == 'closers':
            event['closed_by'] = 'nonsense_tactic'
            event['attempts'][0]['ok'] = True
    trajectory['events'].append({'type': 'declined_turn', 'why': 'forged'})
    (output / 'trajectory.json').write_text(json.dumps(trajectory, indent=2) + '\n', encoding='utf-8')

    issues = acceptance.validate_batch_consistency(output)

    assert any('no submission that was accepted and kept' in issue for issue in issues)


def test_a_closer_solve_relabelled_as_the_no_closer_condition_is_refused(tmp_path) -> None:
    """Blanking the block and deleting one event is all it took: the disabled
    branch returned before the decline check, leaving the signature of a closer
    solve inside a record certified as the no-closer experimental condition."""
    acceptance = importlib.import_module('hardy.acceptance')
    closers = importlib.import_module('hardy.closers')
    output = _with_closers(tmp_path)
    trajectory = json.loads((output / 'trajectory.json').read_text(encoding='utf-8'))
    trajectory['closers'] = dict(closers.DISABLED)
    trajectory['events'] = [e for e in trajectory['events'] if e.get('type') != 'closers']
    (output / 'trajectory.json').write_text(json.dumps(trajectory, indent=2) + '\n', encoding='utf-8')

    issues = acceptance.validate_batch_consistency(output)

    assert any('declined on a run whose closers are recorded as disabled' in issue for issue in issues)


def _late_sketch(tmp_path: Path, name: str = 'late') -> Path:
    return _batch(tmp_path, [('sketch_proof', {'proof': 'by sorry'})], wall_seconds=0.0001, name=name)


def test_a_run_whose_sketch_was_discarded_is_self_consistent(tmp_path) -> None:
    """The audit must not refuse the honest artifact. A skeleton that
    elaborated after the deadline carries the runner's discard marker and is
    not part of the result; counted as accepted, a truthful timeout was
    rejected for "accepting a sketch no record carries"."""
    acceptance = importlib.import_module('hardy.acceptance')
    output = _late_sketch(tmp_path)

    assert json.loads((output / 'result.json').read_text(encoding='utf-8'))['sketch'] is None
    assert acceptance.validate_batch_consistency(output) == ()


def test_a_sketch_swapped_for_another_skeleton_is_refused(tmp_path) -> None:
    """Every other comparison is the record against itself. The tool result's
    `source` is the one thing in the trajectory that came out of Lean."""
    acceptance = importlib.import_module('hardy.acceptance')
    runner = importlib.import_module('hardy.runner')
    output = _sketched(tmp_path)
    swapped = {'proof': 'by\n  admit', 'holes': [{'keyword': 'admit', 'line': 2, 'column': 2}]}
    for name in ('result.json', 'trajectory.json'):
        _rewrite(output / name, sketch=swapped)
    trajectory = json.loads((output / 'trajectory.json').read_text(encoding='utf-8'))
    for event in trajectory['events']:
        if event.get('name') == 'sketch_proof':
            event['arguments']['proof'] = swapped['proof']
            event['result']['holes'] = swapped['holes']
    (output / 'trajectory.json').write_text(json.dumps(trajectory, indent=2) + '\n', encoding='utf-8')
    writeup = output / 'writeup.md'
    text = writeup.read_text(encoding='utf-8')
    writeup.write_text(
        text[: text.index(runner.SKETCH_HEADING)] + runner.sketch_section(swapped), encoding='utf-8'
    )

    issues = acceptance.validate_batch_consistency(output)

    assert any('not the source Lean was given' in issue for issue in issues)
    assert any('does not hash to the source Lean recorded' in issue for issue in issues)


def test_a_sketch_with_no_recorded_source_is_refused(tmp_path) -> None:
    """Conditional checks let missing evidence pass. A sketch nothing can tie
    to a Lean run is a sketch with no evidence behind it."""
    acceptance = importlib.import_module('hardy.acceptance')
    output = _sketched(tmp_path)
    trajectory = json.loads((output / 'trajectory.json').read_text(encoding='utf-8'))
    for event in trajectory['events']:
        if event.get('name') == 'sketch_proof':
            event['result']['source'] = None
            event['result']['source_sha256'] = None
    (output / 'trajectory.json').write_text(json.dumps(trajectory, indent=2) + '\n', encoding='utf-8')

    issues = acceptance.validate_batch_consistency(output)

    assert any('records no source for Lean to have elaborated' in issue for issue in issues)


def test_a_sketch_whose_request_cannot_be_rebuilt_is_refused(tmp_path) -> None:
    acceptance = importlib.import_module('hardy.acceptance')
    output = _sketched(tmp_path)
    trajectory = json.loads((output / 'trajectory.json').read_text(encoding='utf-8'))
    trajectory['request']['imports'] = []
    (output / 'trajectory.json').write_text(json.dumps(trajectory, indent=2) + '\n', encoding='utf-8')

    issues = acceptance.validate_batch_consistency(output)

    assert any('cannot rebuild the sketch' in issue for issue in issues)


def test_rewriting_a_failed_closer_attempt_is_refused(tmp_path) -> None:
    """Only the tactic that closed the statement was bound to a submission, so
    the names and outputs of the failures could be rewritten together while the
    proofs the run actually submitted stayed where they were."""
    acceptance = importlib.import_module('hardy.acceptance')
    output = _with_closers(tmp_path, tactic='nonsense_tactic')
    trajectory = json.loads((output / 'trajectory.json').read_text(encoding='utf-8'))
    for block in (trajectory['closers'], *[e for e in trajectory['events'] if e.get('type') == 'closers']):
        block['tactics'] = ['omega']
        block['attempts'][0]['tactic'] = 'omega'
    (output / 'trajectory.json').write_text(json.dumps(trajectory, indent=2) + '\n', encoding='utf-8')

    issues = acceptance.validate_batch_consistency(output)

    assert any('does not match the proof submitted for it' in issue for issue in issues)


def test_deleting_the_sketch_fields_does_not_buy_the_legacy_exception(tmp_path) -> None:
    """A trajectory holding an accepted sketch is a record from this code whose
    fields have been removed. Taking the compatibility exception there let the
    human-facing artifact drop every remaining hole."""
    acceptance = importlib.import_module('hardy.acceptance')
    runner = importlib.import_module('hardy.runner')
    output = _sketched(tmp_path)
    for name in ('result.json', 'trajectory.json'):
        payload = json.loads((output / name).read_text(encoding='utf-8'))
        del payload['sketch']
        (output / name).write_text(json.dumps(payload, indent=2) + '\n', encoding='utf-8')
    writeup = output / 'writeup.md'
    text = writeup.read_text(encoding='utf-8')
    writeup.write_text(text[: text.index(runner.SKETCH_HEADING)], encoding='utf-8')

    issues = acceptance.validate_batch_consistency(output)

    assert any('carries no sketch fields' in issue for issue in issues)


def test_dropping_a_trailing_closer_attempt_is_refused(tmp_path) -> None:
    """The forward checks bind each claimed attempt to a submission; they say
    nothing about a submission no attempt claims. So a seven-tactic ladder
    could be recertified as the cheaper three-tactic condition by deleting the
    trailing attempts from the block and its duplicated event together, with
    the elaborations the run actually paid for still sitting in the events."""
    acceptance = importlib.import_module('hardy.acceptance')
    output = _with_closers(tmp_path, tactics=('nonsense_tactic', 'other_nonsense'))
    trajectory = json.loads((output / 'trajectory.json').read_text(encoding='utf-8'))
    blocks = [trajectory['closers'], *[e for e in trajectory['events'] if e.get('type') == 'closers']]
    assert len(blocks[0]['attempts']) == 2
    for block in blocks:
        block['tactics'] = block['tactics'][:1]
        block['attempts'] = block['attempts'][:1]
    (output / 'trajectory.json').write_text(json.dumps(trajectory, indent=2) + '\n', encoding='utf-8')

    issues = acceptance.validate_batch_consistency(output)

    assert any('proofs were submitted before the closers event' in issue for issue in issues)


def test_rewriting_a_closer_diagnostic_is_refused(tmp_path) -> None:
    """`ok` alone left Lean's own words free.

    The tactic name and the verdict were bound to the submission behind them;
    the `output` beside them was not, so the diagnostic could be rewritten in
    the block and its duplicated event together while the `submit_proof` that
    produced it kept the real one -- a record saying a tactic failed for a
    reason Lean never gave."""
    acceptance = importlib.import_module('hardy.acceptance')
    output = _with_closers(tmp_path, tactic='nonsense_tactic')
    trajectory = json.loads((output / 'trajectory.json').read_text(encoding='utf-8'))
    blocks = [trajectory['closers'], *[e for e in trajectory['events'] if e.get('type') == 'closers']]
    for block in blocks:
        block['attempts'][0]['output'] = 'unknown tactic; try `omega` instead'
    (output / 'trajectory.json').write_text(json.dumps(trajectory, indent=2) + '\n', encoding='utf-8')

    issues = acceptance.validate_batch_consistency(output)

    assert any('reports output its submission did not produce' in issue for issue in issues)


def test_a_closer_that_landed_late_is_not_a_record_at_odds_with_itself(tmp_path) -> None:
    """A tactic whose check began inside the deadline and finished outside it.

    `runner.submit` reports it as having failed, because the run did not keep
    the proof -- while the `submit_proof` event beside it still carries Lean's
    own `ok: true` behind the runner's discard marker. Compared against that
    raw flag rather than against whether the submission was kept, the checks
    refused an honest `wall_clock_limit` artifact as inconsistent with itself.
    """
    import time

    acceptance = importlib.import_module('hardy.acceptance')
    models = importlib.import_module('hardy.models')
    lean_module = importlib.import_module('hardy.lean')
    runner = importlib.import_module('hardy.runner')
    request = models.Request.from_dict(
        {'declaration': 'theorem HardyTarget : True', 'informal_claim': 'True is true.'}
    )

    class Slow(lean_module.LeanTools):
        def check_proof(self, proof, *, final=False):
            # Long enough that the check starts inside the budget below and
            # cannot possibly finish inside it.
            time.sleep(0.6)
            return super().check_proof(proof, final=final)

    class Unasked(_Runtime):
        # The ladder uses the whole budget here, so the runtime is never asked
        # anything. `_Runtime` declares two turns whatever happens; a real one
        # that was never asked has no count to give, and on a backend whose SDK
        # owns the loop the count rides on a final result that never arrives.
        turns = None

    lean = Slow(request, (sys.executable, str(FAKE_LEAN)))
    output = tmp_path / 'late-closer'
    runner.run(
        request,
        lambda model=None, **context: Unasked([], **context),
        lean,
        output,
        max_turns=3,
        wall_seconds=0.3,
        toolchain=IDENTITY,
        closers=('exact True.intro',),
    )

    trajectory = json.loads((output / 'trajectory.json').read_text(encoding='utf-8'))
    # The run really did produce the shape this is about: an attempt the ladder
    # reports as failed, beside a submission Lean accepted and the deadline
    # discarded. Asserted rather than assumed, so a change that stopped
    # producing it would fail here instead of quietly passing the check below.
    assert trajectory['closers']['attempts'] == [
        {'tactic': 'exact True.intro', 'ok': False, 'output': trajectory['closers']['attempts'][0]['output']}
    ]
    assert trajectory['closers']['closed_by'] is None
    submissions = [
        (index, event) for index, event in enumerate(trajectory['events'])
        if event.get('type') == 'tool' and event.get('name') == 'submit_proof'
    ]
    assert len(submissions) == 1
    index, event = submissions[0]
    assert event['result']['ok'] is True
    assert trajectory['events'][index - 1]['type'] == 'discarded'

    assert acceptance.validate_batch_consistency(output) == ()


def test_a_malformed_sketch_is_a_finding_rather_than_a_crash(tmp_path) -> None:
    """`sketch_section` indexes `proof` and reads `keyword` and `line` off every
    hole, so a truncated or hand-edited record took the audit down with a
    TypeError two comparisons later. "This artifact is invalid" is the finding;
    a crash is the one answer a validator may not give."""
    acceptance = importlib.import_module('hardy.acceptance')
    output = _sketched(tmp_path)
    for name in ('result.json', 'trajectory.json'):
        payload = json.loads((output / name).read_text(encoding='utf-8'))
        payload['sketch']['holes'] = 'one sorry'
        (output / name).write_text(json.dumps(payload, indent=2) + '\n', encoding='utf-8')

    issues = acceptance.validate_batch_consistency(output)

    assert any('not shaped like one' in issue for issue in issues)


def test_a_sketch_with_no_proof_is_refused_the_same_way(tmp_path) -> None:
    acceptance = importlib.import_module('hardy.acceptance')
    output = _sketched(tmp_path)
    for name in ('result.json', 'trajectory.json'):
        payload = json.loads((output / name).read_text(encoding='utf-8'))
        del payload['sketch']['proof']
        (output / name).write_text(json.dumps(payload, indent=2) + '\n', encoding='utf-8')

    issues = acceptance.validate_batch_consistency(output)

    assert any('not shaped like one' in issue for issue in issues)


def test_a_run_that_asked_no_provider_records_zero_turns(tmp_path) -> None:
    """Hardy knows exactly how many provider turns a ladder-only run took, and
    it is zero. `None` means "nobody said", which is honest only when a
    provider was asked and did not report -- so reading it off a runtime that
    was never built turned a measurement into an unknown, and a turn-based
    comparison could not use the run at all."""
    acceptance = importlib.import_module('hardy.acceptance')
    output = _with_closers(tmp_path, tactic='exact True.intro', name='ladder-only')

    trajectory = json.loads((output / 'trajectory.json').read_text(encoding='utf-8'))
    result = json.loads((output / 'result.json').read_text(encoding='utf-8'))
    assert trajectory['closers']['closed_by'] == 'exact True.intro'
    assert result['turns'] == 0
    assert trajectory['closers']['enabled'] is True
    assert acceptance.validate_batch_consistency(output) == ()

    # And the record may not say anything else. A count beside a run that asked
    # nothing is a count nobody measured.
    result['turns'] = 2
    (output / 'result.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    assert any(
        'asked no provider anything' in issue or 'turn count' in issue
        for issue in acceptance.validate_batch_consistency(output)
    )


def test_a_zero_budget_run_does_not_blame_closers_that_never_ran(tmp_path) -> None:
    """`--wall-seconds 0` with no ladder reached the same branch, and the
    record then said the closers had used the whole budget beside a `closers`
    block saying they were disabled -- a false sentence, and one the audit
    reads as evidence that the provider was deliberately unasked."""
    acceptance = importlib.import_module('hardy.acceptance')
    models = importlib.import_module('hardy.models')
    lean_module = importlib.import_module('hardy.lean')
    runner = importlib.import_module('hardy.runner')
    request = models.Request.from_dict(
        {'declaration': 'theorem HardyTarget : True', 'informal_claim': 'True is true.'}
    )

    class Unasked(_Runtime):
        turns = None

    output = tmp_path / 'no-budget'
    runner.run(
        request,
        lambda model=None, **context: Unasked([], **context),
        lean_module.LeanTools(request, (sys.executable, str(FAKE_LEAN))),
        output,
        max_turns=3,
        wall_seconds=0.0,
        toolchain=IDENTITY,
    )

    trajectory = json.loads((output / 'trajectory.json').read_text(encoding='utf-8'))
    limits = [event for event in trajectory['events'] if event.get('type') == 'limit']
    assert len(limits) == 1
    assert 'closers' not in limits[0]['detail']
    assert trajectory['closers']['enabled'] is False
    # And it is still a run that asked nothing, so it still reports zero turns
    # and still validates.
    assert json.loads((output / 'result.json').read_text(encoding='utf-8'))['turns'] == 0
    assert acceptance.validate_batch_consistency(output) == ()


def test_a_ladder_that_kept_going_past_a_success_is_refused(tmp_path) -> None:
    """`closers.close` returns on the first submission the run keeps, so
    exactly one attempt succeeds and it is the last. Any other arrangement is a
    record no run could have produced, and a hand-edited or merged trajectory
    could otherwise certify a ladder order and a cost that never happened."""
    acceptance = importlib.import_module('hardy.acceptance')
    output = _with_closers(tmp_path, tactics=('nonsense_tactic', 'exact True.intro'))
    trajectory = json.loads((output / 'trajectory.json').read_text(encoding='utf-8'))
    blocks = [trajectory['closers'], *[e for e in trajectory['events'] if e.get('type') == 'closers']]
    for block in blocks:
        # The successful attempt moved ahead of the failed one, as a record
        # claiming a cheaper ladder would.
        block['tactics'] = list(reversed(block['tactics']))
        block['attempts'] = list(reversed(block['attempts']))
    (output / 'trajectory.json').write_text(json.dumps(trajectory, indent=2) + '\n', encoding='utf-8')

    issues = acceptance.validate_batch_consistency(output)

    assert any('went on past' in issue for issue in issues)


def test_a_batch_run_records_the_window_it_was_planned_against(tmp_path) -> None:
    """The window is a setting the interactive surface honoured and this one
    did not: a batch aimed at a smaller gateway kept appending messages until
    the endpoint refused, and its trajectory did not even say which window had
    shaped the experiment."""
    acceptance = importlib.import_module('hardy.acceptance')
    output = _batch(tmp_path, [], name='windowed')

    trajectory = json.loads((output / 'trajectory.json').read_text(encoding='utf-8'))
    limits = trajectory['limits']
    assert limits['context_window'] == importlib.import_module('hardy.compaction').CONTEXT_WINDOW
    # And who did the compacting, in the same terms `turns_enforced_by` uses:
    # a backend whose SDK owns the loop has nowhere to put a compactor, and
    # the record says that rather than leaving it to be assumed.
    assert limits['compacted_by'] == 'nobody: the SDK owns the loop'
    assert acceptance.validate_batch_consistency(output) == ()


def test_a_batch_on_a_loop_hardy_owns_is_given_the_compactor(tmp_path) -> None:
    runner = importlib.import_module('hardy.runner')
    models = importlib.import_module('hardy.models')
    lean_module = importlib.import_module('hardy.lean')
    request = models.Request.from_dict(
        {'declaration': 'theorem HardyTarget : True', 'informal_claim': 'True is true.'}
    )
    offered = []

    class Compacting(_Runtime):
        def attach_compactor(self, compact):
            offered.append(compact)

    output = tmp_path / 'compacting'
    runner.run(
        request,
        lambda model=None, **context: Compacting([], **context),
        lean_module.LeanTools(request, (sys.executable, str(FAKE_LEAN))),
        output,
        max_turns=3,
        wall_seconds=300.0,
        toolchain=IDENTITY,
    )

    assert len(offered) == 1
    trajectory = json.loads((output / 'trajectory.json').read_text(encoding='utf-8'))
    assert trajectory['limits']['compacted_by'] == 'hardy'


def test_the_batch_compactor_summarises_what_the_run_knows(tmp_path) -> None:
    """The same rule and the same cut as the interactive one, on a narrower set
    of facts: an unattended run has no naming registry and no approved
    assumptions, but it has the claim it was given and every failed attempt in
    Lean's own words."""
    compaction = importlib.import_module('hardy.compaction')
    runner = importlib.import_module('hardy.runner')
    models = importlib.import_module('hardy.models')
    lean_module = importlib.import_module('hardy.lean')
    request = models.Request.from_dict(
        {'declaration': 'theorem HardyTarget : True', 'informal_claim': 'True is true.'}
    )
    held = []

    class Compacting(_Runtime):
        def attach_compactor(self, compact):
            held.append(compact)

    runner.run(
        request,
        lambda model=None, **context: Compacting([('check_proof', {'proof': 'by nonsense'})], **context),
        lean_module.LeanTools(request, (sys.executable, str(FAKE_LEAN))),
        tmp_path / 'summarised',
        max_turns=3,
        wall_seconds=300.0,
        toolchain=IDENTITY,
        context_window=20_000,
    )

    compact = held[0]
    # A conversation far past a 20,000-token window, in a form the cut is legal
    # anywhere in.
    conversation = [compaction.Message('user', text='x' * 30_000) for _ in range(4)]
    rebuilt = compact(conversation)

    assert rebuilt is not None
    assert rebuilt[0].text.startswith(compaction.PREAMBLE)
    assert 'True is true.' in rebuilt[0].text
    # And the failed check reaches it, in Lean's words rather than a narration.
    assert 'check_proof' in rebuilt[0].text
    # A short conversation needs none of this.
    assert compact([compaction.Message('user', text='short')]) is None
