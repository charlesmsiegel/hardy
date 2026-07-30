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
