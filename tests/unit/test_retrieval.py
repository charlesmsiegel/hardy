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

    asked = lean.calls[0][1]
    assert asked > 3 and asked >= retrieval.FUSION_DEPTH_FLOOR
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

    assert lean.calls == [('_ + _ = _ + _', retrieval.FUSION_DEPTH_FLOOR)]
    assert retrieval.FUSION_DEPTH_FLOOR == retrieval.RRF_K + 1
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

    assert lean.calls == [('⊢ _ + _ = _ + _', retrieval.FUSION_DEPTH_FLOOR)]
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


class _Service:
    """A `LeanService` as far as retrieval is concerned."""

    lean_project = None

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
    # The Lean deadline plus what `run_process` may spend stopping a child that
    # reached it: one bounded `wait` and one bounded `join` per output reader.
    # Declaring the deadline alone let an admitted search overrun the budget.
    process = importlib.import_module('hardy.process')
    assert source.worst_case_seconds == 30.0 + process.MAX_TEARDOWN_SECONDS
    assert process.MAX_TEARDOWN_SECONDS == process.TEARDOWN_SECONDS * 3
    # Every toolchain identity that can move a `#find` result, `lean_commit`
    # included: two builds can display one version and be different Leans.
    for identity in ('81a5d257', '4.32.0', '8c9756b', 'b' * 64):
        assert identity in source.identity.corpus
    # `search_declarations` accepts 1..20, and a ranking may ask for more.
    source.search('_ + _ = _ + _', 40)
    assert service.calls == [('_ + _ = _ + _', 5), ('_ + _ = _ + _', 20)]


def test_the_corpus_identity_names_the_toolchain_that_will_actually_run(tmp_path) -> None:
    """`cli._environment_identity` hard-codes `lean_version` and `lean_commit`,
    so those two fields are asserted rather than measured. Claiming `pinned` on
    them alone would be the exact failure this module exists to prevent: a
    ranking promising it can be replayed on evidence nobody checked.

    The project's `lean-toolchain` is what `elan` pins the compiler with, and
    `chat.py` already reads it for this reason. Without it there is no evidence
    of which Lean runs, so the source is not pinned.
    """
    retrieval = importlib.import_module('hardy.retrieval')
    lean_module = importlib.import_module('hardy.lean')
    domain = importlib.import_module('hardy.domain')

    service = _Service(domain, lean_module)
    unverified = retrieval.LeanSearchSource(service, limits=domain.RunLimits())
    assert not unverified.identity.pinned

    service.lean_project = tmp_path
    (tmp_path / 'lean-toolchain').write_text('leanprover/lean4:v4.32.0\n', encoding='utf-8')
    manifest = b'{"packages": []}'
    (tmp_path / 'lake-manifest.json').write_bytes(manifest)
    service.environment = service.environment.model_copy(
        update={'lake_manifest_sha256': hashlib.sha256(manifest).hexdigest()}
    )
    verified = retrieval.LeanSearchSource(service, limits=domain.RunLimits())

    assert verified.identity.pinned
    corpus = verified.identity.corpus
    assert 'leanprover/lean4:v4.32.0' in corpus
    # A project pinned to a different compiler is a different corpus, even with
    # the same Mathlib manifest beside it. Captured above rather than compared
    # against `verified.identity` again -- the identity is read from the file
    # each time, so both sides would see the rewrite.
    (tmp_path / 'lean-toolchain').write_text('leanprover/lean4:v4.33.0\n', encoding='utf-8')
    moved = retrieval.LeanSearchSource(service, limits=domain.RunLimits())
    assert moved.identity.corpus != corpus


