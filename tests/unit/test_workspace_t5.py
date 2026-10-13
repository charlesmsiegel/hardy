from pathlib import PurePosixPath

import pytest

from hardy.formal.workspace import LeanWorkspace
from hardy.foundation.files import LayoutError


def space(tmp_path, external=None):
    root = tmp_path / 'lean'
    root.mkdir()
    return LeanWorkspace(root, tmp_path / 'build', lambda *args: (True, ''), external=external)


def test_failed_stage_removes_scratch(tmp_path, monkeypatch):
    scratch = tmp_path / 'scratch'
    scratch.mkdir()
    monkeypatch.setattr('tempfile.tempdir', str(scratch))
    ws = space(tmp_path)
    with pytest.raises(LayoutError):
        ws.stage(PurePosixPath('../Main.lean'), 'def a := 1')
    assert list(scratch.iterdir()) == []


def test_external_stamp_once_per_signature_snapshot(tmp_path):
    calls = []
    ws = space(tmp_path, lambda name: calls.append(name) or 'stamp')
    for name in ('A', 'B', 'C'):
        (ws.root / f'{name}.lean').write_text('import Mathlib\ndef a := 1\n')
    first = ws.current_signatures()
    assert calls == ['Mathlib']
    calls.clear()
    assert ws.current_signatures() == first
    assert calls == ['Mathlib']


def test_prune_batches_index_write(tmp_path, monkeypatch):
    ws = space(tmp_path)
    ws.build.mkdir()
    for name in ('A', 'B', 'C'):
        ws.olean(name).write_bytes(b'olean')
    ws._write_index(dict.fromkeys(('A', 'B', 'C', 'D'), 'stamp'))
    writes = []
    original = ws._write_index
    monkeypatch.setattr(ws, '_write_index', lambda value: (writes.append(dict(value)), original(value)))
    assert ws.prune_orphans() == ('A', 'B', 'C', 'D')
    assert writes == [{}]


def test_locked_build_refuses_before_source_publish(tmp_path, monkeypatch):
    ws = space(tmp_path)
    (ws.root / 'Main.lean').write_text('def a := 1\n')
    ws.build.mkdir()
    ws.olean('Main').write_bytes(b'old')
    shadow, commit = ws.stage(PurePosixPath('Main.lean'), 'def a := 2\n')
    original = __import__('os').replace
    def locked(source, target):
        if str(source) == str(ws.build):
            raise PermissionError('build is in use')
        return original(source, target)
    monkeypatch.setattr('os.replace', locked)
    try:
        with pytest.raises(PermissionError, match='build is in use'):
            commit()
        assert (ws.root / 'Main.lean').read_text() == 'def a := 1\n'
        assert ws.olean('Main').read_bytes() == b'old'
    finally:
        ws.discard(shadow)


def test_build_parses_each_import_header_once(tmp_path, monkeypatch):
    import hardy.formal.syntax.imports as imports
    ws = space(tmp_path)
    (ws.root / 'A.lean').write_text('import Mathlib\ndef a := 1\n')
    (ws.root / 'B.lean').write_text('import A Mathlib\ndef b := a\n')
    calls = []
    original = imports.parse_imports
    monkeypatch.setattr(imports, 'parse_imports', lambda source: (calls.append(source), original(source))[1])
    assert ws.build_modules(['B']) is None
    assert len(calls) == 2


def test_build_swap_rolls_back_on_source_write_failure(tmp_path, monkeypatch):
    from hardy.foundation.files import WriteGuard
    ws = space(tmp_path)
    (ws.root / 'Main.lean').write_text('def a := 1\n')
    ws.build.mkdir()
    ws.olean('Main').write_bytes(b'old')
    shadow, commit = ws.stage(PurePosixPath('Main.lean'), 'def a := 2\n')
    shadow.olean('Main').write_bytes(b'new')
    monkeypatch.setattr(WriteGuard, 'write_text', lambda *a, **kw: (_ for _ in ()).throw(OSError('write refused')))
    try:
        with pytest.raises(OSError, match='write refused'):
            commit()
        assert (ws.root / 'Main.lean').read_text() == 'def a := 1\n'
        assert ws.olean('Main').read_bytes() == b'old'
    finally:
        ws.discard(shadow)


