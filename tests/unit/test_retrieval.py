"""What a premise ranking has to be able to say about itself.

The feature is easy to build dishonestly: fan out to a couple of searches,
concatenate, and hand back a list that looks authoritative. These tests pin the
three things that stop that -- the ranking names every source that produced it,
carries a digest a reader can recompute, and admits when a source was skipped
or failed rather than returning a shorter list that reads as complete.
"""

from __future__ import annotations

import hashlib
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

    def query_for(self, query: str) -> str:
        return query

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


def test_a_source_failing_in_a_way_nobody_predicted_does_not_take_the_ranking_with_it() -> None:
    """`lake` losing its execute bit raises `PermissionError`, which is neither
    a `RetrievalError` nor a `ValueError`. Letting it escape `rank` threw away
    the other sources' results *and* the provenance that exists to say a source
    failed -- the one outcome this module is built to never produce.
    """
    retrieval = importlib.import_module('hardy.retrieval')
    lean = FakeSource(_pinned(retrieval), error=PermissionError('lake: not executable'))
    loogle = FakeSource(_unpinned(retrieval), [_record('Nat.mul_comm')])

    ranking = _retriever(retrieval, [lean, loogle]).rank('_ + _ = _ + _')

    assert [premise.name for premise in ranking.premises] == ['Nat.mul_comm']
    assert not ranking.complete
    broken = next(item for item in ranking.provenance.sources if item.identity.name == 'lean-find')
    assert not broken.answered
    assert 'not executable' in (broken.detail or '')


def test_the_declared_worst_case_covers_the_read_the_deadline_cannot_stop() -> None:
    """The admission check spends this number, so it has to be the true bound.

    A request admitted just before its deadline can still sit in one more
    socket read, which the socket timeout bounds and the deadline does not.
    Declaring the intended figure would let one call overrun the run's budget
    after passing the very check that exists to stop it.
    """
    retrieval = importlib.import_module('hardy.retrieval')

    source = retrieval.LoogleSource(timeout=30.0, fetch=lambda url, timeout: b'{"hits": []}')

    assert source.worst_case_seconds == 60.0
    # And it is the deadline, not the doubled figure, that is handed to fetch.
    seen: list[float] = []
    retrieval.LoogleSource(
        timeout=30.0, fetch=lambda url, timeout: (seen.append(timeout), b'{"hits": []}')[1]
    ).search('x', 5)
    assert seen == [30.0]


def test_a_response_that_never_stops_arriving_is_cut_off_at_its_deadline(monkeypatch) -> None:
    """`urlopen(timeout=...)` bounds each socket operation, not the transfer. A
    server dripping a byte at a time keeps `read` alive forever, which made the
    declared `worst_case_seconds` a fiction and let one call outlast the whole
    run budget the admission check exists to protect.
    """
    retrieval = importlib.import_module('hardy.retrieval')
    now = iter([float(tick) for tick in range(200)])

    class Dribbling:
        def __enter__(self):
            return self

        def __exit__(self, *exception):
            return False

        def read(self, size):
            return b'0'

    monkeypatch.setattr(retrieval.time, 'monotonic', lambda: next(now))
    monkeypatch.setattr(retrieval.urllib.request, 'urlopen', lambda *a, **k: Dribbling())

    with pytest.raises(retrieval.RetrievalTransportError, match='deadline'):
        retrieval._fetch_url('https://example.invalid/json', 5.0)


def test_an_endpoint_that_rejects_the_request_is_drift_rather_than_downtime(monkeypatch) -> None:
    """404 and 400 mean the request was wrong, which is the endpoint's contract
    having moved -- exactly what the live test exists to notice. Filing those
    under transport left it skipping on the drift it was written to catch, one
    level up from the last time.

    5xx, 408 and 429 stay transport: those say the service is unwell, not that
    Hardy is asking it the wrong thing.
    """
    retrieval = importlib.import_module('hardy.retrieval')
    error_module = importlib.import_module('urllib.error')

    def failing(status):
        def urlopen(*arguments, **keywords):
            raise error_module.HTTPError('https://loogle.invalid/json', status, 'no', {}, None)

        return urlopen

    for status in (400, 404, 410):
        monkeypatch.setattr(retrieval.urllib.request, 'urlopen', failing(status))
        with pytest.raises(retrieval.RetrievalError) as raised:
            retrieval._fetch_url('https://loogle.invalid/json', 5.0)
        assert not isinstance(raised.value, retrieval.RetrievalTransportError), status
        assert str(status) in str(raised.value)

    for status in (408, 429, 500, 503):
        monkeypatch.setattr(retrieval.urllib.request, 'urlopen', failing(status))
        with pytest.raises(retrieval.RetrievalTransportError):
            retrieval._fetch_url('https://loogle.invalid/json', 5.0)


def test_a_service_that_did_not_answer_is_distinguished_from_one_that_answered_badly() -> None:
    """Both are failures for the ranking, and only one of them means Loogle
    changed its contract. The live test skips on the first and must not skip on
    the second, so they cannot be the same exception.
    """
    retrieval = importlib.import_module('hardy.retrieval')

    def unreachable(url, timeout):
        raise TimeoutError('the read operation timed out')

    with pytest.raises(retrieval.RetrievalTransportError):
        retrieval.LoogleSource(fetch=unreachable).search('x', 10)
    shape = retrieval.LoogleSource(fetch=lambda url, timeout: b'{"count": 0}')
    with pytest.raises(retrieval.RetrievalError) as raised:
        shape.search('x', 10)
    assert not isinstance(raised.value, retrieval.RetrievalTransportError)
    # Both still reach `rank` as one kind of thing: a source that did not answer.
    assert issubclass(retrieval.RetrievalTransportError, retrieval.RetrievalError)


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


