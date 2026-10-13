import json

import pytest

from hardy.formal.contracts import Request
from hardy.formal.lean import Elaboration, LeanTools, environment_identity
from hardy.foundation.process import ProcessResult


def result(spec, **kwargs):
    return ProcessResult(argv=spec.argv, cwd=spec.cwd, returncode=0, stdout='', stderr='', timed_out=False, output_overflow=False, duration_ms=1, **kwargs)


def test_timeout_override_is_reported(tmp_path):
    def runner(spec):
        return ProcessResult(argv=spec.argv, cwd=spec.cwd, returncode=None, stdout='', stderr='', timed_out=True, output_overflow=False, duration_ms=240000)
    tools = LeanTools(Request('theorem t : True', 'x'), ('lean',), timeout=30, runner=runner)
    assert tools.run_source('import Mathlib', timeout=240).output.startswith('timeout after 240.0s')
    assert tools.check_proof('by trivial').output.startswith('timeout after 30.0s')
    source = tmp_path / 'Main.lean'
    source.write_text('import Mathlib')
    assert tools.compile_module(tmp_path, tmp_path / 'build', source).output.startswith('timeout after 30.0s')


def test_interrupted_zero_exit_is_not_success(tmp_path):
    process = ProcessResult(argv=('lean',), cwd=tmp_path, returncode=0, stdout='', stderr='', timed_out=False, output_overflow=False, duration_ms=1, interrupted=True)
    assert not Elaboration(process=process, diagnostics=(), open_goals=(), source_sha256='a' * 64).success


@pytest.mark.parametrize('manifest', [{}, [], {'packages': None}, {'packages': [{}]}, {'packages': [{'name': 'batteries', 'rev': 'abc'}]}, {'packages': [{'name': 'mathlib'}]}])
def test_manifest_failure_names_manifest(tmp_path, manifest):
    path = tmp_path / 'lake-manifest.json'
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match='lake-manifest.json'):
        environment_identity(tmp_path)


def test_interrupted_verification_records_stopped_reason(tmp_path):
    from test_verifier import _claim, _store

    from hardy.formal.verifier import FinalVerifier
    from hardy.workflows import contracts, storage

    claim = _claim(contracts)
    store = _store(storage, tmp_path)
    final = FinalVerifier(lake=tmp_path / 'lake', lean_project=tmp_path, environment=claim.environment, limits=contracts.RunLimits(), runner=lambda spec: result(spec, interrupted=True))
    verdict = final.verify(claim, 'by rfl', store)
    assert verdict.reason is contracts.TerminalReason.TIMEOUT_BUDGET_EXHAUSTED
    report = json.loads((store.path / 'lean' / 'verification.json').read_text())
    assert 'interrupted before it finished' in str(report)
    assert 'did not accept' not in str(report)


def test_escaped_sorry_ax_is_a_hole_but_escaped_sorry_is_a_name():
    assert LeanTools.has_holes('by exact «sorryAx» _ true')
    assert LeanTools.holes('by exact «sorryAx» _ true')[0].keyword == 'sorryAx'
    assert not LeanTools.has_holes('by exact «sorry»')
    assert not LeanTools.has_holes('-- «sorryAx»\nexample := "«sorryAx»"')
