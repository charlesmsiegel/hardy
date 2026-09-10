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
