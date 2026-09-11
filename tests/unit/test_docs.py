"""The documentation is checked against the code, not the other way round.

Reference pages must name every command, flag, slash command and setting the
code defines; every relative link must resolve; and nothing outside the
roadmap may carry a milestone marker, because status has one home.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]

# Pages in scope. Tasks that add a page append it here. The old root
# documents join in Task 26, when they are deleted or rewritten.
NEW_TREE: list[Path] = [
    ROOT / "AGENTS.md",
    ROOT / "CLAUDE.md",
    ROOT / "CONTRIBUTING.md",
    ROOT / "docs" / "roadmap.md",
    ROOT / "docs" / "README.md",
    ROOT / "docs" / "getting-started.md",
    ROOT / "docs" / "install.md",
    ROOT / "docs" / "isolation.md",
    ROOT / "docs" / "research-architecture.md",
    *sorted((ROOT / "docs" / "guides").glob("*.md")),
    *sorted((ROOT / "docs" / "reference").glob("*.md")),
    *sorted((ROOT / "docs" / "design").glob("*.md")),
]

STATUS_MARKER = re.compile(
    r"\bCore [A-I]\b|\*\*(Now|Next|Later)\b|\b[Ll]ane [XSV]\b|\b[XSV][0-9]\b"
)
LINK = re.compile(r"(?<!!)\[[^\]]*\]\(([^)\s]+)\)")
HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*$", re.MULTILINE)


def markdown_files() -> list[Path]:
    return [path for path in NEW_TREE if path.exists()]


def links(text: str) -> list[str]:
    return [target for target in LINK.findall(text) if not target.startswith(("http://", "https://", "mailto:"))]


def section(text: str, heading: str) -> str:
    """The body under the first heading whose text equals `heading`."""
    matches = list(HEADING.finditer(text))
    for index, match in enumerate(matches):
        if match.group(2).strip("`") == heading:
            level = len(match.group(1))
            end = len(text)
            for later in matches[index + 1 :]:
                if len(later.group(1)) <= level:
                    end = later.start()
                    break
            return text[match.end() : end]
    raise AssertionError(f"no heading {heading!r}")


@pytest.mark.parametrize("path", markdown_files(), ids=lambda p: str(p.relative_to(ROOT)))
def test_every_relative_link_resolves(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    missing = []
    for target in links(text):
        file_part = target.split("#", 1)[0]
        if not file_part:
            continue
        if not (path.parent / file_part).exists():
            missing.append(target)
    assert not missing, f"{path.relative_to(ROOT)} links to missing targets: {missing}"


@pytest.mark.parametrize("path", markdown_files(), ids=lambda p: str(p.relative_to(ROOT)))
def test_status_markers_live_only_in_the_roadmap(path: Path) -> None:
    if path.name == "roadmap.md" or "archive" in path.parts:
        return
    text = path.read_text(encoding="utf-8")
    hits = sorted({m.group(0) for m in STATUS_MARKER.finditer(text)})
    assert not hits, f"{path.relative_to(ROOT)} carries milestone markers {hits}; status lives in docs/roadmap.md"


def _subcommands(parser, prefix: str = "hardy"):
    """Yield (command name, parser) for every subparser, depth first."""
    import argparse

    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for name, sub in action.choices.items():
                full = f"{prefix} {name}"
                yield full, sub
                yield from _subcommands(sub, full)


def _options(parser) -> list[str]:
    import argparse

    out = []
    for action in parser._actions:
        if isinstance(action, (argparse._HelpAction, argparse._SubParsersAction)):
            continue
        if action.option_strings:
            out.append(max(action.option_strings, key=len))
    return out


def test_cli_reference_names_every_command_and_option() -> None:
    from hardy.app.cli import build_parser

    page = (ROOT / "docs" / "reference" / "cli.md").read_text(encoding="utf-8")
    parser = build_parser()
    body = section(page, "Global options")
    for option in _options(parser):
        assert f"`{option}`" in body, f"global option {option} missing from Global options"
    for name, sub in _subcommands(parser):
        body = section(page, name)
        for option in _options(sub):
            assert f"`{option}`" in body, f"{name}: option {option} missing"


def test_session_reference_names_every_slash_command() -> None:
    from hardy.app.tui.handlers import build_registry
    from hardy.prompts import user

    page = (ROOT / "docs" / "reference" / "session-commands.md").read_text(encoding="utf-8")
    for command in build_registry():
        assert f"`/{command.name}`" in page, f"/{command.name} missing"
        if command.safe_in_flight:
            assert f"`/{command.name}`" in section(page, "Commands that work while a turn is running")
    for template in user.SHORTCUTS:
        assert f"`/{template.name}`" in section(page, "Prompt shortcuts")


def test_configuration_reference_names_every_setting() -> None:
    from hardy.app.config import SETTINGS

    page = (ROOT / "docs" / "reference" / "configuration.md").read_text(encoding="utf-8")
    body = section(page, "Settings")
    for key, env in SETTINGS.items():
        assert f"`{key}`" in body, f"setting {key} missing"
        assert f"`{env}`" in body, f"env var {env} missing"
    for extra in ("HARDY_CONFIG", "HARDY_PLAIN", "ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL"):
        assert f"`{extra}`" in page, f"{extra} missing"


def test_module_boundaries_page_matches_the_package_list() -> None:
    page = (ROOT / "docs" / "design" / "module-boundaries.md").read_text(encoding="utf-8")
    packages = sorted(p.name for p in (ROOT / "src" / "hardy").iterdir() if p.is_dir() and p.name != "__pycache__")
    for name in packages:
        assert f"`{name}/`" in page, f"package {name}/ missing from the ownership table"


def test_trust_boundary_qualifies_the_codex_reader_and_compaction() -> None:
    page = (ROOT / "docs" / "design" / "trust-boundary.md").read_text(encoding="utf-8").lower()
    assert "codex" in page and "cannot" in page
    for phrase in ("compaction", "what was dropped", "checkable", "precompact"):
        assert phrase in page, f"trust-boundary.md must keep the compaction-integrity argument ({phrase})"


def test_output_contract_records_what_the_theorem_gate_does_not_cover() -> None:
    """Two live runs on the same problem walked past the theorem gate -- one
    asserted its result in ordinary prose with no theorem environment at all,
    the other put the same claim in a `lemma` environment, which is exempt.
    Both routes are open by design, and the provenance banner is what covers
    them. That is a decision, and the output contract must record it beside the
    other scanner limits rather than leave it to be rediscovered as a bug.
    """
    page = (ROOT / "docs" / "design" / "output-contract.md").read_text(encoding="utf-8")
    body = section(page, "The scanner reads environments, not claims")

    for route in ('prose', '`lemma`'):
        assert route in body, f'the {route} route past the gate is not named'
    assert 'banner' in body, 'the page does not say what covers the rest'
    assert 'known_gaps' in body, 'the stronger answer is not named'
    # The banner's cover is aggregate -- counts, never which claim is unbacked.
    # A page that presents it as coverage without that residue overstates,
    # which is the failure the banner itself is documented to refuse.
    assert 'which' in body and 'count' in body, (
        'the page does not say the banner counts and never points at a claim'
    )


def test_computer_algebra_page_states_that_computation_is_not_evidence() -> None:
    page = (ROOT / "docs" / "design" / "computer-algebra.md").read_text(encoding="utf-8")
    assert "no computation is evidence" in page.lower()
    assert "os.system" in page, "the escape hatches a cell has must be named"


def test_docs_index_lists_every_page() -> None:
    index = (ROOT / "docs" / "README.md").read_text(encoding="utf-8")
    listed = {target.split("#")[0] for target in links(index)}
    for path in (ROOT / "docs").rglob("*.md"):
        rel = path.relative_to(ROOT / "docs").as_posix()
        if rel == "README.md" or rel.startswith(("archive/", "superpowers/", "ideas/")) or rel in {"INSTALL.md", "security.md"}:
            continue
        assert rel in listed, f"docs/README.md does not list {rel}"