def test_the_budget_is_spent_across_the_run_rather_than_refilled_per_call() -> None:
    """The retriever is deliberately shared across a proving stage, so a
    per-call budget was no budget at all: a model calling `rank_premises` in a
    loop could spend an arbitrary multiple of what the run was frozen under.
    """
    retrieval = importlib.import_module('hardy.retrieval')
    readings = iter([0.0, 4.0, 4.0])
    lean = FakeSource(_pinned(retrieval), [_record('Nat.add_comm')], worst_case_seconds=4.0)
    retriever = _retriever(retrieval, [lean], seconds=5, clock=lambda: next(readings))

    first = retriever.rank('_ + _ = _ + _')
    second = retriever.rank('_ * _ = _ * _')

    assert not first.budget_exhausted
    assert first.run_seconds_remaining == pytest.approx(1.0)
    # 4 of the 5 seconds are gone, and this source may take 4 more.
    assert second.budget_exhausted
    assert second.premises == ()
    assert second.run_seconds_remaining == pytest.approx(1.0)
    assert len(lean.calls) == 1


def test_whether_a_ranking_is_complete_and_replayable_survives_serialization() -> None:
    """The tool contract promises both, and a Python property is not part of
    the JSON a model actually receives. Stored and revalidated instead, the way
    `provenance_sha256` is, so neither can be asserted into existence either.
    """
    retrieval = importlib.import_module('hardy.retrieval')
    lean = FakeSource(_pinned(retrieval), [_record('Nat.add_comm')])
    loogle = FakeSource(_unpinned(retrieval), [_record('Nat.mul_comm')])

    ranking = _retriever(retrieval, [lean, loogle]).rank('_ + _ = _ + _')
    payload = json.loads(ranking.model_dump_json())

    assert payload['complete'] is True
    assert payload['reproducible'] is False
    assert retrieval.PremiseRanking.model_validate(payload).premises == ranking.premises
    for field, wrong in (('complete', False), ('reproducible', True)):
        with pytest.raises(ValidationError):
            retrieval.PremiseRanking.model_validate(payload | {field: wrong})


def test_fusion_sees_deeper_than_the_number_of_premises_it_returns() -> None:
    """A result both sources rank past the cutoff outscores one that only a
    single source put first: 2/(60+5) beats 1/(60+1). Asking each source for
    only `limit` results threw those away -- which is the agreement the fusion
    exists to find.

    `Both.found` is fifth on both lists, so a three-premise answer that only
    looked three deep would never see it and would lead with `Lean.only`.
    """
    retrieval = importlib.import_module('hardy.retrieval')
    def listing(prefix):
        return [_record(f'{prefix}.{index}') for index in range(4)] + [_record('Both.found')]

    lean = FakeSource(_pinned(retrieval), listing('Lean'))
    loogle = FakeSource(_unpinned(retrieval), listing('Loogle'))

    ranking = _retriever(retrieval, [lean, loogle]).rank('_ + _ = _ + _', limit=3)

    assert lean.calls[0][1] == retrieval.MAX_HITS
    assert ranking.premises[0].name == 'Both.found'
    assert len(ranking.premises) == 3


def test_a_ranking_cannot_be_read_back_under_a_goal_it_was_not_computed_for() -> None:
    """The provenance hashes the goal, and nothing checked the two agreed. A
    ranking whose top-level `goal` was swapped passed every integrity check
    while presenting its premises as answers to a question nobody asked.
    """
    retrieval = importlib.import_module('hardy.retrieval')
    lean = FakeSource(_pinned(retrieval), [_record('Nat.add_comm')])

    ranking = _retriever(retrieval, [lean]).rank('_ + _ = _ + _')
    payload = json.loads(ranking.model_dump_json())

    assert retrieval.PremiseRanking.model_validate(payload).goal == '_ + _ = _ + _'
    with pytest.raises(ValidationError):
        retrieval.PremiseRanking.model_validate(payload | {'goal': '_ * _ = _ * _'})


def test_fusion_looks_deep_enough_even_for_a_one_premise_answer() -> None:
    """A multiplier cannot fix this; only a floor can.

    A premise both sources rank at `r` scores 2/(60+r), which beats the best a
    single source can offer -- 1/(60+1) -- for every r up to 61. So a cutoff of
    `limit * 3` hid the winner whenever the answer was short: at limit=1 it
    looked three deep and a shared fourth-place premise never reached fusion.
    """
    retrieval = importlib.import_module('hardy.retrieval')

    def listing(prefix):
        return [_record(f'{prefix}.{index}') for index in range(3)] + [_record('Both.found')]

    lean = FakeSource(_pinned(retrieval), listing('Lean'))
    loogle = FakeSource(_unpinned(retrieval), listing('Loogle'))

    ranking = _retriever(retrieval, [lean, loogle]).rank('_ + _ = _ + _', limit=1)

    # Asked for everything the source will give, because no cutoff derived
    # from `limit` is sufficient: a premise ranked first by one source gains
    # from a vote at any depth in the other.
    assert lean.calls == [('_ + _ = _ + _', retrieval.MAX_HITS)]
    assert [premise.name for premise in ranking.premises] == ['Both.found']


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


