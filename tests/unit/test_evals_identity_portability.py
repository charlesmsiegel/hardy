"""Source scope and compatibility identities remain portable."""
import json
import subprocess
import sys
from pathlib import Path

from hardy.evals import identity, sweep
from hardy.workflows import prove


def test_all_prompt_templates_count_and_web_does_not(tmp_path, monkeypatch):
    monkeypatch.setattr(identity, 'RUN_SOURCE_ROOT', tmp_path)
    template = tmp_path / 'prompts/compaction_preamble.md.j2'
    template.parent.mkdir()
    template.write_text('before', encoding='utf-8')
    web = tmp_path / 'app/web/server.py'
    web.parent.mkdir(parents=True)
    web.write_text('before', encoding='utf-8')
    assert identity.run_source_paths() == (template,)
    args = dict(model='m', mode='batch', limits={}, repeats=1)
    before = identity.run_procedure_digest_of(**args)
    template.write_text('after', encoding='utf-8')
    assert identity.run_procedure_digest_of(**args) != before
    before = identity.run_procedure_digest_of(**args)
    web.write_text('after', encoding='utf-8')
    assert identity.run_procedure_digest_of(**args) == before


def test_deciding_process_changes_digest(tmp_path, monkeypatch):
    assert any(Path(p).as_posix().endswith('foundation/process.py') for p in sweep.DECIDING_SOURCES)
    source = tmp_path / 'process.py'
    source.write_bytes(b'before')
    monkeypatch.setattr(sweep, 'DECIDING_SOURCES', (str(source),))
    sweep._source_digests.cache_clear()
    before = sweep.procedure_digest_of(600)
    source.write_bytes(b'after')
    sweep._source_digests.cache_clear()
    assert sweep.procedure_digest_of(600) != before
    sweep._source_digests.cache_clear()


def test_kernel_patch_is_provenance_not_compatibility():
    from hardy.formal.contracts import EnvironmentIdentity
    env = EnvironmentIdentity(lean_version='v', lean_commit='c', mathlib_revision='m', lake_manifest_sha256='a' * 64)
    host = dict(environment_identity_scheme='host-compatibility-v2', platform='Linux-6.18.44-x86_64', system='Linux', machine='x86_64', cpu_count=4, processor='cpu')
    assert sweep.environment_digest_of(env, host) == sweep.environment_digest_of(env, {**host, 'platform': 'Linux-6.18.45-x86_64'})
    assert sweep.environment_digest_of(env, host) != sweep.environment_digest_of(env, {**host, 'machine': 'arm64'})
    assert sweep.environment_digest_of(env, host) != sweep.environment_digest_of(env, {**host, 'processor': 'other'})


def test_run_import_closure_covers_loaded_modules():
    code = '''import json, sys
import hardy.evals.runner, hardy.evals.staged, hardy.app.wiring
from hardy.evals.identity import RUN_SOURCE_ROOT, run_source_paths
covered = {str(p.resolve()) for p in run_source_paths()}
missing = [name for name, module in sys.modules.items() if name.startswith('hardy.') and getattr(module, '__file__', None) and str(__import__('pathlib').Path(module.__file__).resolve()) not in covered]
print(json.dumps(missing))
'''
    result = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, encoding='utf-8', check=True)
    assert json.loads(result.stdout) == []


def test_strategy_sources_normalize_crlf(monkeypatch):
    request = prove.ProveRequest(text='claim', model='m')
    original = Path.read_bytes
    lf = prove._strategy_record(request, shared_tool_budget=True)
    monkeypatch.setattr(Path, 'read_bytes', lambda path: original(path).replace(b'\r\n', b'\n').replace(b'\n', b'\r\n'))
    crlf = prove._strategy_record(request, shared_tool_budget=True)
    assert lf['source_sha256'] == crlf['source_sha256']


def test_every_packaged_prompt_template_is_covered():
    templates = set((identity.RUN_SOURCE_ROOT / "prompts").rglob("*.j2"))
    assert templates <= set(identity.run_source_paths())


def test_mixed_case_artifacts_and_tar_have_portable_order(tmp_path):
    import io
    import tarfile

    from hardy.evals.adjudication import _artifacts
    from hardy.foundation.files import WriteGuard
    from hardy.literature.sources.tex import pack_directory

    names = ["main.tex", "Macros.tex", "figures/Plot.tex", "figures/axes.tex"]
    for name in names:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"content")
    with tarfile.open(fileobj=io.BytesIO(pack_directory(tmp_path))) as tar:
        assert tar.getnames() == sorted(names)
    assert [artifact.path for artifact in _artifacts(WriteGuard(tmp_path))] == sorted(names)


def test_persisted_path_sorts_use_explicit_string_keys():
    import ast
    offenders = []
    for directory in ("evals", "corpus", "literature/sources"):
        for path in (identity.RUN_SOURCE_ROOT / directory).rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name) or node.func.id != "sorted" or not node.args:
                    continue
                argument = node.args[0]
                if isinstance(argument, ast.Call) and isinstance(argument.func, ast.Attribute) and argument.func.attr in {"glob", "rglob", "iterdir"} and not any(keyword.arg == "key" for keyword in node.keywords):
                    offenders.append(f"{path.name}:{node.lineno}")
    assert offenders == []


def test_historical_boards_keep_original_exact_host_digest():
    from hardy.evals import digests
    from hardy.evals.outstanding import environment_digest_of_board
    from hardy.formal.contracts import EnvironmentIdentity

    env = EnvironmentIdentity(lean_version='v', lean_commit='c', mathlib_revision='m', lake_manifest_sha256='a' * 64)
    host = dict(platform='Linux-6.18.44-x86_64', machine='x86_64', cpu_count=4)
    board = dict(environment=env.model_dump(mode='json'), host=host)
    original = digests.environment_digest({'identity': env.model_dump(mode='json'), 'host': host})
    assert environment_digest_of_board(board) == original
    patched = {**board, 'host': {**host, 'platform': 'Linux-6.18.45-x86_64'}}
    assert environment_digest_of_board(patched) != original


def test_new_host_records_explicit_environment_scheme():
    assert sweep.host_info()['environment_identity_scheme'] == 'host-compatibility-v2'


def test_linux_processor_identity_and_missing_fallback(monkeypatch):
    monkeypatch.setattr(Path, 'read_text', lambda path, **kwargs: 'model name : Example CPU\n')
    assert sweep._processor_identity('Linux', '') == 'Example CPU'

    def missing(path, **kwargs):
        raise FileNotFoundError(path)

    monkeypatch.setattr(Path, 'read_text', missing)
    assert sweep._processor_identity('Linux', '') == 'unknown-processor'
    assert sweep._processor_identity('Windows', 'CPU Model') == 'CPU Model'
