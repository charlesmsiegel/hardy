"""Unsupported file change generations reverify bytes, including on Windows."""
import os
from types import SimpleNamespace


def test_windows_timestamps_do_not_enable_verified_reuse(tmp_path, monkeypatch):
    from hardy.literature import file_identity

    path = tmp_path / 'file'
    path.write_bytes(b'one')
    monkeypatch.setattr(file_identity, 'WINDOWS', True)
    # CI demonstrated a positive NT ChangeTime surviving a same-size edit.
    monkeypatch.setattr(file_identity, '_windows_change_time', lambda path: 134364104104532783, raising=False)
    info = SimpleNamespace(st_ctime_ns=134364104104532783)
    assert file_identity.change_token(path, info) is None


def test_windows_reverifies_artifact_representation_and_paper_after_mtime_restore(tmp_path, monkeypatch):
    import pytest
    from test_paper_tools import _sourced
    from test_source_representations import SHA, record

    from hardy.literature import file_identity
    from hardy.literature.sources.artifacts import ArtifactStore, ImportRequest
    from hardy.literature.sources.representations import RepresentationError, RepresentationStore

    monkeypatch.setattr(file_identity, 'WINDOWS', True)
    monkeypatch.setattr(file_identity, '_windows_change_time', lambda path: 134364104104532783, raising=False)
    artifacts = ArtifactStore(tmp_path / 'artifacts')
    sha = artifacts.import_bytes(ImportRequest(data=b'one')).artifact.sha256
    assert artifacts.availability(sha).status == 'available'
    artifact_path = artifacts.root / sha / 'content'
    representations = RepresentationStore(tmp_path / 'representations')
    representations.admit(record('rep-text', {'text.txt': b'one'}), {'text.txt': b'one'})
    assert representations.text(SHA, 'rep-text') == 'one'
    representation_path = representations.root / SHA / 'rep-text' / 'text.txt'
    runtime = _sourced(tmp_path / 'papers')
    paper_id = 'math/0211159v1'
    assert runtime.call('fetch_paper', {'paper_id': paper_id}).ok
    assert runtime.call('fetch_source', {'paper_id': paper_id}).ok
    args = {'paper_id': paper_id, 'file': 'main.tex'}
    assert runtime.call('read_paper', args).ok
    from hardy.literature.metadata import parse_id
    paper_path = runtime.library.path_for(parse_id(paper_id)) / 'source/archive.bin'
    for path in (artifact_path, representation_path, paper_path):
        before = path.stat()
        data = path.read_bytes()
        path.write_bytes(b'!' + data[1:])
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert artifacts.availability(sha).status == 'corrupt'
    with pytest.raises(RepresentationError, match='no longer'):
        representations.text(SHA, 'rep-text')
    assert not runtime.call('read_paper', args).ok
    assert not artifacts._verified
    assert not representations._verified
    assert not runtime.library._verified


def test_missing_change_clock_disables_all_verification_caches(tmp_path, monkeypatch):
    from test_paper_tools import _sourced
    from test_source_representations import SHA, record

    from hardy.literature import library
    from hardy.literature.metadata import parse_id
    from hardy.literature.sources import artifacts, representations

    artifact_store = artifacts.ArtifactStore(tmp_path / 'artifacts')
    sha = artifact_store.import_bytes(artifacts.ImportRequest(data=b'plain text')).artifact.sha256
    representation_store = representations.RepresentationStore(tmp_path / 'representations')
    representation_store.admit(record('rep-text', {'text.txt': b'one'}), {'text.txt': b'one'})
    runtime = _sourced(tmp_path / 'papers')
    paper_id = 'math/0211159v1'
    runtime.call('fetch_paper', {'paper_id': paper_id})
    runtime.call('fetch_source', {'paper_id': paper_id})
    runtime.library = library.PaperLibrary(runtime.library.root)
    reads = []
    for module in (artifacts, representations, library):
        monkeypatch.setattr(module, 'change_token', lambda path, info: None)
        original = module.read_bytes

        def counted(root, relative, original=original):
            reads.append(str(relative))
            return original(root, relative)

        monkeypatch.setattr(module, 'read_bytes', counted)
    for _ in range(2):
        assert artifact_store.availability(sha).status == 'available'
        assert representation_store.text(SHA, 'rep-text') == 'one'
        assert runtime.library.source_manifest(parse_id(paper_id))
    assert reads.count(f'{sha}/content') == 2
    assert reads.count(f'{SHA}/rep-text/text.txt') == 2
    assert sum(name.endswith('archive.bin') for name in reads) == 2



def test_simulated_change_generation_does_not_depend_on_native_ctime(tmp_path, monkeypatch):
    from pathlib import Path

    from file_identity_helpers import windows_creation_clock

    from hardy.literature.sources import artifacts

    original = Path.lstat

    class CreationStat:
        st_ctime_ns = 100

        def __init__(self, held):
            self.held = held

        def __getattr__(self, name):
            return getattr(self.held, name)

    monkeypatch.setattr(Path, 'lstat', lambda path: CreationStat(original(path)))
    windows_creation_clock(monkeypatch)
    path = tmp_path / 'file'
    path.write_bytes(b'one')
    before = path.stat()
    initial = artifacts.change_token(path, path.lstat())
    path.write_bytes(b'two')
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert path.lstat().st_ctime_ns == 100
    assert artifacts.change_token(path, path.lstat()) != initial