def test_a_goal_hardy_cannot_turn_into_one_query_is_refused_before_any_source_runs() -> None:
    retrieval = importlib.import_module('hardy.retrieval')
    lean = FakeSource(_pinned(retrieval), [_record('Nat.add_comm')])
    retriever = _retriever(retrieval, [lean])

    for goal in ('', '   ', 'x' * 513, 'y' * 5_000):
        with pytest.raises(ValueError):
            retriever.rank(goal)
    assert lean.calls == []


def test_a_goal_as_lean_prints_it_is_searched_by_its_conclusion() -> None:
    """`open_goals` comes back from `lean_check_proof` as Lean displays it:
    hypotheses on their own lines, then a turnstile. Refusing that outright
    made the tool unusable on its most natural input -- a model holding a goal
    had to reword it before Hardy would look at anything.

    The conclusion is taken and its locals become wildcards. Sending `n` as
    written earns `Unknown identifier ``n``` from Loogle -- the name means
    nothing outside the goal that bound it -- so the hypothesis lines are read
    for which names are local, and for nothing else.
    """
    retrieval = importlib.import_module('hardy.retrieval')
    lean = FakeSource(_pinned(retrieval), [_record('Nat.add_comm')])
    retriever = _retriever(retrieval, [lean])

    ranking = retriever.rank('n m : ℕ\nh : n < m\n⊢ n + m = m + n')

    assert lean.calls == [('⊢ _ + _ = _ + _', retrieval.MAX_HITS)]
    assert ranking.query == '⊢ _ + _ = _ + _'
    assert ranking.goal == 'n m : ℕ\nh : n < m\n⊢ n + m = m + n'
    # A pattern with no hypotheses is already a query and passes through whole.
    assert retriever.rank('_ + _ = _ + _').query == '_ + _ = _ + _'
    assert retriever.rank('⊢ True').query == '⊢ True'


def test_only_the_names_the_goal_bound_are_wildcarded() -> None:
    """A local name is meaningless to a search; a global one is the whole point
    of searching. Replacing `Nat.add` because a hypothesis happened to be
    called `Nat` would throw away the only anchor the query has.
    """
    retrieval = importlib.import_module('hardy.retrieval')

    assert retrieval.search_query('xs : List ℕ\n⊢ xs.reverse.reverse = xs') == (
        '⊢ _.reverse.reverse = _'
    )
    # `n` is bound, `Nat.succ` is not, and `nm` is neither `n` nor `m`.
    assert retrieval.search_query('n : ℕ\nnm : ℕ\n⊢ Nat.succ n = n + nm') == (
        '⊢ Nat.succ _ = _ + _'
    )
    # A wrapped type continuing on its own line binds nothing.
    assert retrieval.search_query('h : a very long type\n  continuing here\n⊢ f h') == '⊢ f _'


def test_the_local_environment_keeps_its_signatures_even_when_it_is_not_pinned() -> None:
    """Tightening what pinning requires quietly cost the local search its say.

    An unpinned Lean source is still the environment that will elaborate the
    proof, so its rendering of a type is the one worth showing. Whether the
    ranking can be *replayed* is a separate question, and `reproducible` is
    where it belongs.
    """
    retrieval = importlib.import_module('hardy.retrieval')
    unpinned_lean = retrieval.SourceIdentity(
        name='lean-find', kind='lean_search', corpus='Mathlib 81a5 / toolchain unverified',
        pinned=False,
    )
    lean = FakeSource(unpinned_lean, [_record('Nat.add_comm', 'Nat.add_comm : n + m = m + n')])
    loogle = FakeSource(_unpinned(retrieval), [_record('Nat.add_comm', 'from the internet')])

    ranking = _retriever(retrieval, [loogle, lean]).rank('_ + _ = _ + _')

    assert ranking.premises[0].signature == 'Nat.add_comm : n + m = m + n'
    assert not ranking.reproducible


def test_a_field_label_is_not_a_use_of_the_local_that_shares_its_name() -> None:
    """`{ field := field }` names the field once and the local once. Rewriting
    both produced `{ _ := _ }`, which no source accepts."""
    retrieval = importlib.import_module('hardy.retrieval')

    assert retrieval.search_query('field : Nat\n⊢ { field := field } = expected') == (
        '⊢ { field := _ } = expected'
    )
    assert retrieval.search_query('x : Nat\n⊢ f (y := x) = x') == '⊢ f (y := _) = _'


def test_a_corpus_revision_that_can_move_is_refused() -> None:
    """The escape hatch accepted any string, which re-made the `stable`/
    `nightly` mistake one field over: a branch or tag can be repointed under
    the identity that named it. A git object name is the content."""
    retrieval = importlib.import_module('hardy.retrieval')

    for movable in ('master', 'main', 'v4.32.0', 'nightly', 'not-hex-at-all'):
        with pytest.raises(ValueError, match='git object name'):
            retrieval.LoogleSource(corpus_revision=movable)
    assert retrieval.LoogleSource(corpus_revision='81a5d25').identity.pinned
    assert retrieval.LoogleSource(corpus_revision='81a5d257c8e410db227a6665ed08f64fea08e997')


