"""The bounded-observation policy of the staged Lean tool runtime.

Each bound keeps its own cut -- a check drops process output and then its
excerpts, a search excerpts signatures and is marked truncated, an inspection
excerpts signatures and never refuses -- over one rule: the whole result is
recorded first, and a view that still does not fit is refused.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest

from hardy.formal.contracts import EnvironmentIdentity, FormalizationProposal, freeze_claim
from hardy.formal.lean import (
    DeclarationInspection,
    DeclarationRecord,
    DeclarationSearch,
    LeanCheckResult,
    LeanDiagnostic,
)
from hardy.formal.tools import EXCERPT_CHARACTERS, EXCERPT_ITEMS, LeanToolRuntime
from hardy.foundation.process import ProcessResult
from hardy.workflows.storage import RunStore

NOW = datetime(2026, 7, 24, tzinfo=UTC)
CLAIM = freeze_claim(
    'Two equals two.',
    FormalizationProposal(
        restatement='r', domains=(), quantifiers=(), assumptions=(), interpretation_choices=(),
        theorem_name='two_eq_two', binders='', proposition='2 = 2',
    ),
    EnvironmentIdentity(
        lean_version='4.32.0', lean_commit='8c9756b', mathlib_revision='81a5d257',
        lake_manifest_sha256='b' * 64,
    ),
    NOW,
)


def _check(stdout: str = '', messages: int = 0, goals: int = 0) -> LeanCheckResult:
    return LeanCheckResult(
        success=False,
        diagnostics=tuple(LeanDiagnostic(severity='error', message='m' * 1000) for _ in range(messages)),
        open_goals=tuple('g' * 1000 for _ in range(goals)),
        process=ProcessResult(
            argv=('lean',), cwd='.', returncode=1, stdout=stdout, stderr='',
            timed_out=False, output_overflow=False, duration_ms=1,
        ),
        source_sha256='c' * 64,
        toolchain=CLAIM.environment,
    )


class _Service:
    """Scratch and inspection answers, and a record of what was asked."""

    def __init__(self, scratch: LeanCheckResult, inspection: DeclarationInspection) -> None:
        self.scratch = scratch
        self.inspection = inspection
        self.asked: list[object] = []

    def check_proof(self, claim, proof_body, allowed=()):
        raise AssertionError('no official check is spent here')

    def check_scratch(self, source):
        self.asked.append(source)
        return self.scratch

    def inspect_declarations(self, names):
        self.asked.append(names)
        return self.inspection


def _runtime(tmp_path, observation_bytes, service=None) -> LeanToolRuntime:
    store = RunStore.create(tmp_path, 'tools', now=NOW, run_id=UUID(int=1))
    return LeanToolRuntime(
        claim=CLAIM, service=service, store=store, official_checks=1,
        observation_bytes=observation_bytes,
    )


def test_a_check_that_fits_is_returned_as_it_is(tmp_path) -> None:
    result = _check(stdout='short')

    assert _runtime(tmp_path, 100_000).bound_check(result) is result


def test_a_check_keeps_excerpts_of_its_leading_diagnostics_and_goals(tmp_path) -> None:
    runtime = _runtime(tmp_path, 8_000)

    bounded = runtime.bound_check(_check(stdout='x' * 50_000, messages=6, goals=6))

    assert bounded.process.stdout == ''
    assert [len(item.message) for item in bounded.diagnostics] == [EXCERPT_CHARACTERS] * EXCERPT_ITEMS
    assert [len(goal) for goal in bounded.open_goals] == [EXCERPT_CHARACTERS] * EXCERPT_ITEMS
    assert bounded.output_artifact == 'process/mcp-lean-0.json'
    full = LeanCheckResult.model_validate_json((runtime.store.path / bounded.output_artifact).read_text())
    assert full.process.stdout == 'x' * 50_000


def test_a_check_whose_excerpts_do_not_fit_keeps_only_its_verdict(tmp_path) -> None:
    bounded = _runtime(tmp_path, 1_200).bound_check(_check(stdout='x' * 5_000, messages=6, goals=6))

    assert bounded.diagnostics == () and bounded.open_goals == ()
    assert bounded.observation_truncated
    assert len(bounded.model_dump_json().encode('utf-8')) <= 1_200


def test_a_check_whose_envelope_cannot_fit_is_refused(tmp_path) -> None:
    with pytest.raises(ValueError, match='smaller than the result envelope'):
        _runtime(tmp_path, 100).bound_check(_check(stdout='x' * 5_000))


def test_an_inspection_is_excerpted_and_returned_even_over_budget(tmp_path) -> None:
    inspection = DeclarationInspection(
        resolved=tuple(DeclarationRecord(name=f'n{index}', signature='s' * 1000) for index in range(3)),
        unavailable=('u' * 5_000,),
    )

    bounded = _runtime(tmp_path, 1_000).bound_inspection(inspection)

    assert [len(item.signature) for item in bounded.resolved] == [EXCERPT_CHARACTERS] * 3
    assert bounded.output_artifact == 'process/mcp-result-0.json'
    assert len(bounded.model_dump_json().encode('utf-8')) > 1_000


def test_a_search_is_excerpted_and_marked_truncated_or_refused(tmp_path) -> None:
    search = DeclarationSearch(
        query='q', results=(DeclarationRecord(name='n', signature='s' * 2_000),), truncated=False,
        success=True, timed_out=False, diagnostics=(),
    )

    bounded = _runtime(tmp_path, 1_000).bound_search(search)

    assert bounded.truncated and bounded.observation_truncated
    assert len(bounded.results[0].signature) == EXCERPT_CHARACTERS
    with pytest.raises(ValueError, match='smaller than the result envelope'):
        _runtime(tmp_path / 'small', 100).bound_search(search)


def test_scratch_and_inspection_are_bounded_and_spend_no_check(tmp_path) -> None:
    inspection = DeclarationInspection(resolved=(), unavailable=('Nope',))
    service = _Service(_check(stdout='x' * 5_000), inspection)
    runtime = _runtime(tmp_path, 1_200, service)

    scratch = runtime.check_scratch('#check Nat')
    inspected = runtime.inspect_declarations(['Nope'])

    assert scratch.observation_truncated
    assert inspected is inspection
    assert service.asked == ['#check Nat', ('Nope',)]
    assert runtime.remaining_official_checks == 1
