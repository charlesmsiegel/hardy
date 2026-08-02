"""Retrieval reaches a model the same way on both transports, or it does not count.

`staged.py` states the rule these cover: a tool costs the same budget whichever
transport the model reached it through. A ranking that is bounded and metered
over MCP but unbounded in-process would be one feature with two behaviours.
"""

from __future__ import annotations

import importlib
import json
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


class _Source:
    def __init__(self, retrieval, results, name='lean-find'):
        self.identity = retrieval.SourceIdentity(
            name=name, kind='lean_search', corpus='Mathlib 81a5d257', pinned=True
        )
        self.worst_case_seconds = 1.0
        self._results = results

    def search(self, goal, limit):
        return tuple(self._results)


def _retriever(retrieval, results, seconds=300):
    domain = importlib.import_module('hardy.domain')
    return retrieval.PremiseRetriever(
        sources=[_Source(retrieval, results)],
        limits=domain.RunLimits(retrieval_seconds=seconds),
        clock=lambda: 0.0,
    )


def test_the_mcp_server_answers_a_ranking_and_bounds_it(tmp_path) -> None:
    domain = importlib.import_module('hardy.domain')
    lean = importlib.import_module('hardy.lean')
    retrieval = importlib.import_module('hardy.retrieval')
    server = importlib.import_module('hardy.mcp_server')
    storage = importlib.import_module('hardy.storage')

    store = storage.RunStore.create(tmp_path, 'mcp', now=NOW, run_id=RUN_ID)
    server.configure_runtime(
        server.LeanToolRuntime(
            claim=_claim(domain),
            service=object(),
            store=store,
            official_checks=1,
            observation_bytes=1_200,
            retriever=_retriever(
                retrieval, [lean.DeclarationRecord(name='Huge.result', signature='x' * 10_000)]
            ),
        )
    )

    ranking = server.rank_premises('_ + _ = _ + _')

    assert [premise.name for premise in ranking.premises] == ['Huge.result']
    assert ranking.observation_truncated
    assert ranking.output_artifact is not None
    assert len(ranking.model_dump_json().encode('utf-8')) <= 1_200
    assert (store.path / ranking.output_artifact).exists()
    # Bounding rewrites the premises, so the digest must still describe the
    # provenance the bounded value carries rather than the one it was cut from.
    assert ranking.provenance_sha256 == ranking.provenance.digest


def test_a_run_without_a_retriever_says_so_instead_of_ranking_nothing(tmp_path) -> None:
    domain = importlib.import_module('hardy.domain')
    server = importlib.import_module('hardy.mcp_server')
    storage = importlib.import_module('hardy.storage')

    server.configure_runtime(
        server.LeanToolRuntime(
            claim=_claim(domain),
            service=object(),
            store=storage.RunStore.create(tmp_path, 'mcp', now=NOW, run_id=RUN_ID),
            official_checks=1,
            observation_bytes=32 * 1024,
        )
    )

    with pytest.raises(ValueError, match='retrieval'):
        server.rank_premises('_ + _ = _ + _')


def test_the_staged_dispatcher_offers_the_same_tool(tmp_path) -> None:
    domain = importlib.import_module('hardy.domain')
    lean = importlib.import_module('hardy.lean')
    retrieval = importlib.import_module('hardy.retrieval')
    server = importlib.import_module('hardy.mcp_server')
    staged = importlib.import_module('hardy.staged')
    storage = importlib.import_module('hardy.storage')

    assert 'rank_premises' in {spec['function']['name'] for spec in staged.TOOLS}

    runtime = server.LeanToolRuntime(
        claim=_claim(domain),
        service=object(),
        store=storage.RunStore.create(tmp_path, 'mcp', now=NOW, run_id=RUN_ID),
        official_checks=1,
        observation_bytes=32 * 1024,
        retriever=_retriever(
            retrieval, [lean.DeclarationRecord(name='Nat.add_comm', signature='n + m = m + n')]
        ),
    )
    dispatch = staged.ClaudeStagedRuntime(
        store=None, lean_runtime_factory=lambda claim: runtime
    )._dispatcher(runtime)

    result = dispatch('rank_premises', {'goal': '_ + _ = _ + _', 'limit': 5})

    assert result.ok
    payload = json.loads(result.output)
    assert [premise['name'] for premise in payload['premises']] == ['Nat.add_comm']
    assert payload['provenance']['sources'][0]['identity']['pinned'] is True


def test_a_malformed_retrieval_call_is_an_answer_rather_than_a_traceback(tmp_path) -> None:
    domain = importlib.import_module('hardy.domain')
    retrieval = importlib.import_module('hardy.retrieval')
    server = importlib.import_module('hardy.mcp_server')
    staged = importlib.import_module('hardy.staged')
    storage = importlib.import_module('hardy.storage')

    runtime = server.LeanToolRuntime(
        claim=_claim(domain),
        service=object(),
        store=storage.RunStore.create(tmp_path, 'mcp', now=NOW, run_id=RUN_ID),
        official_checks=1,
        observation_bytes=32 * 1024,
        retriever=_retriever(retrieval, []),
    )
    dispatch = staged.ClaudeStagedRuntime(
        store=None, lean_runtime_factory=lambda claim: runtime
    )._dispatcher(runtime)

    result = dispatch('rank_premises', {'goal': 'x' * 5_000})

    assert not result.ok
    assert 'characters' in result.output
