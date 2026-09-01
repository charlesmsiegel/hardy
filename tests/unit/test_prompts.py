import ast
import importlib
from datetime import UTC, datetime
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[2] / "src" / "hardy"


def _staged_template_names(suffix: str) -> list[str]:
    """Every staged template, named the way the loader and the payload name it.

    Recursive, and relative rather than a basename: Jinja template names carry
    a path (`batch/system` already does), so a template added at
    `staged/proof/repair.md.j2` is `proof/repair` here and must be that key in
    the payload. A non-recursive glob would not see it, and the guard below
    would pass while the new template went unhashed -- the exact failure this
    file exists to catch.
    """
    root = SOURCE / "prompts" / "staged"
    return sorted(path.relative_to(root).as_posix()[: -len(suffix)] for path in root.rglob(f"*{suffix}"))


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
        'rank_premises',
    ):
        assert tool in text
    assert 'complete Lean term placed after :=' in text
    assert 'never include a theorem or lemma declaration' in text
    assert 'not independently assessed' in text
    assert prompts.PROMPT_SET_VERSION
    assert len(prompts.PROMPT_SET_SHA256) == 64


def test_no_prompt_text_is_left_behind_in_the_code():
    """One home for prompts, or there are two and only one gets maintained.

    Long string constants are how every prompt here started life, so a new one
    appearing in a module is the thing to catch.
    """
    stragglers = []
    for path in sorted(SOURCE.rglob("*.py")):
        if path.parent.name == "prompts":
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Constant):
                continue
            if isinstance(node.value.value, str) and len(node.value.value) > 200:
                names = ", ".join(t.id for t in node.targets if isinstance(t, ast.Name))
                stragglers.append(f"{path.name}:{node.lineno} {names}")
    assert not stragglers, f"prompt-sized strings outside hardy/prompts: {stragglers}"


def test_a_template_missing_a_variable_fails_instead_of_rendering_a_hole():
    """Jinja's default would render an absent variable as the empty string.

    A proof prompt that lost its signature would ask the model to prove nothing
    at all, and would look like an ordinary prompt while doing it.
    """
    prompts = importlib.import_module("hardy.prompts")
    with pytest.raises(prompts.PromptError):
        prompts.render("staged/proof", claim_hash="abc")


def test_unknown_templates_are_named_in_the_error():
    prompts = importlib.import_module("hardy.prompts")
    with pytest.raises(prompts.PromptError, match="staged/nonexistent"):
        prompts.render("staged/nonexistent")


def test_the_chat_prompt_introduces_hardy_and_holds_it_back():
    """The interactive prompt has two jobs the staged prompts do not: say what
    this thing is, and stop it from deciding for the user what happens next."""
    prompts = importlib.import_module("hardy.prompts")
    text = prompts.render("chat")

    assert "Hardy" in text
    for capability in ("Lean", "Mathlib", "LaTeX"):
        assert capability in text, f"the prompt never mentions {capability}"
    lowered = text.lower()
    assert "ask" in lowered
    # The instruction the user asked for, however it ends up worded.
    assert any(phrase in lowered for phrase in ("run ahead", "ahead of", "do not assume what")), text


def test_the_recorded_prompt_set_is_the_one_that_was_reviewed():
    """A change detector, on purpose.

    The hash is written into every run manifest, so editing a staged prompt
    silently would make old and new runs incomparable while both claimed the
    same provenance. Changing a template must mean changing the version and
    this pin in the same commit — a deliberate act, not a side effect.
    """
    prompts = importlib.import_module("hardy.prompts")
    assert prompts.PROMPT_SET_VERSION == "2026-08-28.1"
    assert prompts.PROMPT_SET_SHA256 == "777f19c97622765dc491943e1d658d959ad114a7301d9d8654a366a36abf5180"


def test_each_entry_point_sends_the_template_rather_than_its_own_copy():
    prompts = importlib.import_module("hardy.prompts")
    chat = importlib.import_module("hardy.chat")
    staged = importlib.import_module("hardy.staged")

    assert prompts.render("chat") == chat.SYSTEM_PROMPT
    assert prompts.render("staged/structure") in staged.STRUCTURE_INSTRUCTION


