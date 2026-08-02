"""What a premise ranking has to be able to say about itself.

The feature is easy to build dishonestly: fan out to a couple of searches,
concatenate, and hand back a list that looks authoritative. These tests pin the
three things that stop that -- the ranking names every source that produced it,
carries a digest a reader can recompute, and admits when a source was skipped
or failed rather than returning a shorter list that reads as complete.
"""

from __future__ import annotations

import importlib
import json

import pytest
from pydantic import ValidationError

from hardy.lean import DeclarationRecord


def _record(name: str, signature: str = '') -> DeclarationRecord:
    return DeclarationRecord(name=name, signature=signature or f'{name} : True')


class FakeSource:
    """A premise source with scripted answers, under the real protocol."""

    def __init__(self, identity, results=(), error=None, worst_case_seconds=5.0, seconds=1.0):
        self.identity = identity
        self.worst_case_seconds = worst_case_seconds
        self._results = tuple(results)
        self._error = error
        self._seconds = seconds
        self.calls: list[tuple[str, int]] = []

    def search(self, goal: str, limit: int) -> tuple[DeclarationRecord, ...]:
        self.calls.append((goal, limit))
        if self._error is not None:
            raise self._error
        return self._results[:limit]


def _pinned(retrieval, name='lean-find'):
    return retrieval.SourceIdentity(
        name=name,
        kind='lean_search',
        corpus='Mathlib 81a5d257 / Lean 4.32.0',
        pinned=True,
    )


def _unpinned(retrieval, name='loogle'):
    return retrieval.SourceIdentity(
        name=name,
        kind='loogle',
        corpus='https://loogle.lean-lang.org/json',
        pinned=False,
    )


def _retriever(retrieval, sources, seconds=300, clock=None):
    domain = importlib.import_module('hardy.domain')
    return retrieval.PremiseRetriever(
        sources=sources,
        limits=domain.RunLimits(retrieval_seconds=seconds),
        clock=clock or (lambda: 0.0),
    )


def test_agreement_between_two_sources_outranks_a_single_source_favourite() -> None:
    """The point of ranking rather than concatenating.

    `Nat.add_comm` is second on both lists; `Only.lean` and `Only.loogle` are
    each first on one. Fusion has to prefer the premise both searches found.
    """
    retrieval = importlib.import_module('hardy.retrieval')
    lean = FakeSource(_pinned(retrieval), [_record('Only.lean'), _record('Nat.add_comm')])
    loogle = FakeSource(_unpinned(retrieval), [_record('Only.loogle'), _record('Nat.add_comm')])

    ranking = _retriever(retrieval, [lean, loogle]).rank('_ + _ = _ + _', limit=3)

    assert [premise.name for premise in ranking.premises][0] == 'Nat.add_comm'
    assert ranking.premises[0].score > ranking.premises[1].score
    assert [(item.source, item.rank) for item in ranking.premises[0].ranks] == [
        ('lean-find', 2),
        ('loogle', 2),
    ]


def test_one_source_listing_a_name_twice_does_not_vote_twice() -> None:
    """Otherwise a duplicate outranks a premise two searches genuinely agreed
    on, and its `ranks` would read as though two sources had found it."""
    retrieval = importlib.import_module('hardy.retrieval')
    lean = FakeSource(
        _pinned(retrieval), [_record('Nat.add_comm'), _record('Nat.add_comm'), _record('Other')]
    )
    loogle = FakeSource(_unpinned(retrieval), [_record('Other')])

    ranking = _retriever(retrieval, [lean, loogle]).rank('_ + _ = _ + _')

    duplicated = next(item for item in ranking.premises if item.name == 'Nat.add_comm')
    assert [item.source for item in duplicated.ranks] == ['lean-find']
    assert ranking.premises[0].name == 'Other'


def test_the_signature_shown_is_the_one_the_pinned_environment_gave() -> None:
    """A remote service's rendering of a type is not what will elaborate here."""
    retrieval = importlib.import_module('hardy.retrieval')
    lean = FakeSource(_pinned(retrieval), [_record('Nat.add_comm', 'Nat.add_comm : n + m = m + n')])
    loogle = FakeSource(_unpinned(retrieval), [_record('Nat.add_comm', 'from the internet')])

    ranking = _retriever(retrieval, [loogle, lean]).rank('_ + _ = _ + _')

    assert ranking.premises[0].signature == 'Nat.add_comm : n + m = m + n'


