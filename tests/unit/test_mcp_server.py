import importlib
from datetime import UTC, datetime
from uuid import UUID

import pytest

NOW = datetime(2026, 7, 24, tzinfo=UTC)
RUN_ID = UUID('12345678-1234-5678-1234-567812345678')


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
    return domain.freeze_claim('Two equals two.', proposal, environment, NOW)


def _check(lean, process, claim, proof_body):
    child = process.ProcessResult(
        argv=('lake', 'env', 'lean'),
        cwd='.',
        returncode=0,
        stdout=proof_body,
        stderr='',
        timed_out=False,
        output_overflow=False,
        duration_ms=1,
    )
    return lean.LeanCheckResult(
        success=True,
        diagnostics=(),
        open_goals=(),
        process=child,
        source_sha256='c' * 64,
        toolchain=claim.environment,
    )


def test_proof_tool_requires_the_frozen_claim_and_owns_the_official_budget(
    tmp_path,
) -> None:
    domain = importlib.import_module('hardy.domain')
    lean = importlib.import_module('hardy.lean')
    server = importlib.import_module('hardy.mcp_server')
    process = importlib.import_module('hardy.process')
    storage = importlib.import_module('hardy.storage')
    claim = _claim(domain)
    store = storage.RunStore.create(tmp_path, 'mcp', now=NOW, run_id=RUN_ID)

    class Service:
        def check_proof(self, received_claim, proof_body, allowed=()):
            assert received_claim is claim
            return _check(lean, process, claim, proof_body)

        def check_scratch(self, source):
            return _check(lean, process, claim, source)

    server.configure_runtime(
        server.LeanToolRuntime(
            claim=claim,
            service=Service(),
            store=store,
            official_checks=1,
            observation_bytes=domain.RunLimits().model_observation_bytes,
        )
    )

    server.lean_check_scratch('#check Nat')
    result = server.lean_check_proof(claim.content_hash, 'by rfl')

    assert result.success
    with pytest.raises(ValueError, match='budget'):
        server.lean_check_proof(claim.content_hash, 'by rfl')
    with pytest.raises(ValueError, match='Frozen Claim'):
        server.lean_check_proof('0' * 64, 'by rfl')


def test_tool_observations_are_bounded_and_full_output_is_saved(tmp_path) -> None:
    domain = importlib.import_module('hardy.domain')
    lean = importlib.import_module('hardy.lean')
    server = importlib.import_module('hardy.mcp_server')
    process = importlib.import_module('hardy.process')
    storage = importlib.import_module('hardy.storage')
    claim = _claim(domain)
    store = storage.RunStore.create(tmp_path, 'mcp', now=NOW, run_id=RUN_ID)

    class Service:
        def check_proof(self, received_claim, proof_body, allowed=()):
            return _check(lean, process, claim, 'x' * 10_000)

    server.configure_runtime(
        server.LeanToolRuntime(
            claim=claim,
            service=Service(),
            store=store,
            official_checks=1,
            observation_bytes=1_024,
        )
    )

    result = server.lean_check_proof(claim.content_hash, 'by rfl')

    assert result.observation_truncated
    assert result.output_artifact is not None
    assert len(result.model_dump_json().encode('utf-8')) <= 1_024
    assert (store.path / result.output_artifact).exists()


def test_runtime_loader_rejects_a_claim_file_with_a_mismatched_hash(tmp_path) -> None:
    domain = importlib.import_module('hardy.domain')
    server = importlib.import_module('hardy.mcp_server')
    storage = importlib.import_module('hardy.storage')
    config = importlib.import_module('hardy.config')
    claim = _claim(domain).model_copy(update={'content_hash': '0' * 64})
    run_dir = tmp_path / 'run'
    run_dir.mkdir()
    storage.RunStore(run_dir, RUN_ID).write_json(
        __import__('pathlib').PurePosixPath('formalization.json'), claim
    )
    config_path = tmp_path / 'hardy.toml'
    config.write_setting(config_path, 'lean_project', str(tmp_path / 'lean-project'))
    config.write_setting(config_path, 'lake', str(tmp_path / 'lake.exe'))

    with pytest.raises(ValueError, match='hash'):
        server.load_runtime(
            {
                'HARDY_RUN_DIR': str(run_dir),
                'HARDY_CONFIG': str(config_path),
                'HARDY_CLAIM_SHA256': claim.content_hash,
            }
        )


def test_oversized_proof_input_is_rejected_without_spending_a_check(tmp_path) -> None:
    domain = importlib.import_module('hardy.domain')
    server = importlib.import_module('hardy.mcp_server')
    storage = importlib.import_module('hardy.storage')
    claim = _claim(domain)
    runtime = server.LeanToolRuntime(
        claim=claim,
        service=object(),
        store=storage.RunStore.create(tmp_path, 'mcp', now=NOW, run_id=RUN_ID),
        official_checks=1,
        observation_bytes=domain.RunLimits().model_observation_bytes,
    )
    server.configure_runtime(runtime)

    with pytest.raises(ValueError, match='64 KiB'):
        server.lean_check_proof(claim.content_hash, 'x' * (64 * 1024 + 1))

    assert runtime.remaining_official_checks == 1


def test_declaration_search_observation_is_bounded(tmp_path) -> None:
    declarations = importlib.import_module('hardy.declarations')
    domain = importlib.import_module('hardy.domain')
    server = importlib.import_module('hardy.mcp_server')
    storage = importlib.import_module('hardy.storage')
    claim = _claim(domain)

    package = tmp_path / 'project' / '.lake' / 'packages' / 'mathlib' / 'Mathlib'
    package.mkdir(parents=True)
    (package / 'Huge.lean').write_text(
        f'theorem huge_result : {"x" * 600} := trivial\n', encoding='utf-8'
    )

    store = storage.RunStore.create(tmp_path, 'mcp', now=NOW, run_id=RUN_ID)
    server.configure_runtime(
        server.LeanToolRuntime(
            claim=claim,
            service=object(),
            store=store,
            official_checks=1,
            observation_bytes=600,
            declarations=declarations.DeclarationIndex(tmp_path / 'project'),
        )
    )

    result = server.lean_search_declarations('huge')

    assert [record.name for record in result.results] == ['huge_result']
    assert result.observation_truncated
    assert result.output_artifact is not None
    assert len(result.model_dump_json().encode('utf-8')) <= 600
    assert (store.path / result.output_artifact).exists()
