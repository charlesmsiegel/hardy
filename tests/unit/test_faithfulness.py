"""The statement faithfulness gate, read on its own.

What these pin is the property the gate exists for: the reader is asked about
the two texts that have to say the same thing, it is asked from a thread that
never saw the formalization conversation, and nothing it can answer -- an
agreement, a refusal, or no answer at all -- is turned into a pass it did not
give.
"""

from __future__ import annotations

import importlib
import json
from datetime import UTC, datetime
from uuid import UUID

NOW = datetime(2026, 8, 27, tzinfo=UTC)
RUN_ID = UUID('12345678-1234-5678-1234-567812345678')


def _claim(domain, original='Every prime above two is odd.'):
    return domain.freeze_claim(
        original,
        domain.FormalizationProposal(
            restatement='Primes exceeding two are odd.',
            domains=('natural numbers',),
            quantifiers=('for all p',),
            assumptions=('p is prime',),
            interpretation_choices=('read "above two" as 2 < p',),
            theorem_name='odd_of_prime_gt_two',
            binders='(p : Nat)',
            proposition='p.Prime -> 2 < p -> Odd p',
        ),
        domain.EnvironmentIdentity(
            lean_version='4.32.0',
            lean_commit='8c9756b',
            mathlib_revision='81a5d257',
            lake_manifest_sha256='b' * 64,
            imports=('Mathlib',),
        ),
        NOW,
    )


class _Runtime:
    """A runtime that records how the reader was started and what it was asked."""

    def __init__(self, answer):
        self.answer = answer
        self.starts = []
        self.asked = []

    def start(self, *, model, run_dir, claim):
        self.starts.append({'model': model, 'claim': claim})
        return object()

    def run_structured(self, thread, stage, prompt, output_type):
        self.asked.append((stage, prompt, output_type))
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


def _store(tmp_path):
    storage = importlib.import_module('hardy.storage')
    return storage.RunStore.create(tmp_path, 'gate', now=NOW, run_id=RUN_ID)


def _review(domain, **overrides):
    values = dict(
        formalization_entails_claim=True,
        claim_entails_formalization=True,
        divergences=(),
        notes='',
    )
    values.update(overrides)
    return domain.FaithfulnessReview(**values)


def test_the_reader_is_asked_about_the_claim_and_the_lean_and_nothing_else(tmp_path) -> None:
    """The formalizer's own gloss is withheld on purpose.

    A reader handed the restatement and the interpretation choices is reading
    the translation through the account that produced it, which is exactly the
    shared context this gate is built to defeat.
    """
    domain = importlib.import_module('hardy.domain')
    faithfulness = importlib.import_module('hardy.faithfulness')
    claim = _claim(domain)
    runtime = _Runtime(_review(domain))

    faithfulness.review_translation(
        claim,
        runtime=runtime,
        model='reviewer-model',
        store=_store(tmp_path),
        phase=domain.RunPhase.AWAITING_APPROVAL,
    )

    stage, prompt, output_type = runtime.asked[0]
    assert stage == 'faithfulness'
    assert output_type is domain.FaithfulnessReview
    assert claim.original_text in prompt
    assert 'p.Prime -> 2 < p -> Odd p' in prompt
    assert claim.proposal.restatement not in prompt
    for choice in claim.proposal.interpretation_choices:
        assert choice not in prompt


def test_the_reader_runs_on_its_own_thread_with_no_lean_tools(tmp_path) -> None:
    domain = importlib.import_module('hardy.domain')
    faithfulness = importlib.import_module('hardy.faithfulness')
    runtime = _Runtime(_review(domain))

    faithfulness.review_translation(
        _claim(domain),
        runtime=runtime,
        model='a-second-model',
        store=_store(tmp_path),
        phase=domain.RunPhase.AWAITING_APPROVAL,
    )

    assert len(runtime.starts) == 1
    # `claim=None` is what withholds the Lean tools: see `ClaudeStagedRuntime.
    # start`. A reader that can elaborate the statement can be convinced the
    # statement is right because Lean accepted it, which is a different question.
    assert runtime.starts[0] == {'model': 'a-second-model', 'claim': None}


def test_an_agreement_is_recorded_as_an_artifact_and_in_the_trajectory(tmp_path) -> None:
    domain = importlib.import_module('hardy.domain')
    faithfulness = importlib.import_module('hardy.faithfulness')
    claim = _claim(domain)
    store = _store(tmp_path)

    verdict = faithfulness.review_translation(
        claim,
        runtime=_Runtime(_review(domain, notes='Same statement.')),
        model='reviewer-model',
        store=store,
        phase=domain.RunPhase.AWAITING_APPROVAL,
    )

    assert verdict.agreed
    assert verdict.claim_sha256 == claim.content_hash
    assert verdict.reviewer_model == 'reviewer-model'
    saved = domain.FaithfulnessVerdict.model_validate_json(
        (store.path / 'faithfulness.json').read_text(encoding='utf-8')
    )
    assert saved == verdict
    events = [
        json.loads(line)
        for line in store.trajectory_path.read_text(encoding='utf-8').splitlines()
    ]
    recorded = [event for event in events if event['kind'] == 'faithfulness.verdict']
    assert len(recorded) == 1
    assert recorded[0]['payload']['outcome'] == 'agreed'
    assert recorded[0]['payload']['claim_sha256'] == claim.content_hash