def test_a_ranking_carries_a_digest_taken_over_what_produced_it() -> None:
    """Derived, not declared -- the same rule `VerificationEvidence` follows.

    A reader holding the ranking can rebuild the provenance record and
    recompute the number, so a stamp cannot be asserted into existence.
    """
    retrieval = importlib.import_module('hardy.retrieval')
    lean = FakeSource(_pinned(retrieval), [_record('Nat.add_comm')])

    ranking = _retriever(retrieval, [lean]).rank('_ + _ = _ + _')

    assert ranking.provenance_sha256 == ranking.provenance.digest
    assert len(ranking.provenance_sha256) == 64
    rebuilt = retrieval.RetrievalProvenance.model_validate_json(
        ranking.provenance.model_dump_json()
    )
    assert rebuilt.digest == ranking.provenance_sha256
    with pytest.raises(ValidationError):
        retrieval.PremiseRanking.model_validate(
            ranking.model_dump() | {'provenance_sha256': 'f' * 64}
        )


def test_the_digest_moves_when_the_corpus_behind_the_ranking_moves() -> None:
    """Two rankings over different Mathlib revisions are not the same ranking."""
    retrieval = importlib.import_module('hardy.retrieval')
    first = FakeSource(_pinned(retrieval), [_record('Nat.add_comm')])
    moved = retrieval.SourceIdentity(
        name='lean-find', kind='lean_search', corpus='Mathlib deadbeef / Lean 4.32.0', pinned=True
    )
    second = FakeSource(moved, [_record('Nat.add_comm')])

    one = _retriever(retrieval, [first]).rank('_ + _ = _ + _')
    two = _retriever(retrieval, [second]).rank('_ + _ = _ + _')

    assert [p.name for p in one.premises] == [p.name for p in two.premises]
    assert one.provenance_sha256 != two.provenance_sha256


def test_a_ranking_an_unpinned_source_contributed_to_is_not_reproducible() -> None:
    """Loogle is a live service, so a ranking it shaped cannot be replayed."""
    retrieval = importlib.import_module('hardy.retrieval')
    lean = FakeSource(_pinned(retrieval), [_record('Nat.add_comm')])
    loogle = FakeSource(_unpinned(retrieval), [_record('Nat.mul_comm')])

    both = _retriever(retrieval, [lean, loogle]).rank('_ + _ = _ + _')
    alone = _retriever(retrieval, [lean]).rank('_ + _ = _ + _')

    assert not both.reproducible
    assert alone.reproducible


def test_an_unpinned_source_that_contributed_nothing_still_costs_reproducibility() -> None:
    """It was asked, and what it returned is part of why the order is what it is.

    An empty answer from Loogle is still an answer that moved no premise up --
    calling the ranking replayable because the list happened to be empty would
    be reproducibility by luck.
    """
    retrieval = importlib.import_module('hardy.retrieval')
    lean = FakeSource(_pinned(retrieval), [_record('Nat.add_comm')])
    loogle = FakeSource(_unpinned(retrieval), [])

    ranking = _retriever(retrieval, [lean, loogle]).rank('_ + _ = _ + _')

    assert ranking.complete
    assert not ranking.reproducible


def test_a_source_that_failed_is_named_rather_than_quietly_dropped() -> None:
    retrieval = importlib.import_module('hardy.retrieval')
    lean = FakeSource(_pinned(retrieval), [_record('Nat.add_comm')])
    loogle = FakeSource(_unpinned(retrieval), error=retrieval.RetrievalError('loogle: 503'))

    ranking = _retriever(retrieval, [lean, loogle]).rank('_ + _ = _ + _')

    assert not ranking.complete
    outcome = next(item for item in ranking.provenance.sources if item.identity.name == 'loogle')
    assert not outcome.answered
    assert '503' in (outcome.detail or '')
    assert [premise.name for premise in ranking.premises] == ['Nat.add_comm']


