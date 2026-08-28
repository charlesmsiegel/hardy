import importlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

DIAGNOSTICS = Path(__file__).parents[1] / 'fixtures' / 'lean' / 'error.jsonl'


def _claim(domain):
    proposal = domain.FormalizationProposal(
        restatement='Two equals two.',
        domains=(),
        quantifiers=(),
        assumptions=(),
        interpretation_choices=(),
        theorem_name='two_eq_two',
        binders='',
        proposition='2 = 2',
    )
    environment = domain.EnvironmentIdentity(
        lean_version='4.32.0',
        lean_commit='8c9756b',
        mathlib_revision='81a5d257',
        lake_manifest_sha256='b' * 64,
        imports=('Mathlib',),
    )
    return domain.FrozenClaim(
        original_text='Two equals two.',
        proposal=proposal,
        environment=environment,
        imports=('Mathlib',),
        approved_at=datetime(2026, 7, 24, tzinfo=UTC),
        content_hash='a' * 64,
    )


def test_render_theorem_uses_only_the_frozen_statement_and_proof_term() -> None:
    domain = importlib.import_module('hardy.domain')
    lean = importlib.import_module('hardy.lean')

    source = lean.render_theorem(_claim(domain), 'by\n  rfl')

    assert source == 'import Mathlib\n\ntheorem two_eq_two : 2 = 2 :=\nby\n  rfl\n'


def test_parse_lean_json_returns_locations_and_open_goals() -> None:
    lean = importlib.import_module('hardy.lean')

    diagnostics, open_goals = lean.parse_lean_json(
        DIAGNOSTICS.read_text(encoding='utf-8')
    )

    assert diagnostics[0].severity == 'error'
    assert diagnostics[0].line == 1
    assert diagnostics[0].column == 15
    assert 'Type mismatch' in diagnostics[0].message
    assert open_goals == ('⊢ True',)


def test_unstructured_lean_output_is_preserved_as_information() -> None:
    lean = importlib.import_module('hardy.lean')

    diagnostics, open_goals = lean.parse_lean_json('native tool message\n')

    assert diagnostics[0].severity == 'information'
    assert diagnostics[0].message == 'native tool message'
    assert open_goals == ()


def test_check_proof_invokes_pinned_lean_with_canonical_source(tmp_path) -> None:
    domain = importlib.import_module('hardy.domain')
    lean = importlib.import_module('hardy.lean')
    process = importlib.import_module('hardy.process')
    lake = tmp_path / 'lake.exe'
    lean_project = tmp_path / 'lean_project'
    lean_project.mkdir()
    observed = {}

    def runner(spec):
        observed['argv'] = spec.argv
        observed['source'] = Path(spec.argv[-1]).read_text(encoding='utf-8')
        return process.ProcessResult(
            argv=spec.argv,
            cwd=spec.cwd,
            returncode=0,
            stdout='',
            stderr='',
            timed_out=False,
            output_overflow=False,
            duration_ms=3,
        )

    service = lean.LeanService(
        lake=lake,
        lean_project=lean_project,
        environment=_claim(domain).environment,
        limits=domain.RunLimits(),
        runner=runner,
    )
    result = service.check_proof(_claim(domain), 'by\n  rfl')

    assert result.success
    assert observed['argv'][1:4] == ('env', 'lean', '--json')
    assert observed['source'].endswith('theorem two_eq_two : 2 = 2 :=\nby\n  rfl\n')
    assert len(result.source_sha256) == 64


def test_scratch_source_is_bounded_before_lean_runs(tmp_path) -> None:
    domain = importlib.import_module('hardy.domain')
    lean = importlib.import_module('hardy.lean')
    service = lean.LeanService(
        lake=tmp_path / 'lake.exe',
        lean_project=tmp_path,
        environment=_claim(domain).environment,
        limits=domain.RunLimits(),
        runner=lambda _: pytest.fail('Lean must not run for oversized scratch source'),
    )

    with pytest.raises(ValueError, match='64 KiB'):
        service.check_scratch('x' * (64 * 1024 + 1))


def test_scratch_check_uses_the_fixed_import(tmp_path) -> None:
    domain = importlib.import_module('hardy.domain')
    lean = importlib.import_module('hardy.lean')
    process = importlib.import_module('hardy.process')
    observed = {}

    def runner(spec):
        observed['source'] = Path(spec.argv[-1]).read_text(encoding='utf-8')
        return process.ProcessResult(
            argv=spec.argv,
            cwd=spec.cwd,
            returncode=0,
            stdout='',
            stderr='',
            timed_out=False,
            output_overflow=False,
            duration_ms=1,
        )

    service = lean.LeanService(
        lake=tmp_path / 'lake.exe',
        lean_project=tmp_path,
        environment=_claim(domain).environment,
        limits=domain.RunLimits(),
        runner=runner,
    )

    result = service.check_scratch('#check Nat.add_comm')

    assert result.success
    assert observed['source'] == 'import Mathlib\n\n#check Nat.add_comm\n'