def test_a_listed_divergence_disputes_the_translation_however_the_flags_read(tmp_path) -> None:
    """A reader that names a problem has found one.

    The flags and the list can disagree, and when they do the list wins: a
    reader answering "yes, and also here is what is wrong with it" is not an
    agreement, and reading it as one is how a gate becomes decorative.
    """
    domain = importlib.import_module('hardy.domain')
    faithfulness = importlib.import_module('hardy.faithfulness')
    store = _store(tmp_path)

    verdict = faithfulness.review_translation(
        _claim(domain),
        runtime=_Runtime(
            _review(domain, divergences=('the Lean fixes p = 3 rather than any prime',))
        ),
        model='reviewer-model',
        store=store,
        phase=domain.RunPhase.AWAITING_APPROVAL,
    )

    assert not verdict.agreed
    assert verdict.outcome is domain.FaithfulnessOutcome.DISPUTED
    assert faithfulness.dispute_gaps(verdict) == (
        'The independent faithfulness review disputed the translation: '
        'the Lean fixes p = 3 rather than any prime',
    )
    assert (store.path / 'faithfulness.json').exists()


def test_a_failed_entailment_disputes_the_translation_with_no_list(tmp_path) -> None:
    domain = importlib.import_module('hardy.domain')
    faithfulness = importlib.import_module('hardy.faithfulness')

    verdict = faithfulness.review_translation(
        _claim(domain),
        runtime=_Runtime(_review(domain, claim_entails_formalization=False)),
        model='reviewer-model',
        store=_store(tmp_path),
        phase=domain.RunPhase.AWAITING_APPROVAL,
    )

    assert not verdict.agreed
    assert faithfulness.dispute_gaps(verdict) == (
        'The independent faithfulness review did not accept the translation.',
    )


def test_a_reader_that_cannot_be_read_is_not_a_pass(tmp_path) -> None:
    """Fail-closed. An unobtainable review is a different fact from a refusal
    and gets the same treatment, because neither one is an agreement."""
    domain = importlib.import_module('hardy.domain')
    faithfulness = importlib.import_module('hardy.faithfulness')
    store = _store(tmp_path)

    verdict = faithfulness.review_translation(
        _claim(domain),
        runtime=_Runtime(ValueError('faithfulness turn returned malformed structured output')),
        model='reviewer-model',
        store=store,
        phase=domain.RunPhase.AWAITING_APPROVAL,
    )

    assert not verdict.agreed
    assert verdict.outcome is domain.FaithfulnessOutcome.UNAVAILABLE
    assert verdict.review is None
    assert 'malformed structured output' in verdict.detail
    assert 'could not be obtained' in faithfulness.dispute_gaps(verdict)[0]
    assert (store.path / 'faithfulness.json').exists()


def test_a_verdict_cannot_claim_an_agreement_its_review_did_not_give() -> None:
    domain = importlib.import_module('hardy.domain')
    pytest = importlib.import_module('pytest')
    validation_error = importlib.import_module('pydantic').ValidationError

    with pytest.raises(validation_error, match='does not follow'):
        domain.FaithfulnessVerdict(
            claim_sha256='a' * 64,
            reviewer_model='reviewer-model',
            prompt_sha256='d' * 64,
            outcome=domain.FaithfulnessOutcome.AGREED,
            review=_review(domain, divergences=('the domain is wrong',)),
        )


def test_an_unavailable_verdict_must_say_why_and_carry_no_answer() -> None:
    domain = importlib.import_module('hardy.domain')
    pytest = importlib.import_module('pytest')
    validation_error = importlib.import_module('pydantic').ValidationError

    with pytest.raises(validation_error, match='must say why'):
        domain.FaithfulnessVerdict(
            claim_sha256='a' * 64,
            reviewer_model='reviewer-model',
            prompt_sha256='d' * 64,
            outcome=domain.FaithfulnessOutcome.UNAVAILABLE,
        )
    with pytest.raises(validation_error, match='carries no answer'):
        domain.FaithfulnessVerdict(
            claim_sha256='a' * 64,
            reviewer_model='reviewer-model',
            prompt_sha256='d' * 64,
            outcome=domain.FaithfulnessOutcome.UNAVAILABLE,
            detail='the provider refused the request',
            review=_review(domain),
        )
