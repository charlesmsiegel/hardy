import importlib
import json
from datetime import datetime, timezone
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
        approved_at=datetime(2026, 7, 24, tzinfo=timezone.utc),
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


def test_search_declarations_bounds_and_structures_find_results(tmp_path) -> None:
    domain = importlib.import_module('hardy.domain')
    lean = importlib.import_module('hardy.lean')
    process = importlib.import_module('hardy.process')
    messages = '\n'.join(
        json.dumps({'data': text, 'severity': 'information'})
        for text in (
            'Nat.add_comm (n m : Nat) : n + m = m + n',
            'Nat.mul_comm (n m : Nat) : n * m = m * n',
        )
    )

    def runner(spec):
        return process.ProcessResult(
            argv=spec.argv,
            cwd=spec.cwd,
            returncode=0,
            stdout=messages,
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

    search = service.search_declarations('_ + _ = _ + _', limit=1)

    assert search.query == '_ + _ = _ + _'
    assert [result.name for result in search.results] == ['Nat.add_comm']
    assert search.truncated


def test_search_reports_lean_timeout_instead_of_claiming_no_results(tmp_path) -> None:
    domain = importlib.import_module('hardy.domain')
    lean = importlib.import_module('hardy.lean')
    process = importlib.import_module('hardy.process')

    def runner(spec):
        return process.ProcessResult(
            argv=spec.argv,
            cwd=spec.cwd,
            returncode=None,
            stdout='',
            stderr='',
            timed_out=True,
            output_overflow=False,
            duration_ms=30_000,
        )

    service = lean.LeanService(
        lake=tmp_path / 'lake.exe',
        lean_project=tmp_path,
        environment=_claim(domain).environment,
        limits=domain.RunLimits(),
        runner=runner,
    )

    search = service.search_declarations('_ + _ = _ + _', limit=3)

    assert not search.success
    assert search.timed_out
    assert search.results == ()