def test_a_local_inside_string_interpolation_is_still_a_local() -> None:
    """`s!"{x}"` is not literal text all the way through -- the braces hold an
    expression. A plain `"{x}"` is literal, braces included, so the prefix has
    to be read rather than assumed."""
    retrieval = importlib.import_module('hardy.retrieval')

    assert retrieval.search_query('x : Nat\n⊢ s!"{x}" = "0"') == '⊢ s!"{_}" = "0"'
    assert retrieval.search_query('x : Nat\n⊢ m!"a {x} b" = c') == '⊢ m!"a {_} b" = c'
    # The round-seven guarantee, unchanged: an ordinary literal is not touched,
    # braces or no braces.
    assert retrieval.search_query('x : Nat\n⊢ "{x}" = "x"') == '⊢ "{x}" = "x"'


def test_a_self_hosted_loogle_can_name_the_corpus_it_serves() -> None:
    """The endpoint is configurable so a project can run its own against a
    pinned Mathlib -- and hard-coding `pinned=False` made that configuration
    pointless for the only reason anyone would use it. The public instance
    names no revision and stays unpinned."""
    retrieval = importlib.import_module('hardy.retrieval')

    public = retrieval.LoogleSource(fetch=lambda url, timeout: b'{"hits": []}')
    assert not public.identity.pinned

    hosted = retrieval.LoogleSource(
        endpoint='https://loogle.internal/json',
        corpus_revision='81a5d257',
        fetch=lambda url, timeout: b'{"hits": []}',
    )

    assert hosted.identity.pinned
    assert '81a5d257' in hosted.identity.corpus
    assert hosted.identity.corpus != public.identity.corpus


def test_the_budget_a_ranking_reports_is_re_derived_when_it_is_read_back() -> None:
    """`seconds_spent` and `budget_exhausted` sat beside two derived booleans
    while being neither derived nor checked, so a ranking could misreport what
    an experiment spent and still pass every validation.
    """
    retrieval = importlib.import_module('hardy.retrieval')
    readings = iter([0.0, 4.0, 4.0])
    lean = FakeSource(_pinned(retrieval), [_record('Nat.add_comm')], worst_case_seconds=4.0)
    loogle = FakeSource(_unpinned(retrieval), [_record('Nat.mul_comm')], worst_case_seconds=4.0)

    ranking = _retriever(
        retrieval, [lean, loogle], seconds=5, clock=lambda: next(readings)
    ).rank('_ + _ = _ + _')
    payload = json.loads(ranking.model_dump_json())

    assert ranking.budget_exhausted and ranking.seconds_spent == pytest.approx(4.0)
    assert retrieval.PremiseRanking.model_validate(payload).seconds_spent == pytest.approx(4.0)
    for field, wrong in (
        ('seconds_spent', 0.5),
        ('budget_exhausted', False),
        ('run_seconds_remaining', 999.0),
    ):
        with pytest.raises(ValidationError):
            retrieval.PremiseRanking.model_validate(payload | {field: wrong})


def test_a_conclusion_lean_wrapped_over_several_lines_is_rejoined() -> None:
    """Taking only the turnstile line searched a shorter, different
    proposition, and said nothing about having done so -- the same silent
    wrongness as rewriting a string literal.
    """
    retrieval = importlib.import_module('hardy.retrieval')

    # `x` is bound above the turnstile, so rejoining and wildcarding compose:
    # the continuation is picked up and its local is replaced like any other.
    assert retrieval.search_query('x : T\n⊢ SomePredicate\n  x') == '⊢ SomePredicate _'
    assert retrieval.search_query(
        'n : ℕ\n⊢ VeryLongName n +\n    OtherName n =\n    Result n'
    ) == '⊢ VeryLongName _ + OtherName _ = Result _'
    # A blank line ends the goal, and an unindented line starts the next one:
    # neither belongs to this conclusion.
    assert retrieval.search_query('⊢ First\n\n⊢ Second') == '⊢ First'
    assert retrieval.search_query('⊢ First\n⊢ Second') == '⊢ First'


def test_a_duplicate_does_not_push_the_premise_behind_it_down_a_rank() -> None:
    """`[A, A, B]` put B second among the results the source usefully returned.
    Ranking it third lowered its score for a duplicate already discarded, and
    that can change the fused order.
    """
    retrieval = importlib.import_module('hardy.retrieval')
    lean = FakeSource(
        _pinned(retrieval), [_record('A'), _record('A'), _record('B')]
    )

    ranking = _retriever(retrieval, [lean]).rank('_ + _ = _ + _')

    ranks = {premise.name: premise.ranks[0].rank for premise in ranking.premises}
    assert ranks == {'A': 1, 'B': 2}


