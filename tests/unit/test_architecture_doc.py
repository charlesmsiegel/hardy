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
