"""Malformed filesystem evidence and last-turn active expiry remain honest."""
import hashlib
import json
import os
from uuid import uuid4

import pytest
from test_t8_staged import _kept
from test_workflow import Terminal, _proposal, _scripted_controller

from hardy.workflows.contracts import RunLimits, RunPhase, TerminalReason
from hardy.workflows.prove import ProveRequest
from hardy.workflows.recorded import validate_recorded_run


@pytest.mark.parametrize('malformed', [True, False, 'timeout'])
def test_last_formalization_turn_expiry_is_not_model_exhaustion(tmp_path, malformed):
    clock = [0.0]
    _, domain, controller, state = _scripted_controller(
        tmp_path, limits=RunLimits(formalization_proposals=1, active_seconds=1),
        monotonic=lambda: clock[0])
    factory = controller._runtime_factory
    def timed(store):
        runtime = factory(store)
        def response(*args, **kwargs):
            clock[0] = 2.0
            if malformed == 'timeout':
                raise TimeoutError('active deadline elapsed')
            if malformed:
                raise ValueError('no structured response')
            return _proposal(domain)
        runtime.run_structured = response
        return runtime
    controller._runtime_factory = timed
    terminal = Terminal()
    manifest = controller.run(ProveRequest(text='Two equals two.', model='fixture'), terminal)
    assert manifest.terminal_reason is TerminalReason.TIMEOUT_BUDGET_EXHAUSTED
    assert manifest.phase is RunPhase.CANCELLED
    assert any('active budget' in gap for gap in manifest.grades.known_gaps)
    assert not terminal.shown
    run = next(tmp_path.rglob('manifest.json')).parent
    events = [json.loads(row) for row in (run / 'trajectory.jsonl').read_text(encoding='utf-8').splitlines()]
    assert not any(event['kind'] == 'formalization.malformed' for event in events)


@pytest.mark.parametrize('relative', ['request.md', 'lean/verification.json', 'lean/Main.lean', 'writeup/paper.tex'])
@pytest.mark.parametrize('kind', ['directory', 'fifo'])
def test_listed_file_replaced_by_nonregular_entry_is_a_finding(tmp_path, relative, kind):
    run = _kept(tmp_path)
    path = run / relative
    path.unlink()
    if kind == 'directory':
        path.mkdir()
    else:
        if not hasattr(os, 'mkfifo'):
            pytest.skip('FIFO unavailable')
        os.mkfifo(path)
    issues = validate_recorded_run(run)
    assert any(relative in issue and 'regular file' in issue for issue in issues)


@pytest.mark.parametrize('relative', ['manifest.json', 'lean/verification.json', 'faithfulness.json', 'writeup/paper.tex'])
def test_non_utf8_text_evidence_is_a_finding(tmp_path, relative):
    run = _kept(tmp_path)
    (run / relative).write_bytes(b'\xff')
    issues = validate_recorded_run(run)
    assert any(relative in issue for issue in issues)


@pytest.mark.parametrize('kind', ['directory_link', 'dangling_link', 'file_link', 'fifo'])
def test_nonregular_inventory_entries_are_refused(tmp_path, kind):
    run = _kept(tmp_path)
    path = run / 'hidden'
    if kind == 'fifo':
        if not hasattr(os, 'mkfifo'):
            pytest.skip('FIFO unavailable')
        os.mkfifo(path)
    else:
        target = tmp_path / 'outside'
        if kind == 'directory_link':
            target.mkdir()
            (target / 'Hidden.lean').write_text('axiom hidden : False', encoding='utf-8')
        elif kind == 'file_link':
            target.write_text('axiom hidden : False', encoding='utf-8')
        try:
            path.symlink_to(target, target_is_directory=kind == 'directory_link')
        except OSError as error:
            pytest.skip(f'symlinks unavailable: {error}')
    issues = validate_recorded_run(run)
    assert any('hidden' in issue and 'regular file' in issue for issue in issues)
    from hardy.workflows.prove import _artifact_hashes

    with pytest.raises(ValueError, match='regular file'):
        _artifact_hashes(run)


@pytest.mark.parametrize('field,value', [
    ('kind', []), ('sequence', 'garbage'), ('run_id', 'different'),
    ('run_id', str(uuid4())), ('sequence', 99), ('phase', 'invented'),
])
def test_invalid_event_envelope_is_a_line_finding(tmp_path, field, value):
    run = _kept(tmp_path)
    trajectory = run / 'trajectory.jsonl'
    rows = trajectory.read_text(encoding='utf-8').splitlines()
    event = json.loads(rows[0])
    event[field] = value
    rows[0] = json.dumps(event)
    trajectory.write_bytes(('\n'.join(rows) + '\n').encode('utf-8'))
    manifest_path = run / 'manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    manifest['artifacts']['trajectory.jsonl'] = hashlib.sha256(trajectory.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
    issues = validate_recorded_run(run)
    assert any('trajectory.jsonl line 1' in issue for issue in issues)


def test_unreadable_artifact_preserves_other_hash_findings(tmp_path, monkeypatch):
    from pathlib import Path

    run = _kept(tmp_path)
    (run / 'faithfulness-prompt.md').write_bytes(b'changed')
    read = Path.read_bytes
    def unreadable(path):
        if path == run / 'request.md':
            raise PermissionError(13, 'read refused', str(path))
        return read(path)
    monkeypatch.setattr(Path, 'read_bytes', unreadable)
    issues = validate_recorded_run(run)
    assert 'hash mismatch: faithfulness-prompt.md' in issues
    assert any('request.md' in issue and 'read refused' in issue for issue in issues)