def test_a_name_too_long_to_be_a_declaration_is_discarded_not_truncated() -> None:
    """Only the signature was bounded, so a name of arbitrary length rode into
    the ranking and crowded genuine premises out of the observation budget on
    its way to meaning nothing. Truncating it would be worse still: a cut name
    is a different name.
    """
    retrieval = importlib.import_module('hardy.retrieval')
    body = json.dumps(
        {
            'hits': [
                {'name': 'A.' + 'x' * retrieval.MAX_NAME_CHARACTERS, 'type': ' : True'},
                {'name': 'Nat.add_comm', 'type': ' : n + m = m + n'},
            ]
        }
    ).encode('utf-8')

    found = retrieval.LoogleSource(fetch=lambda url, timeout: body).search('x', 10)

    assert [record.name for record in found] == ['Nat.add_comm']


def test_a_local_name_inside_a_string_literal_is_left_alone() -> None:
    """Textual substitution changed the proposition rather than the query.

    `⊢ x = "x"` became `⊢ _ = "_"`, which is a different statement about a
    different string -- and both searches would rank premises for it perfectly
    happily. Silently searching the wrong thing is worse than failing to search.
    """
    retrieval = importlib.import_module('hardy.retrieval')

    assert retrieval.search_query('x : String\n⊢ x = "x"') == '⊢ _ = "x"'
    assert retrieval.search_query('s : String\n⊢ f s "s" s = "s s"') == '⊢ f _ "s" _ = "s s"'
    # An escaped quote does not end the literal it appears in.
    assert retrieval.search_query('x : String\n⊢ x = "a \\" x"') == '⊢ _ = "a \\" x"'
    # A char literal was already safe: `'` is an identifier character in Lean,
    # so the quote before it blocks the match.
    assert retrieval.search_query("c : Char\n⊢ c = 'c'") == "⊢ _ = 'c'"


def test_the_local_names_lean_actually_displays_are_recognised() -> None:
    """Two forms went through to the search verbatim, where they mean nothing.

    `x✝` is how Lean shows a hypothesis it had to disambiguate, and it is not
    an identifier, so nothing recognised it. `«foo bar»` is one name, but
    splitting the binder head on whitespace tore it into `«foo` and `bar»`
    before it could be matched. Either way both sources failed on a goal that
    was otherwise perfectly ordinary.
    """
    retrieval = importlib.import_module('hardy.retrieval')

    assert retrieval.search_query('x✝ : ℕ\n⊢ x✝ + 1 = 1 + x✝') == '⊢ _ + 1 = 1 + _'
    assert retrieval.search_query('x✝¹ : ℕ\nx✝ : ℕ\n⊢ x✝¹ + x✝ = x✝') == '⊢ _ + _ = _'
    assert retrieval.search_query('«foo bar» : ℕ\n⊢ «foo bar» = 0') == '⊢ _ = 0'


def test_a_response_hardy_cannot_decode_is_a_failed_source() -> None:
    """`errors="replace"` turned undecodable bytes into `\\ufffd` and handed back
    a signature that reads as ordinary Lean while naming something else --
    altered data recorded as a source that answered successfully. JSON is
    required to be valid Unicode, so a body that is not is a failure.
    """
    retrieval = importlib.import_module('hardy.retrieval')
    body = b'{"hits": [{"name": "Nat.add_comm", "type": " : \xff\xfe"}]}'

    source = retrieval.LoogleSource(fetch=lambda url, timeout: body)

    with pytest.raises(retrieval.RetrievalError, match='UTF-8'):
        source.search('_ + _ = _ + _', 5)


def test_a_goal_written_in_dot_notation_is_not_desugared_and_does_not_pretend_to_be() -> None:
    """A known limit, pinned so nobody reads silence as support.

    `xs.reverse` means `List.reverse xs`, and recovering that needs the type of
    `xs` and a model of how Lean elaborates projections -- an elaborator inside
    a retrieval module, which is well past what this can do soundly. So the
    local is wildcarded like any other and the query goes out as it is;
    measured against the live service, Loogle answers `Unknown identifier
    ``«_».reverse.reverse```, which lands in the provenance as a source that
    did not answer rather than as a ranking of the wrong thing.
    """
    retrieval = importlib.import_module('hardy.retrieval')

    assert retrieval.search_query('xs : List ℕ\n⊢ xs.reverse.reverse = xs') == (
        '⊢ _.reverse.reverse = _'
    )


def test_the_premises_are_bound_to_the_record_that_validates_them() -> None:
    """Otherwise the digest covered only how the ranking was made, not what it
    said: names, scores and source ranks could be swapped wholesale and every
    check still passed.
    """
    retrieval = importlib.import_module('hardy.retrieval')
    lean = FakeSource(_pinned(retrieval), [_record('Nat.add_comm'), _record('Nat.mul_comm')])

    ranking = _retriever(retrieval, [lean]).rank('_ + _ = _ + _')
    payload = json.loads(ranking.model_dump_json())

    assert retrieval.PremiseRanking.model_validate(payload).premises == ranking.premises
    for swapped in (
        [payload['premises'][0] | {'name': 'Not.returned'}],
        [payload['premises'][0] | {'score': 99.0}],
        list(reversed(payload['premises'])),
        [],
    ):
        with pytest.raises(ValidationError):
            retrieval.PremiseRanking.model_validate(payload | {'premises': swapped})


