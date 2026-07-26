from __future__ import annotations

from pathlib import Path

from hardy import config as configuration
from hardy import runner
from hardy.tui import banner


def settings(tmp_path: Path) -> configuration.Config:
    return configuration.Config(
        model="claude-opus-5",
        lean_command=("lake", "env", "lean"),
        lean_project=None,
        lean_timeout=180.0,
        latex_command=("pdflatex",),
        workspace=tmp_path / "workspace",
        path=tmp_path / "config.toml",
    )


def test_the_banner_is_five_lines(tmp_path: Path):
    assert len(banner.lines(settings(tmp_path))) == 5


def test_the_unsandboxed_warning_is_present_in_full(tmp_path: Path):
    """AGENTS.md makes this a standing disclosure. It must never be trimmed."""
    style, text = banner.lines(settings(tmp_path))[3]
    assert style == "warning"
    assert runner.WARNING in text
    assert "LaTeX is also executed without isolation" in text


def test_the_banner_names_the_workspace_model_and_lean_project(tmp_path: Path):
    rendered = "\n".join(text for _, text in banner.lines(settings(tmp_path)))
    assert "claude-opus-5" in rendered
    assert str(tmp_path / "workspace") in rendered
    assert "current directory" in rendered


def test_the_hint_points_at_the_registry_not_just_model(tmp_path: Path):
    hint = banner.lines(settings(tmp_path))[4][1]
    assert "/help" in hint and "/exit" in hint


def test_no_cas_detail_leaves_the_banner_exactly_as_before(tmp_path: Path):
    """The default for both new keyword arguments -- what every caller that
    knows nothing about CAS (every existing test above, `PlainUi`-less
    callers) gets."""
    config = settings(tmp_path)
    assert banner.lines(config) == banner.lines(config, cas=None, cas_detail="")


def test_a_live_cas_backend_gets_its_own_line_and_extends_the_warning(tmp_path: Path):
    """Computer algebra cells execute unsandboxed exactly like Lean and LaTeX
    -- the same standing disclosure `test_the_unsandboxed_warning_is_present_
    in_full` pins, extended rather than duplicated into a separate, easier to
    miss notice."""
    rows = banner.lines(settings(tmp_path), cas=object(), cas_detail="sympy 1.12")
    assert ("hint", "Computer algebra: sympy 1.12") in rows
    style, warning = next((s, t) for s, t in rows if s == "warning")
    assert runner.WARNING in warning
    assert "LaTeX is also executed without isolation" in warning
    assert "So are computer algebra cells." in warning


def test_an_unavailable_cas_backend_says_so_without_extending_the_warning(tmp_path: Path):
    """`cas is None` (no backend discovered) must not claim cells run
    unsandboxed here when nothing is actually running."""
    rows = banner.lines(settings(tmp_path), cas=None, cas_detail="sympy raised ImportError")
    assert ("hint", "Computer algebra: unavailable — sympy raised ImportError") in rows
    _, warning = next((s, t) for s, t in rows if s == "warning")
    assert "computer algebra" not in warning.lower()


def test_the_cas_line_sits_between_the_lean_project_and_the_warning(tmp_path: Path):
    rows = banner.lines(settings(tmp_path), cas=object(), cas_detail="sympy 1.12")
    styles = [style for style, _ in rows]
    assert styles == ["normal", "hint", "hint", "hint", "warning", "hint"]
    assert rows[2][1].startswith("Lean project:")
    assert rows[3] == ("hint", "Computer algebra: sympy 1.12")
