from pathlib import Path

ROOT = Path(__file__).parents[2]


def test_trust_boundary_panel_warns_about_computer_algebra() -> None:
    """AGENTS.md requires README.md, DESIGN.md, FEATURES.md, and
    ARCHITECTURE.html to stay consistent. README.md and DESIGN.md both say
    CAS cells and their helper processes are unsandboxed; the trust-boundary
    panel in ARCHITECTURE.html must say so too, not only warn about generated
    Lean, TeX, and downloaded archives.
    """
    html = (ROOT / 'ARCHITECTURE.html').read_text(encoding='utf-8')
    start = html.index('<h2>Trust boundary</h2>')
    end = html.index('</div>', start)
    panel = html[start:end].lower()

    assert 'computer algebra' in panel


def test_the_axiom_audit_is_not_still_listed_as_future_work() -> None:
    """It was claimed as implemented in FEATURES.md while holding on one of the
    three surfaces, and listed under Next in ARCHITECTURE.html at the same
    time. Both said something about the same feature, and they disagreed.
    """
    html = (ROOT / 'ARCHITECTURE.html').read_text(encoding='utf-8')
    start = html.index('Honest experiments')
    card = html[start:html.index('</article>', start)].lower()

    assert 'axiom audit' not in card or 'is done' in card


def test_features_claims_the_audit_on_every_surface_that_has_it() -> None:
    """The claim that started this: one bullet covering three surfaces, where
    only `prove` audited anything. Whatever it says, it must name them.
    """
    features = (ROOT / 'FEATURES.md').read_text(encoding='utf-8')
    start = features.index('audit `#print axioms`')
    bullet = features[start:features.index('\n- ', start)]

    for surface in ('hardy prove', 'hardy batch', 'hardy chat'):
        assert surface in bullet, f'{surface} is not named where the audit is claimed'


def test_the_faithfulness_gate_is_not_still_listed_as_future_work() -> None:
    """The same drift the axiom audit had, one feature later. FEATURES.md
    claims the gate as implemented, so the card that used to list it under
    Next must not go on saying it is coming."""
    html = (ROOT / 'ARCHITECTURE.html').read_text(encoding='utf-8')
    start = html.index('Honest experiments')
    card = html[start:html.index('</article>', start)].lower()

    assert 'faithfulness' not in card or 'is done' in card


def test_features_claims_the_faithfulness_gate_as_implemented() -> None:
    """DESIGN.md describes an independent read that halts the run. FEATURES.md
    is where the same feature is either promised or claimed, and it must not be
    left promising what the workflow already does."""
    features = (ROOT / 'FEATURES.md').read_text(encoding='utf-8')
    start = features.index('Statement faithfulness gate')
    bullet = features[features.rindex('\n- ', 0, start):features.index('\n- ', start)]

    assert 'Now (implemented)' in bullet
    for property_ in ('independent', 'fail-closed', 'trajectory'):
        assert property_ in bullet, f'the gate is claimed without saying it is {property_}'


def test_features_records_what_the_theorem_gate_does_not_cover() -> None:
    """Two live runs on the same problem walked past the theorem gate -- one
    asserted its result in ordinary prose with no theorem environment at all,
    the other put the same claim in a `lemma` environment, which is exempt.
    Both routes are open by design, and the provenance banner is what covers
    them. That is a decision, and FEATURES.md must record it beside the other
    scanner limits rather than leave it to be rediscovered as a bug.
    """
    features = (ROOT / 'FEATURES.md').read_text(encoding='utf-8')
    start = features.index('Known limit — the theorem gate reads environments')
    bullet = features[start:features.index('\n- ', start)]

    for route in ('prose', '`lemma`'):
        assert route in bullet, f'the {route} route past the gate is not named'
    assert 'banner' in bullet, 'the bullet does not say what covers the rest'
    assert 'known_gaps' in bullet, 'the stronger answer is not named'
    # The banner's cover is aggregate -- counts, never which claim is unbacked.
    # A bullet that presents it as coverage without that residue overstates,
    # which is the failure the banner itself is documented to refuse.
    assert 'which' in bullet and 'count' in bullet, (
        'the bullet does not say the banner counts and never points at a claim'
    )
    # The observation the decision rests on has a record; name it.
    assert 'issue #117' in bullet, 'the evidence for the two routes is not cited'


