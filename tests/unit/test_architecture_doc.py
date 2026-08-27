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
