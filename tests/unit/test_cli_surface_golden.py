"""Every command, flag, default and help string the `hardy` parser defines, pinned.

The command reference test (`test_docs.py`) checks that each documented flag
exists; this pins the whole surface, so moving the command adapters out of
`app/cli.py` cannot change a default, a choice or a help line unnoticed. It
records the parser's structure rather than `format_help()`, whose wrapping
differs between Python versions. Machine-specific defaults are replaced by
placeholders. A deliberate change regenerates it in the same commit:

    uv run python tests/unit/test_cli_surface_golden.py
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from hardy.app import config as configuration
from hardy.app.cli import build_parser

GOLDEN = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "cli-surface.json"


def _placeholders() -> list[tuple[str, str]]:
    found = [
        (str(configuration.default_config_path()), "<default-config>"),
        (repr(configuration.DEFAULT_LEAN_COMMAND), "<default-lean-command>"),
        (repr(configuration.DEFAULT_LATEX_COMMAND), "<default-latex-command>"),
    ]
    return sorted(found, key=lambda pair: len(pair[0]), reverse=True)


def _text(value: object) -> object:
    if value is None:
        return None
    if isinstance(value, Path):
        return f"Path({value.as_posix()!r})"
    text = value if isinstance(value, str) else repr(value)
    for spelling, placeholder in _placeholders():
        text = text.replace(spelling, placeholder)
    return text


def _action(action: argparse.Action) -> dict[str, object]:
    return {
        "kind": type(action).__name__,
        "options": list(action.option_strings),
        "dest": action.dest,
        "nargs": _text(action.nargs),
        "const": _text(action.const),
        "default": _text(action.default),
        "type": getattr(action.type, "__name__", _text(action.type)),
        "choices": None if action.choices is None or isinstance(action.choices, dict) else list(action.choices),
        "required": action.required,
        "help": _text(action.help),
        "metavar": _text(action.metavar),
    }


def surface(parser: argparse.ArgumentParser, name: str = "hardy") -> dict[str, dict[str, object]]:
    """The parser at `name` and every subparser under it, keyed by command path."""
    found: dict[str, dict[str, object]] = {
        name: {
            "description": _text(parser.description),
            "epilog": _text(parser.epilog),
            "actions": [_action(action) for action in parser._actions
                        if not isinstance(action, argparse._SubParsersAction)],
            "groups": [[action.dest for action in group._group_actions]
                       for group in parser._mutually_exclusive_groups],
            "defaults": {key: _text(value) for key, value in sorted(parser._defaults.items())},
        },
    }
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            found[name]["subcommands"] = {
                choice.dest: _text(choice.help) for choice in action._choices_actions
            }
            found[name]["subcommand_dest"] = action.dest
            for command, subparser in action.choices.items():
                found.update(surface(subparser, f"{name} {command}"))
    return found


def test_the_command_surface_is_unchanged():
    assert surface(build_parser()) == json.loads(GOLDEN.read_text(encoding="utf-8"))


def regenerate() -> None:
    GOLDEN.write_text(json.dumps(surface(build_parser()), ensure_ascii=False, indent=1) + "\n",
                      encoding="utf-8")


if __name__ == "__main__":
    regenerate()
    sys.exit(0)