def test_a_bounded_ranking_keeps_the_digest_of_the_premises_it_was_cut_from() -> None:
    """Bounding for the observation budget legitimately drops premises, so the
    digest cannot describe what is left -- and recomputing it would stamp a
    hash over a list no search produced. The truncated view says it is one, and
    the artifact it names holds the ranking the digest is actually over.
    """
    retrieval = importlib.import_module('hardy.retrieval')
    lean = FakeSource(_pinned(retrieval), [_record('Nat.add_comm'), _record('Nat.mul_comm')])

    ranking = _retriever(retrieval, [lean]).rank('_ + _ = _ + _')
    cut = ranking.model_copy(
        update={
            'premises': ranking.premises[:1],
            'observation_truncated': True,
            'output_artifact': 'process/mcp-result-0.json',
        }
    )

    revalidated = retrieval.PremiseRanking.model_validate(json.loads(cut.model_dump_json()))
    assert revalidated.observation_truncated
    assert revalidated.provenance.premises_sha256 == ranking.provenance.premises_sha256
    # A truncated ranking must still name where the whole one went.
    with pytest.raises(ValidationError):
        retrieval.PremiseRanking.model_validate(
            json.loads(cut.model_dump_json()) | {'output_artifact': None}
        )


def test_the_provenance_hashes_what_was_searched_as_well_as_what_was_asked() -> None:
    """Two different goals can reduce to one query, and the ranking answers the
    query. A digest over the goal alone would not describe what produced it.
    """
    retrieval = importlib.import_module('hardy.retrieval')
    lean = FakeSource(_pinned(retrieval), [_record('Nat.add_comm')])

    ranking = _retriever(retrieval, [lean]).rank('h : n < m\n⊢ n + m = m + n')
    payload = json.loads(ranking.model_dump_json())

    assert retrieval.PremiseRanking.model_validate(payload).query == '⊢ n + m = m + n'
    with pytest.raises(ValidationError):
        retrieval.PremiseRanking.model_validate(payload | {'query': '⊢ something else'})


def test_the_budget_is_scoped_to_the_retriever_and_says_so() -> None:
    """A known limit, pinned rather than left for someone to discover.

    Spend lives on the retriever, so a second MCP server started against the
    same run directory begins with a full allowance. `official_checks` in
    `LeanToolRuntime` is scoped exactly the same way and has been since it was
    written; persisting either one is a change to how a run records its own
    consumption, and belongs with both of them at once rather than half-done
    here.
    """
    retrieval = importlib.import_module('hardy.retrieval')
    domain = importlib.import_module('hardy.domain')
    readings = iter([0.0, 4.0])
    lean = FakeSource(_pinned(retrieval), [_record('Nat.add_comm')])

    first = _retriever(retrieval, [lean], seconds=5, clock=lambda: next(readings))
    first.rank('_ + _ = _ + _')
    assert first.seconds_remaining == pytest.approx(1.0)

    restarted = retrieval.PremiseRetriever(
        sources=[lean], limits=domain.RunLimits(retrieval_seconds=5), clock=lambda: 0.0
    )

    assert restarted.seconds_remaining == 5.0


def _environment(domain, manifest_sha256='b' * 64):
    return domain.EnvironmentIdentity(
        lean_version='4.32.0',
        lean_commit='8c9756b',
        mathlib_revision='81a5d257',
        lake_manifest_sha256=manifest_sha256,
    )


def _index_source(retrieval, domain, project, manifest_sha256='b' * 64):
    declarations = importlib.import_module('hardy.declarations')
    return retrieval.DeclarationIndexSource(
        declarations.DeclarationIndex(project),
        environment=_environment(domain, manifest_sha256),
    )


def test_the_index_source_searches_the_sources_the_run_is_frozen_under(tmp_path) -> None:
    """The motivating case from the graded failure: `IsSimpleGroup` is text in
    Mathlib's own sources, and the index answers about it without Lean running
    -- where `#find`, measured on the pinned toolchain, never answered at all.
    """
    retrieval = importlib.import_module('hardy.retrieval')
    domain = importlib.import_module('hardy.domain')
    package = tmp_path / '.lake' / 'packages' / 'mathlib' / 'Mathlib'
    package.mkdir(parents=True)
    (package / 'Simple.lean').write_text(
        'class IsSimpleGroup (G : Type u) : Prop where\n', encoding='utf-8'
    )

    source = _index_source(retrieval, domain, tmp_path)
    asked = source.query_for('⊢ IsSimpleGroup _')

    assert asked == 'IsSimpleGroup'
    assert [record.name for record in source.search(asked, 5)] == ['IsSimpleGroup']
    for identity in ('81a5d257', 'b' * 64):
        assert identity in source.identity.corpus
    # The corpus names the algorithm as well as the text: the order a ranking
    # replays under depends on the scan grammar and the search ordering, which
    # are Hardy code that can move while the sources stand still. Without this
    # the identity stayed byte-identical across two different algorithms and
    # `reproducible` promised a replay neither could give the other.
    declarations = importlib.import_module('hardy.declarations')
    assert declarations.INDEX_ALGORITHM in source.identity.corpus