def test_retrieval_time_is_metered_and_a_source_that_would_overrun_is_not_started() -> None:
    """Metered like the official proof checks: the budget refuses the call.

    The clock advances 4s per reading, so the first source spends 4 of the
    5-second budget and the second one -- which could take 5 -- is never
    started rather than being allowed to overrun.
    """
    retrieval = importlib.import_module('hardy.retrieval')
    readings = iter([0.0, 4.0, 4.0, 4.0])
    lean = FakeSource(_pinned(retrieval), [_record('Nat.add_comm')], worst_case_seconds=5.0)
    loogle = FakeSource(_unpinned(retrieval), [_record('Nat.mul_comm')], worst_case_seconds=5.0)

    ranking = _retriever(
        retrieval, [lean, loogle], seconds=5, clock=lambda: next(readings)
    ).rank('_ + _ = _ + _')

    assert loogle.calls == []
    assert ranking.budget_exhausted
    assert not ranking.complete
    skipped = next(item for item in ranking.provenance.sources if item.identity.name == 'loogle')
    assert not skipped.answered
    assert 'budget' in (skipped.detail or '')
    assert ranking.seconds_spent == pytest.approx(4.0)


def test_a_ranking_records_what_each_source_spent() -> None:
    retrieval = importlib.import_module('hardy.retrieval')
    readings = iter([0.0, 1.5, 2.0])
    lean = FakeSource(_pinned(retrieval), [_record('Nat.add_comm')])
    loogle = FakeSource(_unpinned(retrieval), [_record('Nat.mul_comm')])

    ranking = _retriever(
        retrieval, [lean, loogle], seconds=60, clock=lambda: next(readings)
    ).rank('_ + _ = _ + _')

    spent = {item.identity.name: item.seconds for item in ranking.provenance.sources}
    assert spent == {'lean-find': pytest.approx(1.5), 'loogle': pytest.approx(0.5)}
    assert ranking.seconds_spent == pytest.approx(2.0)


def test_an_embedding_source_must_name_the_index_that_produced_it() -> None:
    """The identities the issue asks provenance to record, required where they apply.

    They are absent today because there is no embedding index yet, and absent
    is a different claim from omitted: a `lean_search` identity carrying index
    fields would be describing an index that does not exist.
    """
    retrieval = importlib.import_module('hardy.retrieval')
    index = retrieval.IndexIdentity(
        model='bge-m3',
        tokenizer='xlm-roberta',
        pooling='cls',
        corpus_sha256='a' * 64,
        index_sha256='b' * 64,
        index_version='2026-08-01.1',
    )

    named = retrieval.SourceIdentity(
        name='premise-index', kind='embedding', corpus='Mathlib 81a5d257', pinned=True, index=index
    )
    assert named.index is not None and named.index.pooling == 'cls'

    with pytest.raises(ValidationError):
        retrieval.SourceIdentity(
            name='premise-index', kind='embedding', corpus='Mathlib 81a5d257', pinned=True
        )
    with pytest.raises(ValidationError):
        retrieval.SourceIdentity(
            name='lean-find',
            kind='lean_search',
            corpus='Mathlib 81a5d257',
            pinned=True,
            index=index,
        )


def test_a_goal_that_is_not_one_bounded_line_is_refused_before_any_source_runs() -> None:
    retrieval = importlib.import_module('hardy.retrieval')
    lean = FakeSource(_pinned(retrieval), [_record('Nat.add_comm')])
    retriever = _retriever(retrieval, [lean])

    for goal in ('', 'x\ny', 'x' * 513):
        with pytest.raises(ValueError):
            retriever.rank(goal)
    assert lean.calls == []


class _Service:
    """A `LeanService` as far as retrieval is concerned."""

    def __init__(self, domain, lean_module, *, success=True, timed_out=False, results=()):
        self.environment = domain.EnvironmentIdentity(
            lean_version='4.32.0',
            lean_commit='8c9756b',
            mathlib_revision='81a5d257',
            lake_manifest_sha256='b' * 64,
        )
        self._lean = lean_module
        self._success = success
        self._timed_out = timed_out
        self._results = results
        self.calls: list[tuple[str, int]] = []

    def search_declarations(self, query, limit=10):
        self.calls.append((query, limit))
        return self._lean.DeclarationSearch(
            query=query,
            results=tuple(self._results),
            truncated=False,
            success=self._success,
            timed_out=self._timed_out,
            diagnostics=(),
        )