def test_the_theorem_gate_limit_reaches_the_other_required_surfaces() -> None:
    """AGENTS.md requires README.md, DESIGN.md, FEATURES.md and
    ARCHITECTURE.html to stay consistent. DESIGN.md says whether the work is
    finished is computed from the artifacts, the trust panel says calling
    anything finished is a refused tool call, and the README says prose does
    not get around it -- all true of `report_result` and the turn notice, and
    all read as a stronger guarantee than the theorem gate gives once
    FEATURES.md records that a claim the *document* makes in prose or a
    `lemma` environment owes nothing. The qualification must appear where the
    guarantee is stated, on every one of the three.
    """
    design = (ROOT / 'DESIGN.md').read_text(encoding='utf-8')
    start = design.index('computed from the artifacts')
    paragraph = design[start:design.index('\n\n', start)]
    assert 'prose' in paragraph and '`lemma`' in paragraph, (
        'DESIGN.md states the guarantee without the prose/lemma limit'
    )

    html = (ROOT / 'ARCHITECTURE.html').read_text(encoding='utf-8')
    panel_start = html.index('<div class="trust">')
    panel = html[panel_start:html.index('</div>', panel_start)]
    assert 'theorem environment' in panel and 'lemma' in panel, (
        'the trust panel states the guarantee without the prose/lemma limit'
    )

    readme = (ROOT / 'README.md').read_text(encoding='utf-8')
    start = readme.index('Saying it in prose instead does')
    paragraph = readme[start:readme.index('\n\n', start)]
    assert '`lemma`' in paragraph and 'banner' in paragraph, (
        'README.md says prose cannot bypass reporting without saying the '
        "document's own prose owes the gate nothing"
    )


def test_the_readme_does_not_promise_isolation_codex_cannot_give() -> None:
    """AGENTS.md requires README.md, DESIGN.md, FEATURES.md and
    ARCHITECTURE.html to stay consistent, and the README is what a user reads
    first. FEATURES.md and DESIGN.md both record that the Codex reader cannot
    be confined; a README promising every reader "no tools at all" would hand
    users a stronger trust guarantee than the implementation provides.
    """
    readme = (ROOT / 'README.md').read_text(encoding='utf-8')
    start = readme.index('That faithfulness check is the one gate')
    section = readme[start:readme.index('## ', start)]

    assert 'codex' in section.lower()
    # Named, not merely alluded to: the claim it qualifies is the no-tools one.
    assert 'no-tools' in section or 'no tools' in section
    assert 'reads anywhere' in section or 'read' in section


def test_the_case_for_reclaiming_the_loop_includes_compaction_integrity() -> None:
    """Issue #23's four operational reasons were repeated in README.md and
    DESIGN.md while the stronger one was recorded nowhere (issue #98): the SDK
    compacts a long session invisibly and `transcript.jsonl` does not record
    what was dropped, though Hardy's own summary would be largely mechanical —
    registries and verdicts from `session.json`, declarations from
    `read_workspace` — and therefore checkable. Both documents that make the
    case must include it, and the two that only point at the trade —
    FEATURES.md's gap list and the ARCHITECTURE.html model-runtime card —
    must at least name it, so no required document gives a scope for the
    issue #23 work that omits the record."""
    for name in ('README.md', 'DESIGN.md'):
        text = (ROOT / name).read_text(encoding='utf-8').lower()
        assert 'compaction' in text, f'{name} does not record the compaction argument'
        assert 'what was dropped' in text, f'{name} misses the record-integrity half'
        assert 'checkable' in text, f'{name} misses the mechanical-summary half'
        assert 'precompact' in text, f'{name} overstates: the SDK exposes a PreCompact hook'
    for name in ('FEATURES.md', 'ARCHITECTURE.html'):
        text = (ROOT / name).read_text(encoding='utf-8').lower()
        assert 'compaction' in text, f'{name} does not name the compaction gap'


def test_the_trust_panel_qualifies_the_codex_reader() -> None:
    """The fourth document AGENTS.md asks to agree.

    README, DESIGN and FEATURES all record that the Codex reader cannot be
    confined. The architecture overview is the visual map a reader may take
    the design from, and an unconditional claim there is the same overclaim
    made in the one place easiest to quote out of context.
    """
    html = (ROOT / 'ARCHITECTURE.html').read_text(encoding='utf-8')
    start = html.index('<h2>Trust boundary</h2>')
    panel = html[start:html.index('</div>', start)].lower()

    assert 'faithfulness' in panel or 'read back' in panel
    assert 'codex' in panel
    assert 'not established' in panel
