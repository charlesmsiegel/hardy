from __future__ import annotations

import argparse
import dataclasses
import json
from pathlib import Path
from typing import Any, Callable

from . import catalog
from . import config as configuration
from . import doctor
from . import claude_runtime
from .chat import MathematicsSession
from .lean import LeanTools
from .models import Request
from .runner import WARNING, run


def _confirm_assumption(proposal: dict[str, str]) -> bool:
    print("\nHardy wants to introduce an assumption:")
    print(f"  Informal: {proposal['informal_statement']}")
    print(f"  Lean: axiom {proposal['formal_name']} : {proposal['lean_statement']}")
    print(f"  Source: {proposal['source']}")
    print(f"  Reason: {proposal['reason']}")
    while True:
        answer = input("Approve this explicit assumption? [y/N] ").strip().lower()
        if answer in {"y", "yes"}:
            return True
        if answer in {"", "n", "no"}:
            return False
        print("Please answer y or n.")


def _config(args: argparse.Namespace, parser: argparse.ArgumentParser) -> configuration.Config:
    try:
        return configuration.load(
            args.config,
            model=args.model,
            lean_command=args.lean_command,
            lean_project=args.lean_project,
            latex_command=args.latex_command,
            workspace=getattr(args, "workspace", None),
        )
    except (ValueError, OSError) as error:
        parser.error(str(error))


def runtime_factory(default_model: str) -> Callable[..., Any]:
    """A way for the session to build its runtime once it can offer the tools."""

    def make(model: str | None = None, **context: Any) -> Any:
        return claude_runtime.ClaudeAgentRuntime(model or default_model, **context)

    return make


def _show_models(config: configuration.Config, out: Callable[[str], None]) -> None:
    current = (config.model or "").lower()
    out("")
    for number, entry in enumerate(catalog.available(), start=1):
        mark = "*" if entry.identifier.lower() == current else " "
        note = f"  {entry.note}" if entry.note else ""
        out(f"  {mark} {number:>3}  {entry.identifier}{note}")
    out("")
    out("  * = current. All models run through your Claude Code subscription; any other identity can be typed in.")


def model_command(argument: str, config: configuration.Config, session: MathematicsSession | None, *, ask: Callable[[str], str] = input, out: Callable[[str], None] = print) -> configuration.Config:
    """Handle `/model`, returning the configuration to use from here on.

    Returns the configuration unchanged when the user backs out, so a failed
    switch never leaves the session half-moved.
    """
    choice = argument.strip()
    models = catalog.available()
    if not choice:
        _show_models(config, out)
        try:
            choice = ask(f"Model (number, identity, or blank to keep {config.model}): ").strip()
        except (EOFError, KeyboardInterrupt):
            out("")
            return config
    if not choice:
        return config
    if choice.isdigit():
        index = int(choice)
        if not 1 <= index <= len(models):
            out(f"No model number {index}.")
            return config
        choice = models[index - 1].identifier

    entry = catalog.describe(choice)
    if session is not None:
        try:
            session.switch_model(entry.identifier)
        except RuntimeError as error:
            out(f"{error} Model unchanged.")
            return config
    out(f"Model: {entry.identifier}")

    updated = dataclasses.replace(config, model=entry.identifier)
    destination = config.config_path
    try:
        if ask(f"Save this as the default in {destination}? [y/N] ").strip().lower() in {"y", "yes"}:
            configuration.write_setting(destination, "model", entry.identifier)
            out(f"Saved to {destination}.")
            updated = dataclasses.replace(updated, path=destination)
    except (EOFError, KeyboardInterrupt):
        out("")
    except OSError as error:
        out(f"Could not write {destination}: {error}")
    return updated


def _chat(config: configuration.Config, parser: argparse.ArgumentParser) -> int:
    session = MathematicsSession(config.workspace, runtime_factory(str(config.model)), config.lean_command, config.latex_command, _confirm_assumption, lean_project=config.lean_project, lean_timeout=config.lean_timeout)
    print("Hardy — interactive mathematics workspace")
    print(f"Workspace: {config.workspace}    Model: {config.model}  (Claude Code subscription)")
    print(f"Lean project: {config.lean_project or 'current directory'}")
    print(f"WARNING: {WARNING} LaTeX is also executed without isolation.")
    print("Type /model to change models and /exit to leave. Your transcript and artifacts are saved as you work.\n")
    while True:
        try:
            text = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if text in {"/exit", "/quit"}:
            return 0
        if text == "/model" or text.startswith("/model "):
            config = model_command(text[len("/model"):], config, session)
            print()
            continue
        if not text:
            continue
        print(f"hardy> {session.send(text)}\n")


def _batch(args: argparse.Namespace, config: configuration.Config, parser: argparse.ArgumentParser) -> int:
    request = Request.from_dict(json.loads(args.request.read_text(encoding="utf-8")))
    lean = LeanTools(request, config.lean_command, timeout=config.lean_timeout, project=config.lean_project)
    result = run(request, runtime_factory(str(config.model)), lean, args.output, max_turns=args.max_turns, wall_seconds=args.wall_seconds)
    print(json.dumps(result.as_dict(), indent=2))
    return 0 if result.terminal_reason == "verified" else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Hardy interactive mathematical research agent")
    parser.add_argument("--config", type=Path, help=f"settings file (default {configuration.default_config_path()})")
    parser.add_argument("--model", help="model identity (or set model in the config file, or HARDY_MODEL)")
    parser.add_argument("--lean-command", help=f"command that elaborates a Lean file (default {configuration.DEFAULT_LEAN_COMMAND!r})")
    parser.add_argument("--lean-project", type=Path, help="Lake project whose imports Lean should resolve")
    parser.add_argument("--latex-command", help=f"command that compiles a LaTeX file (default {configuration.DEFAULT_LATEX_COMMAND!r})")
    subparsers = parser.add_subparsers(dest="command")
    chat = subparsers.add_parser("chat", help="start or resume an interactive session")
    chat.add_argument("--workspace", type=Path, help=f"workspace directory (default {configuration.DEFAULT_WORKSPACE})")
    check = subparsers.add_parser("doctor", help="check that Lean, LaTeX, and the model are usable")
    check.add_argument("--deep", action="store_true", help="also compile a Mathlib probe file, which can take minutes")
    batch = subparsers.add_parser("prove", help="run the earlier one-shot proof experiment")
    batch.add_argument("request", type=Path)
    batch.add_argument("--output", type=Path, default=Path("hardy-output"))
    batch.add_argument("--max-turns", type=int, default=8)
    batch.add_argument("--wall-seconds", type=float, default=300)
    args = parser.parse_args()
    config = _config(args, parser)
    if args.command == "doctor":
        return doctor.report(doctor.run_checks(config, deep=args.deep))
    if args.command == "prove":
        return _batch(args, config, parser)
    # No subcommand is intentionally the primary interactive experience.
    return _chat(config, parser)


if __name__ == "__main__":
    raise SystemExit(main())
