"""Run with an installed wheel's Python from outside the source checkout.

No network, model or Lean/TeX binary is used. The SymPy helper executes the
trusted cell below in a temporary directory; this is not an isolation test.
"""
from __future__ import annotations

import json
import runpy
import subprocess
import sys
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

import hardy
from hardy.formal.contracts import (
    EnvironmentIdentity,
    FormalizationProposal,
    FrozenClaim,
    freeze_claim,
)
from hardy.formal.lean import LeanCheckResult
from hardy.formal.tools import LeanToolRuntime
from hardy.foundation.process import ProcessResult
from hardy.workflows.storage import RunStore


class FakeLean:
    def check_proof(self, claim, proof, allowed):
        return LeanCheckResult(
            success=True, diagnostics=(), open_goals=(), source_sha256='c' * 64,
            toolchain=claim.environment,
            process=ProcessResult(argv=('fake-lean',), cwd='.', returncode=0,
                                  stdout='x' * 10_000, stderr='', timed_out=False,
                                  output_overflow=False, duration_ms=0),
        )


def serve(directory):
    from hardy.app import mcp as server

    claim = FrozenClaim.model_validate_json((directory / 'claim.json').read_text())
    server.configure_runtime(LeanToolRuntime(
        claim=claim, service=FakeLean(), store=RunStore(directory, UUID(int=0)),
        official_checks=1, observation_bytes=1024,
    ))
    # Supply only the service construction, then execute the retained launcher.
    # The shim must reach the same server globals and transport as app.mcp.
    server.load_runtime = lambda _: None
    runpy.run_module('hardy.mcp_server', run_name='__main__')


async def transport(directory, claim):
    parameters = StdioServerParameters(
        command=sys.executable, args=[str(Path(__file__).resolve()), '--serve', str(directory)],
        cwd=directory,
    )
    with anyio.fail_after(30):
        async with (
            stdio_client(parameters) as (read, write),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            names = {tool.name for tool in (await session.list_tools()).tools}
            assert names == {'lean_check_proof', 'lean_check_scratch',
                             'lean_inspect_declarations', 'lean_search_declarations', 'rank_premises'}
            wrong = await session.call_tool('lean_check_proof', {'claim_id': '0' * 64, 'proof_body': 'by rfl'})
            assert wrong.isError
            valid = await session.call_tool('lean_check_proof', {'claim_id': claim.content_hash, 'proof_body': 'by rfl'})
            assert not valid.isError
            result = valid.structuredContent
            assert result['success'] and result['observation_truncated']
            assert result['output_artifact'] == 'process/mcp-lean-0.json'
            saved = json.loads((directory / result['output_artifact']).read_text())
            assert saved['process']['stdout'] == 'x' * 10_000
            exhausted = await session.call_tool('lean_check_proof', {'claim_id': claim.content_hash, 'proof_body': 'by rfl'})
            assert exhausted.isError


def smoke(directory):
    from hardy.acceptance import run_deterministic_experiment
    from hardy.algebra.backends import SympyBackend
    from hardy.algebra.session import CasSession
    from hardy.config import Config
    from hardy.workflows.contracts import RunLimits
    from hardy.workflows.recorded import validate_run_consistency

    assert Path(hardy.__file__).resolve().is_relative_to(Path(sys.prefix).resolve()), hardy.__file__
    for resource in ('templates/paper.tex', 'export.css', 'evals/viewer.html',
                     'evals/bibliography.html', 'acceptance_problems.json',
                     'prompts/chat.md.j2', 'prompts/staged/base.md.j2', 'cas_driver.py'):
        assert files('hardy').joinpath(resource).read_bytes(), resource
    for module in ('hardy', 'hardy.cli', 'hardy.app.cli'):
        completed = subprocess.run([sys.executable, '-m', module, 'prove', '--help'],
                                   cwd=directory, capture_output=True, text=True, timeout=30)
        assert completed.returncode == 0, completed.stderr
        assert 'prove' in completed.stdout
    config = Config(model='deterministic-no-model', lean_command=('fake-lean',),
                    lean_project=None, lean_timeout=1, latex_command=('fake-tex',),
                    root=directory, project='smoke', runs_root=directory / 'runs')
    for outcome in ('verified', 'exhausted'):
        run = run_deterministic_experiment(config, outcome=outcome)
        assert validate_run_consistency(run.run_dir, run.manifest) == (), outcome
    kernel = CasSession(backend=SympyBackend(), command=None,
                        log_path=directory / 'cells.jsonl', limits=RunLimits(), cwd=directory)
    try:
        cell = kernel.execute('1 + 1')
        assert cell.accepted and cell.value_repr == '2', cell
    finally:
        kernel.close()
    proposal = FormalizationProposal(restatement='Two equals two.', domains=(), quantifiers=(),
                                     assumptions=(), interpretation_choices=(), theorem_name='two_eq_two',
                                     binders='', proposition='2 = 2')
    environment = EnvironmentIdentity(lean_version='4.32.0', lean_commit='a' * 40,
                                      mathlib_revision='b' * 40, lake_manifest_sha256='c' * 64,
                                      imports=('Mathlib',))
    claim = freeze_claim('Two equals two.', proposal, environment, datetime.now(UTC))
    (directory / 'claim.json').write_text(claim.model_dump_json(), encoding='utf-8')
    anyio.run(transport, directory, claim)
    print('Installed wheel: assets, help, deterministic runs, CAS helper and MCP stdio passed.')


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--serve':
        serve(Path(sys.argv[2]))
    else:
        with TemporaryDirectory(prefix='hardy-wheel-smoke-') as temporary:
            smoke(Path(temporary))