def test_the_index_source_extracts_only_the_constants_a_name_index_can_use() -> None:
    """The shared query is a type pattern; a name index can use the names in it
    and nothing else. The wildcards standing where locals were, the turnstile
    and the operators fall away, keywords are dropped, and what remains is
    deduplicated in order -- so the provenance records the question this source
    actually ran rather than one it cannot parse.
    """
    retrieval = importlib.import_module('hardy.retrieval')
    domain = importlib.import_module('hardy.domain')

    source = _index_source(retrieval, domain, None)

    assert source.query_for('⊢ Nat.succ _ = _ + _') == 'Nat.succ'
    assert source.query_for('⊢ IsSimpleGroup _ → IsCyclic _') == 'IsSimpleGroup IsCyclic'
    assert source.query_for('⊢ Nat.succ _ = Nat.succ _') == 'Nat.succ'
    assert source.query_for('⊢ fun _ => Continuous _') == 'Continuous'
    assert source.query_for('⊢ _ + _ = _ + _') == ''
    # A binder the goal's conclusion introduces is not a hypothesis line, so
    # `search_query` leaves it standing -- and substring-matching a one- or
    # two-character token floods the depth with everything that contains the
    # letter. Too short to discriminate means dropped, binder or not: `Eq`
    # as a substring is half of Mathlib.
    assert source.query_for('⊢ ∀ n : Nat, n + 0 = n') == 'Nat'
    assert source.query_for('⊢ Eq _ _') == ''
    # Text inside a string literal is not a constant the proposition uses --
    # `search_query` deliberately leaves literals intact, so the extraction
    # has to look past them itself. Interpolation holes are expressions and
    # their names still count.
    assert source.query_for('⊢ _ = "Nat.succ"') == ''
    assert source.query_for('⊢ Continuous _ ∧ _ = "Nat.succ"') == 'Continuous'
    assert source.query_for('⊢ s!"{Continuous _}" = _') == 'Continuous'


def test_a_pure_shape_query_is_this_source_refusing_and_loogle_answering(tmp_path) -> None:
    """`#find` took `_ + _ = _ + _`; a name index cannot, and must say so
    rather than matching everything or nothing. The refusal is recorded
    against the source while Loogle still shapes the ranking.
    """
    retrieval = importlib.import_module('hardy.retrieval')
    domain = importlib.import_module('hardy.domain')
    index_source = _index_source(retrieval, domain, tmp_path)
    loogle = FakeSource(_unpinned(retrieval), [_record('Nat.add_comm')])

    ranking = _retriever(retrieval, [index_source, loogle]).rank('_ + _ = _ + _')

    assert [premise.name for premise in ranking.premises] == ['Nat.add_comm']
    assert not ranking.complete
    refused = next(
        item for item in ranking.provenance.sources
        if item.identity.name == 'declaration-index'
    )
    assert not refused.answered
    assert 'Loogle' in (refused.detail or '')


def test_the_index_source_is_pinned_only_when_the_manifest_is_the_frozen_one(
    tmp_path,
) -> None:
    """`load_runtime` takes the project from `HARDY_CONFIG` and the environment
    identity from the frozen claim, and never checks that they are the same
    thing. So the index can read one Lake project while the corpus identity
    names another -- a ranking claiming to be replayable against a corpus it
    did not search. No toolchain pin is demanded, unlike the `#find` source
    this replaces: no compiler runs, so the corpus is the text alone.
    """
    retrieval = importlib.import_module('hardy.retrieval')
    domain = importlib.import_module('hardy.domain')
    manifest = b'{"packages": [{"name": "mathlib", "rev": "81a5d257"}]}'
    (tmp_path / 'lake-manifest.json').write_bytes(manifest)

    mismatched = _index_source(retrieval, domain, tmp_path)
    assert not mismatched.identity.pinned
    assert 'NOT the project searched' in mismatched.identity.corpus

    matched = _index_source(
        retrieval, domain, tmp_path, hashlib.sha256(manifest).hexdigest()
    )
    assert matched.identity.pinned
    assert 'NOT the project searched' not in matched.identity.corpus

    # And with no project at all there is nothing to pin.
    assert not _index_source(retrieval, domain, None).identity.pinned


def test_the_index_source_declares_its_cold_bound_until_the_read_has_happened(
    tmp_path,
) -> None:
    """The admission meter spends the declared figure, and a cold index walks
    every source file the packages ship where a warm one reads memory. One
    figure for both either overcharges every later call or lets the first one
    overrun the budget after passing the check meant to stop it."""
    retrieval = importlib.import_module('hardy.retrieval')
    domain = importlib.import_module('hardy.domain')

    source = _index_source(retrieval, domain, tmp_path)

    assert source.worst_case_seconds == retrieval.DECLARATION_INDEX_COLD_SECONDS
    source.search('anything', 5)
    assert source.worst_case_seconds == retrieval.DECLARATION_INDEX_WARM_SECONDS
    assert (
        retrieval.DECLARATION_INDEX_WARM_SECONDS
        < retrieval.DECLARATION_INDEX_COLD_SECONDS
    )


def test_the_retriever_records_what_each_source_was_actually_asked() -> None:
    """One query, two spellings, and the provenance must carry each source's
    own -- recording only the shared one would name a query the pinned source
    never ran."""
    retrieval = importlib.import_module('hardy.retrieval')
    lean = FakeSource(_pinned(retrieval), [_record('Nat.add_comm')])
    lean.query_for = lambda query: query.removeprefix('⊢ ')
    loogle = FakeSource(_unpinned(retrieval), [_record('Nat.mul_comm')])

    ranking = _retriever(retrieval, [lean, loogle]).rank('⊢ _ + _ = _ + _')

    asked = {item.identity.name: item.query for item in ranking.provenance.sources}
    assert asked == {'lean-find': '_ + _ = _ + _', 'loogle': '⊢ _ + _ = _ + _'}
    assert ranking.query == '⊢ _ + _ = _ + _'