def test_inspect_declarations_returns_resolved_signatures(tmp_path) -> None:
    domain = importlib.import_module('hardy.domain')
    lean = importlib.import_module('hardy.lean')
    process = importlib.import_module('hardy.process')
    message = json.dumps(
        {
            'data': 'Nat.add_comm (n m : Nat) : n + m = m + n',
            'fileName': 'Inspect.lean',
            'pos': {'line': 1, 'column': 0},
            'severity': 'information',
        }
    )

    def runner(spec):
        return process.ProcessResult(
            argv=spec.argv,
            cwd=spec.cwd,
            returncode=0,
            stdout=message,
            stderr='',
            timed_out=False,
            output_overflow=False,
            duration_ms=1,
        )

    service = lean.LeanService(
        lake=tmp_path / 'lake.exe',
        lean_project=tmp_path,
        environment=_claim(domain).environment,
        limits=domain.RunLimits(),
        runner=runner,
    )

    inspection = service.inspect_declarations(('Nat.add_comm',))

    assert inspection.resolved[0].name == 'Nat.add_comm'
    assert inspection.resolved[0].signature.endswith('n + m = m + n')
    assert inspection.unavailable == ()


def _inspecting_service(tmp_path, runner):
    domain = importlib.import_module('hardy.domain')
    lean = importlib.import_module('hardy.lean')
    return lean.LeanService(
        lake=tmp_path / 'lake.exe',
        lean_project=tmp_path,
        environment=_claim(domain).environment,
        limits=domain.RunLimits(),
        runner=runner,
    )


def test_an_inspection_lean_was_stopped_on_says_so(tmp_path) -> None:
    """Every name came back `unavailable` with nothing to distinguish
    "Lean said no" from "Lean was killed". A live session read the second
    as the first, about `IsCyclic` and `Subgroup.center`."""
    process = importlib.import_module('hardy.process')

    def runner(spec):
        return process.ProcessResult(
            argv=spec.argv, cwd=spec.cwd, returncode=None, stdout='', stderr='',
            timed_out=True, output_overflow=False, duration_ms=180_000,
        )

    inspection = _inspecting_service(tmp_path, runner).inspect_declarations(('IsCyclic',))

    assert inspection.timed_out is True
    assert inspection.success is False
    assert inspection.unavailable == ('IsCyclic',)


def test_an_inspection_that_answered_with_unknown_names_is_a_success(tmp_path) -> None:
    """`#check Nope` is an error to Lean, but the batch *answered*."""
    process = importlib.import_module('hardy.process')
    message = json.dumps({
        'data': "unknown identifier 'Nope'", 'fileName': 'Inspect.lean',
        'pos': {'line': 3, 'column': 7}, 'severity': 'error',
    })

    def runner(spec):
        return process.ProcessResult(
            argv=spec.argv, cwd=spec.cwd, returncode=1, stdout=message, stderr='',
            timed_out=False, output_overflow=False, duration_ms=1,
        )

    inspection = _inspecting_service(tmp_path, runner).inspect_declarations(('Nope',))

    assert inspection.success is True
    assert inspection.timed_out is False
    assert inspection.unavailable == ('Nope',)


def test_an_inspection_that_failed_silently_is_not_a_success(tmp_path) -> None:
    process = importlib.import_module('hardy.process')

    def runner(spec):
        return process.ProcessResult(
            argv=spec.argv, cwd=spec.cwd, returncode=1, stdout='', stderr='crash',
            timed_out=False, output_overflow=False, duration_ms=1,
        )

    inspection = _inspecting_service(tmp_path, runner).inspect_declarations(('Nope',))

    assert inspection.success is False


def test_an_inspection_with_an_error_before_the_check_lines_is_not_a_success(tmp_path) -> None:
    """`import Mathlib` on line 1 failing leaves every name `unavailable`, and
    that error has nothing to do with any of the names asked about -- crediting
    it as an answer is the bug this guards against."""
    process = importlib.import_module('hardy.process')
    message = json.dumps({
        'data': "unknown module Mathlib", 'fileName': 'Inspect.lean',
        'pos': {'line': 1, 'column': 0}, 'severity': 'error',
    })

    def runner(spec):
        return process.ProcessResult(
            argv=spec.argv, cwd=spec.cwd, returncode=1, stdout=message, stderr='',
            timed_out=False, output_overflow=False, duration_ms=1,
        )

    inspection = _inspecting_service(tmp_path, runner).inspect_declarations(('Nope',))

    assert inspection.success is False
    assert inspection.unavailable == ('Nope',)


