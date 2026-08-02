import os
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from uuid import UUID

import anyio
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from hardy.config import write_setting
from hardy.domain import EnvironmentIdentity, FormalizationProposal, freeze_claim
from hardy.storage import RunStore

ROOT = Path(__file__).parents[2]
NOW = datetime(2026, 7, 24, tzinfo=UTC)


@pytest.mark.real_toolchain
def test_stdio_server_lists_its_tools_and_checks_valid_and_invalid_proofs(
    tmp_path,
) -> None:
    lake = shutil.which('lake')
    if lake is None:
        pytest.skip('lake is not installed')
    if not (ROOT / 'lean_project' / 'lake-manifest.json').exists():
        pytest.skip('the pinned Lean project is not built; run `hardy setup`')
    environment = EnvironmentIdentity(
        lean_version='4.32.0',
        lean_commit='8c9756b28d64dab099da31a4c09229a9e6a2ef35',
        mathlib_revision='81a5d257c8e410db227a6665ed08f64fea08e997',
        lake_manifest_sha256='b' * 64,
        imports=('Mathlib',),
    )
    proposal = FormalizationProposal(
        restatement='Two equals two.',
        domains=(),
        quantifiers=(),
        assumptions=(),
        interpretation_choices=(),
        theorem_name='two_eq_two',
        binders='',
        proposition='2 = 2',
    )
    claim = freeze_claim('Two equals two.', proposal, environment, NOW)
    run_dir = tmp_path / 'run'
    run_dir.mkdir()
    RunStore(run_dir, UUID(int=0)).write_json(
        PurePosixPath('formalization.json'), claim
    )
    config_path = tmp_path / 'hardy.toml'
    write_setting(config_path, 'lean_project', str(ROOT / 'lean_project'))
    write_setting(config_path, 'lake', str(Path(lake)))
    environment_variables = dict(os.environ)
    environment_variables.update(
        {
            'HARDY_RUN_DIR': str(run_dir),
            'HARDY_CONFIG': str(config_path),
            'HARDY_CLAIM_SHA256': claim.content_hash,
        }
    )

    async def exercise() -> None:
        parameters = StdioServerParameters(
            command=sys.executable,
            args=['-m', 'hardy.mcp_server'],
            env=environment_variables,
            cwd=ROOT,
        )
        async with (
            stdio_client(parameters) as (read_stream, write_stream),
            ClientSession(read_stream, write_stream) as session,
        ):
            await session.initialize()
            listed = await session.list_tools()
            assert {tool.name for tool in listed.tools} == {
                'lean_check_proof',
                'lean_check_scratch',
                'lean_inspect_declarations',
                'lean_search_declarations',
                'rank_premises',
            }
            valid = await session.call_tool(
                'lean_check_proof',
                {'claim_id': claim.content_hash, 'proof_body': 'by rfl'},
            )
            invalid = await session.call_tool(
                'lean_check_proof',
                {
                    'claim_id': claim.content_hash,
                    'proof_body': 'by exact True.intro',
                },
            )
            assert valid.structuredContent is not None
            assert valid.structuredContent['success']
            assert invalid.structuredContent is not None
            assert not invalid.structuredContent['success']

    anyio.run(exercise)