def test_the_lean_source_searches_the_environment_the_run_is_frozen_under() -> None:
    retrieval = importlib.import_module('hardy.retrieval')
    lean_module = importlib.import_module('hardy.lean')
    domain = importlib.import_module('hardy.domain')
    service = _Service(domain, lean_module, results=[_record('Nat.add_comm')])

    source = retrieval.LeanSearchSource(service, limits=domain.RunLimits(lean_process_seconds=30))

    assert [record.name for record in source.search('_ + _ = _ + _', 5)] == ['Nat.add_comm']
    assert source.worst_case_seconds == 30.0
    assert '81a5d257' in source.identity.corpus and '4.32.0' in source.identity.corpus
    # `search_declarations` accepts 1..20, and a ranking may ask for more.
    source.search('_ + _ = _ + _', 40)
    assert service.calls == [('_ + _ = _ + _', 5), ('_ + _ = _ + _', 20)]


def test_the_lean_source_reports_an_unsuccessful_search_as_no_answer() -> None:
    """`#find` that timed out returned no results, which is not the same as
    there being none -- and the ranking must not present it as the latter."""
    retrieval = importlib.import_module('hardy.retrieval')
    lean_module = importlib.import_module('hardy.lean')
    domain = importlib.import_module('hardy.domain')

    class Service:
        environment = domain.EnvironmentIdentity(
            lean_version='4.32.0',
            lean_commit='8c9756b',
            mathlib_revision='81a5d257',
            lake_manifest_sha256='b' * 64,
        )

        def search_declarations(self, query, limit=10):
            return lean_module.DeclarationSearch(
                query=query,
                results=(),
                truncated=True,
                success=False,
                timed_out=True,
                diagnostics=(),
            )

    source = retrieval.LeanSearchSource(Service(), limits=domain.RunLimits())
    assert source.identity.pinned
    assert '81a5d257' in source.identity.corpus
    with pytest.raises(retrieval.RetrievalError):
        source.search('_ + _ = _ + _', 5)


def test_loogle_hits_that_are_not_lean_declaration_names_are_discarded() -> None:
    """The response is data off the internet, so it is filtered, not trusted."""
    retrieval = importlib.import_module('hardy.retrieval')
    body = json.dumps(
        {
            'hits': [
                # The shape the real service answers with: `type` carries the
                # binders and proposition, and does not repeat the name.
                {'name': 'Nat.add_comm', 'type': ' (n m : ℕ) : n + m = m + n', 'module': 'Mathlib'},
                {'name': 'not a name; rm -rf /', 'type': 'True'},
                {'name': 'Nat.mul_comm', 'type': 'x' * 5_000},
                {'type': 'a hit with no name at all'},
                'a hit that is not an object at all',
            ]
        }
    ).encode('utf-8')

    source = retrieval.LoogleSource(fetch=lambda url, timeout: body)
    found = source.search('_ + _ = _ + _', 10)

    assert [record.name for record in found] == ['Nat.add_comm', 'Nat.mul_comm']
    # Joined, not punctuated: `name : type` would render a second colon.
    assert found[0].signature == 'Nat.add_comm (n m : ℕ) : n + m = m + n'
    assert found[0].source_file == 'Mathlib'
    assert len(found[1].signature) <= retrieval.MAX_SIGNATURE_CHARACTERS
    assert not source.identity.pinned


def test_loogle_reports_its_own_error_rather_than_returning_nothing() -> None:
    retrieval = importlib.import_module('hardy.retrieval')
    body = json.dumps({'error': 'could not parse query'}).encode('utf-8')

    source = retrieval.LoogleSource(fetch=lambda url, timeout: body)

    with pytest.raises(retrieval.RetrievalError, match='could not parse query'):
        source.search('_ + _ = _ + _', 10)


def test_an_oversized_loogle_response_is_refused_rather_than_parsed() -> None:
    retrieval = importlib.import_module('hardy.retrieval')
    body = b'{"hits": [' + b'0' * (retrieval.MAX_RESPONSE_BYTES + 1)

    source = retrieval.LoogleSource(fetch=lambda url, timeout: body)

    with pytest.raises(retrieval.RetrievalError, match='too large'):
        source.search('_ + _ = _ + _', 10)