def test_an_inspection_with_an_error_on_the_check_line_is_a_success(tmp_path) -> None:
    process = importlib.import_module('hardy.process')
    message = json.dumps({
        'data': "unknown identifier 'Nope'", 'fileName': 'Inspect.lean',
        'pos': {'line': 3, 'column': 7}, 'severity': 'error',
    })

    def runner(spec):
        return process.ProcessResult(
            argv=spec.argv, cwd=spec.cwd, returncode=1, stdout=message, stderr='',
            timed_out=False, output_overflow=False, duration_ms=1,
        )

    inspection = _inspecting_service(tmp_path, runner).inspect_declarations(('Nope',))

    assert inspection.success is True


def test_an_inspection_that_overflowed_is_not_a_success(tmp_path) -> None:
    """Whatever diagnostics survived an overflowed process are not the whole
    batch, even if every one that came through landed on a `#check` line."""
    process = importlib.import_module('hardy.process')
    message = json.dumps({
        'data': "unknown identifier 'Nope'", 'fileName': 'Inspect.lean',
        'pos': {'line': 3, 'column': 7}, 'severity': 'error',
    })

    def runner(spec):
        return process.ProcessResult(
            argv=spec.argv, cwd=spec.cwd, returncode=1, stdout=message, stderr='',
            timed_out=False, output_overflow=True, duration_ms=1,
        )

    inspection = _inspecting_service(tmp_path, runner).inspect_declarations(('Nope',))

    assert inspection.success is False


# `search_declarations` tests used to sit here, exercising `#find` through a
# scripted runner. The method is gone -- measured never to answer on the pinned
# toolchain -- and its replacement's contract is covered in
# `test_declarations.py::test_search_result_*`.


def _elaboration(lean, process, messages: tuple[str, ...]):
    return lean.Elaboration(
        process=process,
        diagnostics=tuple(
            lean.LeanDiagnostic(severity='error', message=message) for message in messages
        ),
        open_goals=(),
        source_sha256='c' * 64,
    )


def _finished(process_module):
    return process_module.ProcessResult(
        argv=('lake', 'env', 'lean'),
        cwd=Path('.'),
        returncode=1,
        stdout='',
        stderr='',
        timed_out=False,
        output_overflow=False,
        duration_ms=12,
    )


def test_a_long_lean_observation_still_keeps_its_tail() -> None:
    """The deliberate difference, pinned across the shared truncation helper.

    Lean's end is where the unsolved goal is, so `_observe` keeps the end
    while a file read keeps the top. Routing both through one helper is only
    safe if the helper does not quietly make them agree.
    """
    lean = importlib.import_module('hardy.lean')
    process_module = importlib.import_module('hardy.process')
    tools = lean.LeanTools(
        lean.Request.from_dict(
            {'declaration': 'theorem HardyTarget : True', 'informal_claim': 'True is true.'}
        ),
        ('true',),
        output_limit=400,
    )

    messages = tuple(f'complaint {index} about the goal' for index in range(200))
    result = tools._observe(_elaboration(lean, _finished(process_module), messages), 'source')

    assert result.observation_truncated is True
    assert 'complaint 199 about the goal' in result.output
    assert 'complaint 0 about the goal' not in result.output
    assert len(result.output.encode('utf-8')) <= 400 + len('exit=1 elapsed=0.012s\n')


def test_a_lean_observation_that_fits_is_not_marked_truncated() -> None:
    lean = importlib.import_module('hardy.lean')
    process_module = importlib.import_module('hardy.process')
    tools = lean.LeanTools(
        lean.Request.from_dict(
            {'declaration': 'theorem HardyTarget : True', 'informal_claim': 'True is true.'}
        ),
        ('true',),
    )

    result = tools._observe(_elaboration(lean, _finished(process_module), ('one complaint',)), 'x')

    assert result.observation_truncated is False
    assert 'one complaint' in result.output


def test_a_truncated_lean_observation_does_not_start_mid_line() -> None:
    """A goal state cut in half at the front reads as a goal Lean did not
    state. The character slice this replaced could land anywhere.
    """
    lean = importlib.import_module('hardy.lean')
    process_module = importlib.import_module('hardy.process')
    tools = lean.LeanTools(
        lean.Request.from_dict(
            {'declaration': 'theorem HardyTarget : True', 'informal_claim': 'True is true.'}
        ),
        ('true',),
        output_limit=300,
    )

    messages = tuple(f'complaint {index} about the goal' for index in range(200))
    result = tools._observe(_elaboration(lean, _finished(process_module), messages), 'source')

    body = result.output.split('\n', 1)[1]
    assert all(line.startswith('error: complaint ') for line in body.splitlines())
