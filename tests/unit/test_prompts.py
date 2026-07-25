import importlib
from datetime import UTC, datetime


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
    return domain.freeze_claim(
        'Two equals two.',
        proposal,
        environment,
        datetime(2026, 7, 24, tzinfo=UTC),
    )


def test_versioned_proof_prompt_freezes_the_statement_and_names_every_tool() -> None:
    domain = importlib.import_module('hardy.domain')
    prompts = importlib.import_module('hardy.prompts')
    claim = _claim(domain)

    text = prompts.proof_prompt(claim)

    assert claim.content_hash in text
    assert 'theorem two_eq_two : 2 = 2' in text
    assert 'Do not change the theorem name, binders, proposition, or imports.' in text
    for tool in (
        'lean_check_proof',
        'lean_check_scratch',
        'lean_inspect_declarations',
        'lean_search_declarations',
    ):
        assert tool in text
    assert 'complete Lean term placed after :=' in text
    assert 'never include a theorem or lemma declaration' in text
    assert 'not independently assessed' in text
    assert prompts.PROMPT_SET_VERSION
    assert len(prompts.PROMPT_SET_SHA256) == 64
