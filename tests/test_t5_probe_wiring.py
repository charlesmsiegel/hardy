import pytest
from test_chat import FakeChatRuntime, session

from hardy.foundation import process
from hardy.workflows.interactive import session as module


@pytest.mark.parametrize('first', [process.GuardedResult(returncode=1, stdout='partial'), process.GuardedResult(returncode=None, stdout='partial', output_overflow=True), OSError('temporarily unavailable')])
def test_failed_search_path_probe_is_retried(tmp_path, monkeypatch, first):
    chat = session(tmp_path, FakeChatRuntime([]))
    chat._lean_command = ('lake', 'env', 'lean')
    chat._search_path = None
    monkeypatch.setenv('LEAN_PATH', str(tmp_path / 'fallback'))
    replies = iter((first, process.GuardedResult(returncode=0, stdout=str(tmp_path / 'actual'))))
    def run(*args, **kwargs):
        reply = next(replies)
        if isinstance(reply, Exception):
            raise reply
        return reply
    monkeypatch.setattr(process, 'run_guarded', run)
    assert chat._lean_search_path() == (tmp_path / 'fallback',)
    assert chat._lean_search_path() == (tmp_path / 'actual',)
    assert chat._lean_search_path() == (tmp_path / 'actual',)


def test_all_admission_probes_exclude_workspace_builds(tmp_path, monkeypatch):
    chat = session(tmp_path, FakeChatRuntime([]))
    asked = []
    monkeypatch.setattr(chat.lean, 'run_source', lambda source, **kwargs: asked.append(kwargs))
    chat._admission_elaborate_source('import Mathlib')
    chat._admission_refute_source('import Mathlib')
    assert all('env' not in kwargs for kwargs in asked)


def test_automation_probe_uses_its_shorter_budget(tmp_path, monkeypatch):
    chat = session(tmp_path, FakeChatRuntime([]))
    asked = []
    monkeypatch.setattr(chat.formal, '_automation_probe', lambda proposed, **kwargs: asked.append(kwargs))
    chat._automation_probe({'p': 'True'})
    assert asked[0]['probe_seconds'] == module.AUTOMATION_PROBE_SECONDS
    assert module.AUTOMATION_PROBE_SECONDS < module.PROBE_SECONDS


@pytest.mark.real_toolchain
def test_real_admission_probes_ignore_shadow_mathlib(tmp_path):
    import os
    from pathlib import Path

    from hardy.formal.contracts import Request
    from hardy.formal.lean import LeanTools

    project = os.environ.get('HARDY_LEAN_PROJECT')
    if not project:
        pytest.skip('configured Lean project required')
    import shutil
    lake = shutil.which('lake')
    if lake is None:
        pytest.skip('lake is not installed')
    # A tiny configured Lake library establishes path isolation with the real
    # compiler without requiring the whole Mathlib cache on this machine.
    configured = tmp_path / 'configured'
    configured.mkdir()
    (configured / 'lakefile.toml').write_text('name = "probe_fixture"\n[[lean_lib]]\nname = "Mathlib"\n')
    (configured / 'lean-toolchain').write_text((Path(project) / 'lean-toolchain').read_text())
    (configured / 'Mathlib.lean').write_text('theorem configuredMarker : True := True.intro\n')
    built = process.run_guarded([lake, 'build', 'Mathlib'], cwd=configured, timeout=60)
    assert built.returncode == 0, built.output
    project = configured
    chat = session(tmp_path, FakeChatRuntime([]))
    chat.lean = LeanTools(Request('theorem target : True', 'True'), (lake, 'env', 'lean'), project=Path(project), timeout=60)
    # Keep a conflicting compiled workspace module present while both probes
    # elaborate using the configured library. The unit test above checks the
    # absence of workspace paths directly, independent of Lake precedence.
    (tmp_path / 'Mathlib.lean').write_text('def shadowSentinel : Nat := 1')
    from hardy.formal.lean import elaborate
    compiled = elaborate('def shadowSentinel : Nat := 1', argv=(lake, 'env', 'lean', '--root=' + str(tmp_path), '-o', str(tmp_path / 'Mathlib.olean')), cwd=Path(project), timeout_seconds=60, source_path=tmp_path / 'Mathlib.lean')
    assert compiled.success, compiled.process.stdout + compiled.process.stderr
    chat._lean_path = lambda: str(tmp_path)
    source = 'import Mathlib\nexample : True := configuredMarker\n'
    for run in (chat._admission_elaborate_source, chat._admission_refute_source):
        checked = run(source)
        assert checked.ok, checked.output