@pytest.mark.skipif(__import__('sys').platform != 'win32', reason='native Windows sharing semantics')
def test_native_windows_open_olean_retains_consistent_commit(tmp_path):
    ws = space(tmp_path)
    (ws.root / 'Main.lean').write_text('def a := 1\n')
    ws.build.mkdir()
    ws.olean('Main').write_bytes(b'old')
    shadow, commit = ws.stage(PurePosixPath('Main.lean'), 'def a := 2\n')
    shadow.olean('Main').write_bytes(b'new')
    try:
        with ws.olean('Main').open('rb') as held:
            try:
                commit()
            except PermissionError:
                assert (ws.root / 'Main.lean').read_text() == 'def a := 1\n'
                assert ws.olean('Main').read_bytes() == b'old'
            else:
                assert (ws.root / 'Main.lean').read_text() == 'def a := 2\n'
                assert ws.olean('Main').read_bytes() == b'new'
            assert held.read() == b'old'
    finally:
        ws.discard(shadow)


def test_admission_locked_build_keeps_sources(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from hardy.workflows.delegation.admission import AuthoritativeAdmission
    ws = space(tmp_path)
    (ws.root / 'Main.lean').write_text('old')
    ws.build.mkdir()
    ws.olean('Main').write_bytes(b'old')
    shadow, _ = ws.stage(PurePosixPath('Main.lean'), 'new')
    admission = object.__new__(AuthoritativeAdmission)
    admission.workspace = ws
    plan = SimpleNamespace(files=(SimpleNamespace(path='Main.lean', operation='modify', content='new'),))
    original = __import__('os').replace
    def locked(source, target):
        if str(source) == str(ws.build):
            raise PermissionError('build is in use')
        return original(source, target)
    monkeypatch.setattr('os.replace', locked)
    try:
        with pytest.raises(PermissionError):
            admission._commit_files(shadow, plan)
        assert (ws.root / 'Main.lean').read_text() == 'old'
        assert ws.olean('Main').read_bytes() == b'old'
    finally:
        ws.discard(shadow)


def test_promotion_locked_build_keeps_realizations_and_sources(tmp_path, monkeypatch):
    from test_shared_promotion import AUDIT, SOURCES, setup
    root, claims, claim, promoter, request, compiled = setup(tmp_path)
    promoter.shared_build.mkdir(parents=True)
    artifact = promoter.shared_build / 'Old.olean'
    artifact.write_bytes(b'old')
    prepared = promoter.prepare(request, SOURCES, AUDIT)
    original = __import__('os').replace
    def locked(source, target):
        if str(source) == str(promoter.shared_build):
            raise PermissionError('build is in use')
        return original(source, target)
    monkeypatch.setattr('os.replace', locked)
    outcome = promoter.promote(prepared.id, request, SOURCES)
    assert outcome.status == 'failed'
    assert 'build is in use' in outcome.blockers[-1].detail
    assert not list(promoter.shared_root.rglob('*.lean'))
    assert artifact.read_bytes() == b'old'
    assert promoter.realizations.for_claim(claim.id) == ()


def test_delete_imported_overlay_module_refuses_importer(tmp_path):
    from hardy.workflows.delegation.workspace import WorkspaceOverlay
    ws = space(tmp_path)
    (ws.root / 'B.lean').write_text('def b := 1\n')
    (ws.root / 'A.lean').write_text('import B\ndef a := b\n')
    def compile(module, root, build, file):
        if module == 'A' and not (root / 'B.lean').exists():
            return False, 'missing import B'
        return True, ''
    ws._compile = compile
    overlay = object.__new__(WorkspaceOverlay)
    overlay.workspace = ws
    failure = overlay.delete(PurePosixPath('B.lean'))
    assert failure.module == 'A'
    assert (ws.root / 'B.lean').exists()


def test_open_sweeps_only_marked_retired_mirrors(tmp_path):
    for name, retired in (('.build.swap-retired', True), ('.build.swap-recovery', False)):
        holder = tmp_path / name
        (holder / 'old').mkdir(parents=True)
        (holder / 'old' / 'Main.olean').write_bytes(b'old')
        if retired:
            (holder / 'retired').write_bytes(b'retired')
    space(tmp_path)
    assert not (tmp_path / '.build.swap-retired').exists()
    assert (tmp_path / '.build.swap-recovery' / 'old' / 'Main.olean').read_bytes() == b'old'


def test_admission_partial_source_write_restores_previous_files(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from hardy.foundation.files import WriteGuard
    from hardy.workflows.delegation.admission import AuthoritativeAdmission
    ws = space(tmp_path)
    for name in ('A', 'B'):
        (ws.root / f'{name}.lean').write_text('old')
    ws.build.mkdir()
    ws.olean('A').write_bytes(b'old')
    shadow, _ = ws.stage(PurePosixPath('A.lean'), 'new')
    shadow.olean('A').write_bytes(b'new')
    admission = object.__new__(AuthoritativeAdmission)
    admission.workspace = ws
    plan = SimpleNamespace(files=tuple(SimpleNamespace(path=f'{n}.lean', operation='modify', content='new') for n in ('A', 'B')))
    original = WriteGuard.write_text
    def refused(guard, name, content, **kwargs):
        if str(name) == 'B.lean' and content == 'new':
            raise OSError('second write refused')
        return original(guard, name, content, **kwargs)
    monkeypatch.setattr(WriteGuard, 'write_text', refused)
    try:
        with pytest.raises(OSError, match='second write refused'):
            admission._commit_files(shadow, plan)
        assert [(ws.root / f'{n}.lean').read_text() for n in ('A', 'B')] == ['old', 'old']
        assert ws.olean('A').read_bytes() == b'old'
    finally:
        ws.discard(shadow)


def test_retired_locked_mirror_cleanup_does_not_fail_commit(tmp_path, monkeypatch):
    ws = space(tmp_path)
    (ws.root / 'Main.lean').write_text('old')
    ws.build.mkdir()
    ws.olean('Main').write_bytes(b'old')
    shadow, commit = ws.stage(PurePosixPath('Main.lean'), 'new')
    shadow.olean('Main').write_bytes(b'new')
    original = __import__('shutil').rmtree
    def refused(path, **kwargs):
        if '.swap-' in str(path):
            raise PermissionError('retired olean in use')
        return original(path, **kwargs)
    monkeypatch.setattr('shutil.rmtree', refused)
    try:
        commit()
        assert (ws.root / 'Main.lean').read_text() == 'new'
        assert ws.olean('Main').read_bytes() == b'new'
        holders = list(ws.build.parent.glob('.build.swap-*'))
        assert len(holders) == 1
        assert (holders[0] / 'old' / 'Main.olean').read_bytes() == b'old'
        assert (holders[0] / 'retired').is_file()
    finally:
        ws.discard(shadow)


def test_refresh_refuses_deleted_module_newly_imported_by_base(tmp_path):
    from hardy.workflows.delegation.workspace import WorkspaceOverlay
    ws = space(tmp_path)
    (ws.root / 'B.lean').write_text('def b := 1\n')
    def compile(module, root, build, file):
        if module == 'A' and not (root / 'B.lean').exists():
            return False, 'missing import B'
        return True, ''
    ws._compile = compile
    overlay = WorkspaceOverlay.snapshot(ws, delegation_id='worker', base_revision=1, root=tmp_path / 'overlays')
    assert overlay.delete(PurePosixPath('B.lean')) is None
    before = set((tmp_path / 'overlays').iterdir())
    (ws.root / 'A.lean').write_text('import B\ndef a := b\n')
    with pytest.raises(ValueError, match='A.*missing import B'):
        overlay.refresh(ws, base_revision=2)
    assert overlay.workspace.sources() == {}
    assert set((tmp_path / 'overlays').iterdir()) == before
    assert set(ws.sources()) == {'A', 'B'}


def test_refresh_applies_deletions_as_one_batch(tmp_path):
    from hardy.workflows.delegation.workspace import WorkspaceOverlay
    ws = space(tmp_path)
    (ws.root / 'B.lean').write_text('def b := 1\n')
    (ws.root / 'Z.lean').write_text('import B\ndef z := b\n')
    def compile(module, root, build, file):
        if module == 'Z' and not (root / 'B.lean').exists():
            return False, 'missing import B'
        return True, ''
    ws._compile = compile
    overlay = WorkspaceOverlay.snapshot(ws, delegation_id='worker', base_revision=1, root=tmp_path / 'overlays')
    assert overlay.delete(PurePosixPath('Z.lean')) is None
    assert overlay.delete(PurePosixPath('B.lean')) is None
    refreshed = overlay.refresh(ws, base_revision=2)
    assert refreshed.workspace.sources() == {}
    assert [change.path for change in refreshed.change_set().files] == ['B.lean', 'Z.lean']


@pytest.mark.skipif(__import__('sys').platform != 'win32', reason='native Windows sharing semantics')
def test_native_windows_mapped_olean_admission_is_consistent(tmp_path):
    import mmap
    from types import SimpleNamespace

    from hardy.workflows.delegation.admission import AuthoritativeAdmission
    ws = space(tmp_path)
    (ws.root / 'Main.lean').write_text('old')
    ws.build.mkdir()
    ws.olean('Main').write_bytes(b'old')
    shadow, _ = ws.stage(PurePosixPath('Main.lean'), 'new')
    shadow.olean('Main').write_bytes(b'new')
    admission = object.__new__(AuthoritativeAdmission)
    admission.workspace = ws
    plan = SimpleNamespace(files=(SimpleNamespace(path='Main.lean', operation='modify', content='new'),))
    try:
        with ws.olean('Main').open('rb') as held, mmap.mmap(held.fileno(), 0, access=mmap.ACCESS_READ) as mapped:
            try:
                admission._commit_files(shadow, plan)
            except PermissionError:
                assert (ws.root / 'Main.lean').read_text() == 'old'
                assert ws.olean('Main').read_bytes() == b'old'
            else:
                assert (ws.root / 'Main.lean').read_text() == 'new'
                assert ws.olean('Main').read_bytes() == b'new'
            assert mapped[:] == b'old'
    finally:
        ws.discard(shadow)


@pytest.mark.skipif(__import__('sys').platform != 'win32', reason='native Windows sharing semantics')
@pytest.mark.parametrize('refuse_attachment', (False, True), ids=('promotion', 'restore'))
def test_native_windows_mapped_olean_shared_promotion_and_restore(tmp_path, refuse_attachment):
    import mmap

    from test_shared_promotion import AUDIT, SOURCES, setup

    from hardy.workflows.shared.realizations import RealizationError
    root, claims, claim, promoter, request, compiled = setup(tmp_path)
    promoter.shared_root.mkdir(parents=True)
    old_source = promoter.shared_root / 'Old.lean'
    old_source.write_text('theorem old : True := trivial\n')
    promoter.shared_build.mkdir(parents=True)
    artifact = promoter.shared_build / 'Old.olean'
    artifact.write_bytes(b'old')
    prepared = promoter.prepare(request, SOURCES, AUDIT)
    if refuse_attachment:
        def refuse(*args, **kwargs):
            raise RealizationError('attachment refused for native rollback test')
        promoter.realizations.attach = refuse
    with artifact.open('rb') as held, mmap.mmap(held.fileno(), 0, access=mmap.ACCESS_READ) as mapped:
        outcome = promoter.promote(prepared.id, request, SOURCES)
        assert mapped[:] == b'old'
        assert artifact.read_bytes() == b'old'
        assert old_source.read_text() == 'theorem old : True := trivial\n'
        if outcome.status == 'admitted':
            assert not refuse_attachment
            realization = promoter.realizations.get(outcome.realization)
            assert realization.status == 'attached'
        else:
            assert outcome.status == 'failed'
            assert list(promoter.shared_root.rglob('*.lean')) == [old_source]
            assert not any(r.status == 'attached' for r in promoter.realizations.for_claim(claim.id))