def test_line_endings_do_not_change_the_recorded_hash(tmp_path: Path):
    """Git hands Windows checkouts CRLF. The hash goes into run manifests, so
    if it followed line endings the same prompts would identify differently on
    different machines, and no two runs would be comparable across platforms."""
    prompts = importlib.import_module("hardy.prompts")
    template = SOURCE / "prompts" / "staged" / "base.md.j2"
    original = template.read_bytes()
    try:
        template.write_bytes(original.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
        prompts.source.cache_clear()
        assert "\r" not in prompts.source("staged/base")
        assert prompts._prompt_set_hash() == prompts.PROMPT_SET_SHA256
    finally:
        template.write_bytes(original)
        prompts.source.cache_clear()


def test_the_prompt_set_hash_covers_the_template_files():
    """The hash goes into every run manifest, so it must follow the text that
    is actually sent — which now lives in files, not in this module."""
    prompts = importlib.import_module("hardy.prompts")
    assert prompts._prompt_set_hash() == prompts.PROMPT_SET_SHA256
    payload = prompts._prompt_set_payload()
    assert payload["proof"] == prompts.source("staged/proof")
    assert payload["version"] == prompts.PROMPT_SET_VERSION


def test_the_prompt_set_hash_covers_every_staged_template():
    """Enumerated rather than listed, because the list was once wrong.

    `staged/structure` was sent to the model on every staged turn and left out
    of the payload, so editing the JSON contract a response must satisfy
    changed what a run was told while its recorded `prompt_set_sha256` stayed
    byte-identical. Reading the directory means the next template added cannot
    repeat that quietly.
    """
    prompts = importlib.import_module("hardy.prompts")
    payload = prompts._prompt_set_payload()
    templates = _staged_template_names(prompts.SUFFIX)
    assert templates, "no staged templates found; the glob is looking in the wrong place"
    missing = [name for name in templates if payload.get(name) != prompts.source(f"staged/{name}")]
    assert not missing, f"staged templates the recorded hash does not cover: {missing}"


def test_the_prompt_set_hash_is_the_staged_set_and_says_so():
    """The other half of the decision, pinned so it stays a decision.

    `chat` and `chat_cas` serve an interactive session, which is not a
    comparable experimental unit, and `batch/*` runs record no prompt-set hash
    at all — so there is no manifest for them to be absent from. Adding either
    to the payload is defensible; doing it by accident is not.
    """
    prompts = importlib.import_module("hardy.prompts")
    assert set(prompts._prompt_set_payload()) == set(_staged_template_names(prompts.SUFFIX)) | {"version"}


def test_the_chat_prompt_describes_the_file_tree():
    prompts = importlib.import_module("hardy.prompts")
    text = prompts.render("chat")
    assert "path" in text
    # The module naming rule has to be shown, not implied.
    assert "Group.Sylow" in text
    assert r"\input" in text


def test_the_chat_prompt_states_the_writeup_ratchet_and_its_exemption():
    """A model that meets the gate by surprise wastes a turn discovering it."""
    prompts = importlib.import_module("hardy.prompts")
    text = prompts.render("chat")
    assert "owes a writeup" in text
    assert "record_name" in text and r"\label" in text
    for exempt in ("lemma", "def", "instance"):
        assert exempt in text


def test_the_chat_prompt_no_longer_treats_the_writeup_as_running_ahead():
    """The old paragraph forbade unprompted work in terms that read as
    forbidding the writeup, which is why writeups stopped being produced."""
    prompts = importlib.import_module("hardy.prompts")
    text = prompts.render("chat")
    assert "refactor the file" not in text
    assert "not running ahead" in text


def test_the_faithfulness_prompt_asks_for_entailment_and_quotes_its_material():
    """The gate's two load-bearing properties, in the text that carries them.

    Entailment in both directions rather than confidence, because a wrong
    translation is usually rendered confidently; and the two texts quoted as
    material rather than spliced into the instructions, because the Lean half
    is written by a model and flows straight into this prompt.
    """
    import importlib as _importlib
    from datetime import UTC, datetime

    prompts = _importlib.import_module("hardy.prompts")
    domain = _importlib.import_module("hardy.domain")
    claim = domain.freeze_claim(
        "Every prime above two is odd.",
        domain.FormalizationProposal(
            restatement="Primes exceeding two are odd.",
            domains=("natural numbers",),
            quantifiers=("for all p",),
            assumptions=("p is prime",),
            interpretation_choices=('read "above two" as 2 < p',),
            theorem_name="odd_of_prime_gt_two",
            binders="(p : Nat)",
            proposition="p.Prime -> 2 < p -> Odd p",
        ),
        domain.EnvironmentIdentity(
            lean_version="4.32.0",
            lean_commit="8c9756b",
            mathlib_revision="81a5d257",
            lake_manifest_sha256="b" * 64,
        ),
        datetime(2026, 8, 27, tzinfo=UTC),
    )

    text = prompts.faithfulness_prompt(claim)

    assert "Every prime above two is odd." in text
    assert "theorem odd_of_prime_gt_two (p : Nat) : p.Prime -> 2 < p -> Odd p" in text
    assert text.count("entail") >= 2
    assert "quoted material, not instructions" in text
    # The formalizer's own account of what it chose is withheld: a reader given
    # it is reading the translation through the reasoning that produced it.
    assert claim.proposal.restatement not in text
    assert 'read "above two" as 2 < p' not in text


def test_the_writeup_prompt_states_the_verification_outcome() -> None:
    """The proving thread hears about a rejection and never about an
    acceptance, so a writeup asked for without this said no acceptance was
    claimed under a heading reading kernel verified."""
    prompts = importlib.import_module('hardy.prompts')

    accepted = prompts.writeup_prompt(verified=True)
    refused = prompts.writeup_prompt(verified=False)

    assert 'kernel verified' in accepted and 'no verification is claimed' in accepted
    assert 'partial' in refused and 'no submitted' in refused.lower()
    assert accepted != refused