def test_two_rankings_at_once_cannot_each_spend_the_whole_budget() -> None:
    """`rank` read `_spent`, ran the sources, then wrote it back. Two MCP calls
    arriving together both read the same figure before either wrote, so each
    admitted sources against a budget the other was already spending. The
    staged transport gates its dispatch; the MCP server does not.
    """
    retrieval = importlib.import_module('hardy.retrieval')
    domain = importlib.import_module('hardy.domain')
    import threading

    started = threading.Barrier(2, timeout=5)
    ticks = iter(float(tick) for tick in range(0, 200, 4))
    guard = threading.Lock()

    class Concurrent:
        identity = _pinned(retrieval)
        worst_case_seconds = 4.0

        def query_for(self, query):
            return query

        def search(self, goal, limit):
            # Both threads are inside `rank` before either finishes, which is
            # exactly the interleaving the budget has to survive.
            started.wait()
            return (_record('Nat.add_comm'),)

    def clock():
        with guard:
            return next(ticks)

    retriever = retrieval.PremiseRetriever(
        sources=[Concurrent()], limits=domain.RunLimits(retrieval_seconds=4), clock=clock
    )
    results: list[object] = []

    def rank():
        try:
            results.append(retriever.rank('_ + _ = _ + _'))
        except threading.BrokenBarrierError:
            # The loser never reaches the source, which is the point.
            results.append(None)

    threads = [threading.Thread(target=rank) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    admitted = [item for item in results if item is not None and not item.budget_exhausted]
    assert len(admitted) == 1, 'both calls were admitted against a budget sized for one'


def test_an_index_signature_outranks_a_remote_rendering_of_the_same_name() -> None:
    """The declaration index reads the head line the local sources actually
    hold, so its rendering wins over Loogle's for the same reason Lean's own
    used to: it is what the model's environment will elaborate."""
    retrieval = importlib.import_module('hardy.retrieval')
    local = retrieval.SourceIdentity(
        name='declaration-index',
        kind='declaration_index',
        corpus='Mathlib 81a5d257 sources',
        pinned=True,
    )
    index = FakeSource(local, [_record('Nat.add_comm', 'theorem add_comm : n + m = m + n')])
    loogle = FakeSource(_unpinned(retrieval), [_record('Nat.add_comm', 'from the internet')])

    ranking = _retriever(retrieval, [loogle, index]).rank('_ + _ = _ + _')

    assert ranking.premises[0].signature == 'theorem add_comm : n + m = m + n'


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
    # Twice the deadline: the read in flight when the deadline passes is
    # bounded by the socket timeout, not by the deadline, and the budget is
    # admitted against what can happen rather than what was intended.
    assert source.worst_case_seconds == 14.0


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


def test_a_response_carrying_no_usable_hit_list_is_a_failed_source() -> None:
    """`{"hits": "nope"}` is Loogle's contract having changed or broken, and
    reading it as an empty result would file a protocol failure under "found
    nothing" -- leaving the ranking `complete` on a source that never answered.
    An actually empty `hits` is still an answer.
    """
    retrieval = importlib.import_module('hardy.retrieval')

    for body in (b'{"hits": "nope"}', b'{"count": 0}', b'{"hits": {"a": 1}}'):
        with pytest.raises(retrieval.RetrievalError, match='hits'):
            retrieval.LoogleSource(fetch=lambda url, timeout, body=body: body).search('x', 10)
    assert retrieval.LoogleSource(fetch=lambda url, timeout: b'{"hits": []}').search('x', 10) == ()


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


def test_the_default_source_set_puts_the_pinned_local_index_first() -> None:
    """Which is what decides, when the budget runs short, that the source
    dropped is the one whose answers could not be replayed anyway. `#find` is
    deliberately absent: measured on the pinned toolchain it never answered
    while costing a full process timeout per ranking."""
    retrieval = importlib.import_module('hardy.retrieval')
    domain = importlib.import_module('hardy.domain')

    class Service:
        lean_project = None
        environment = _environment(domain)

    retriever = retrieval.build_retriever(Service(), domain.RunLimits())
    kinds = [source.identity.kind for source in retriever._sources]

    assert kinds == ['declaration_index', 'loogle']


def test_a_caller_can_share_one_index_between_search_and_ranking() -> None:
    """`build_retriever` takes the index the plain `search_declarations` tool
    already holds, so a session pays the one-time source scan once rather than
    once per surface."""
    retrieval = importlib.import_module('hardy.retrieval')
    declarations = importlib.import_module('hardy.declarations')
    domain = importlib.import_module('hardy.domain')

    class Service:
        lean_project = None
        environment = _environment(domain)

    shared = declarations.DeclarationIndex(None)
    retriever = retrieval.build_retriever(Service(), domain.RunLimits(), shared)

    assert retriever._sources[0]._index is shared


def test_the_retrieval_budget_is_a_run_limit_like_every_other() -> None:
    domain = importlib.import_module('hardy.domain')

    assert domain.RunLimits().retrieval_seconds > 0
    assert domain.RunLimits(retrieval_seconds=1).retrieval_seconds == 1