def test_the_goal_is_sent_to_loogle_as_a_query_parameter() -> None:
    retrieval = importlib.import_module('hardy.retrieval')
    seen: list[tuple[str, float]] = []

    def fetch(url, timeout):
        seen.append((url, timeout))
        return b'{"hits": []}'

    source = retrieval.LoogleSource(endpoint='https://example.invalid/json', timeout=7.0, fetch=fetch)
    source.search('_ + _ = _ + _', 10)

    assert seen == [('https://example.invalid/json?q=_+%2B+_+%3D+_+%2B+_', 7.0)]
    assert source.worst_case_seconds == 7.0


def test_a_transport_failure_becomes_an_outcome_rather_than_an_escaping_exception() -> None:
    """Every way a request can fail is one thing to the ranking: this source did
    not answer, and here is what it said."""
    retrieval = importlib.import_module('hardy.retrieval')

    def broken(url, timeout):
        raise TimeoutError('the read operation timed out')

    def already_said_so(url, timeout):
        raise retrieval.RetrievalError('Loogle: down for maintenance')

    with pytest.raises(retrieval.RetrievalError, match='timed out'):
        retrieval.LoogleSource(fetch=broken).search('_ + _ = _ + _', 10)
    # A fetcher that already speaks in retrieval's terms is passed through
    # rather than wrapped into "Loogle request failed: Loogle: ...".
    with pytest.raises(retrieval.RetrievalError, match='^Loogle: down for maintenance$'):
        retrieval.LoogleSource(fetch=already_said_so).search('_ + _ = _ + _', 10)


def test_a_response_that_is_not_a_json_object_is_refused() -> None:
    retrieval = importlib.import_module('hardy.retrieval')

    with pytest.raises(retrieval.RetrievalError, match='not JSON'):
        retrieval.LoogleSource(fetch=lambda url, timeout: b'<html>502</html>').search('x', 10)
    with pytest.raises(retrieval.RetrievalError, match='not a JSON object'):
        retrieval.LoogleSource(fetch=lambda url, timeout: b'[1, 2]').search('x', 10)
    assert retrieval.LoogleSource(fetch=lambda url, timeout: b'{"hits": "nope"}').search(
        'x', 10
    ) == ()


def test_loogle_returns_no_more_hits_than_were_asked_for() -> None:
    retrieval = importlib.import_module('hardy.retrieval')
    body = json.dumps(
        {'hits': [{'name': f'Nat.lemma_{index}', 'type': ' : True'} for index in range(50)]}
    ).encode('utf-8')

    found = retrieval.LoogleSource(fetch=lambda url, timeout: body).search('x', 3)

    assert len(found) == 3


def test_a_ranking_of_no_premises_at_all_is_refused_as_a_request() -> None:
    retrieval = importlib.import_module('hardy.retrieval')
    lean = FakeSource(_pinned(retrieval), [_record('Nat.add_comm')])

    with pytest.raises(ValueError, match='between 1 and 50'):
        _retriever(retrieval, [lean]).rank('_ + _ = _ + _', limit=0)
    assert lean.calls == []


def test_a_source_outcome_cannot_say_nothing_about_why_it_did_not_answer() -> None:
    retrieval = importlib.import_module('hardy.retrieval')

    with pytest.raises(ValidationError):
        retrieval.SourceOutcome(identity=_pinned(retrieval), answered=False)


def test_the_default_source_set_puts_the_pinned_environment_first() -> None:
    """Which is what decides, when the budget runs short, that the source
    dropped is the one whose answers could not be replayed anyway."""
    retrieval = importlib.import_module('hardy.retrieval')
    lean_module = importlib.import_module('hardy.lean')
    domain = importlib.import_module('hardy.domain')

    retriever = retrieval.build_retriever(
        _Service(domain, lean_module, results=[_record('Nat.add_comm')]), domain.RunLimits()
    )
    kinds = [source.identity.kind for source in retriever._sources]

    assert kinds == ['lean_search', 'loogle']


def test_the_retrieval_budget_is_a_run_limit_like_every_other() -> None:
    domain = importlib.import_module('hardy.domain')

    assert domain.RunLimits().retrieval_seconds > 0
    assert domain.RunLimits(retrieval_seconds=1).retrieval_seconds == 1