def test_a_project_that_is_not_the_one_the_claim_was_frozen_against_is_not_pinned(
    tmp_path,
) -> None:
    """`load_runtime` takes the project from `HARDY_CONFIG` and the environment
    identity from the frozen claim, and never checks that they are the same
    thing. So `#find` can run in one Lake project while the corpus identity
    names another -- a ranking claiming to be replayable against a corpus it
    did not search.
    """
    retrieval = importlib.import_module('hardy.retrieval')
    lean_module = importlib.import_module('hardy.lean')
    domain = importlib.import_module('hardy.domain')
    manifest = b'{"packages": [{"name": "mathlib", "rev": "81a5d257"}]}'

    service = _Service(domain, lean_module)
    service.lean_project = tmp_path
    (tmp_path / 'lean-toolchain').write_text('leanprover/lean4:v4.32.0\n', encoding='utf-8')
    (tmp_path / 'lake-manifest.json').write_bytes(manifest)

    # The claim's environment names a manifest hash of 'b' * 64, which is not
    # this project's, so the toolchain being readable is not enough.
    assert not retrieval.LeanSearchSource(service, limits=domain.RunLimits()).identity.pinned

    service.environment = service.environment.model_copy(
        update={'lake_manifest_sha256': hashlib.sha256(manifest).hexdigest()}
    )
    matched = retrieval.LeanSearchSource(service, limits=domain.RunLimits())

    assert matched.identity.pinned


def test_the_lean_source_strips_the_turnstile_that_find_does_not_take() -> None:
    """One query, two syntaxes, and each source speaks its own.

    Loogle takes `⊢ p` as a conclusion filter -- verified against the live
    service. Mathlib's `#find t` instead matches the *result type* directly
    (`#find _ + _ = _ + _`), so the turnstile is not merely redundant there but
    unsupported. Passing it through meant the pinned source failed on exactly
    the input the tool was built for, leaving the ranking to the unpinned one.

    Stripping loses nothing: `#find`'s bare term and Loogle's `⊢` mean the same
    search.
    """
    retrieval = importlib.import_module('hardy.retrieval')
    lean_module = importlib.import_module('hardy.lean')
    domain = importlib.import_module('hardy.domain')
    service = _Service(domain, lean_module, results=[_record('Nat.add_comm')])

    source = retrieval.LeanSearchSource(service, limits=domain.RunLimits())

    assert source.query_for('⊢ _ + _ = _ + _') == '_ + _ = _ + _'
    assert source.query_for('_ + _ = _ + _') == '_ + _ = _ + _'
    # And the retriever records what it actually sent, per source, rather than
    # the shared spelling neither of them may have received.
    lean = FakeSource(_pinned(retrieval), [_record('Nat.add_comm')])
    lean.query_for = lambda query: query.removeprefix('⊢ ')
    loogle = FakeSource(_unpinned(retrieval), [_record('Nat.mul_comm')])

    ranking = _retriever(retrieval, [lean, loogle]).rank('⊢ _ + _ = _ + _')

    asked = {item.identity.name: item.query for item in ranking.provenance.sources}
    assert asked == {'lean-find': '_ + _ = _ + _', 'loogle': '⊢ _ + _ = _ + _'}
    assert ranking.query == '⊢ _ + _ = _ + _'


def test_a_toolchain_that_can_change_under_the_same_name_is_not_a_pin(tmp_path) -> None:
    """`stable` and `nightly` are elan aliases, not toolchains.

    The compiler behind one changes while the file, the corpus string and the
    provenance digest all stay identical -- so a ranking would promise to
    replay on a Lean that no longer exists. A pin has to name a version that
    cannot move.
    """
    retrieval = importlib.import_module('hardy.retrieval')
    lean_module = importlib.import_module('hardy.lean')
    domain = importlib.import_module('hardy.domain')
    manifest = b'{"packages": []}'

    service = _Service(domain, lean_module)
    service.lean_project = tmp_path
    (tmp_path / 'lake-manifest.json').write_bytes(manifest)
    service.environment = service.environment.model_copy(
        update={'lake_manifest_sha256': hashlib.sha256(manifest).hexdigest()}
    )

    def pinned_with(toolchain):
        (tmp_path / 'lean-toolchain').write_text(toolchain, encoding='utf-8')
        return retrieval.LeanSearchSource(service, limits=domain.RunLimits()).identity.pinned

    for immutable in (
        'leanprover/lean4:v4.32.0\n',
        'leanprover/lean4:v4.33.0-rc1',
        'leanprover/lean4:nightly-2026-01-15',
    ):
        assert pinned_with(immutable), immutable
    for movable in (
        'leanprover/lean4:stable',
        'leanprover/lean4:nightly',
        'stable',
        'my-local-toolchain',
    ):
        assert not pinned_with(movable), movable


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
