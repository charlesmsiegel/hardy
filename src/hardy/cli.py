from __future__ import annotations

import argparse
import json
import os
import shlex
from pathlib import Path

from .chat import MathematicsSession
from .lean import LeanTools
from .models import Request
from .runner import WARNING, run
from .runtime import OpenAICompatibleRuntime


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


def _runtime(args: argparse.Namespace, parser: argparse.ArgumentParser) -> OpenAICompatibleRuntime:
    model = args.model or os.environ.get("HARDY_MODEL")
    if not model:
        parser.error("configure --model or HARDY_MODEL")
    return OpenAICompatibleRuntime(args.base_url, os.environ.get(args.api_key_env, ""), model)


def _chat(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    runtime = _runtime(args, parser)
    session = MathematicsSession(args.workspace, runtime, tuple(shlex.split(args.lean_command)), tuple(shlex.split(args.latex_command)), _confirm_assumption)
    print("Hardy — interactive mathematics workspace")
    print(f"Workspace: {args.workspace}")
    print(f"WARNING: {WARNING} LaTeX is also executed without isolation.")
    print("Type /exit to leave. Your transcript and artifacts are saved as you work.\n")
    while True:
        try:
            text = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if text in {"/exit", "/quit"}:
            return 0
        if not text:
            continue
        print(f"hardy> {session.send(text)}\n")


def _batch(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    runtime = _runtime(args, parser)
    request = Request.from_dict(json.loads(args.request.read_text(encoding="utf-8")))
    lean = LeanTools(request, tuple(shlex.split(args.lean_command)))
    result = run(request, runtime, lean, args.output, max_turns=args.max_turns, wall_seconds=args.wall_seconds)
    print(json.dumps(result.as_dict(), indent=2))
    return 0 if result.terminal_reason == "verified" else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Hardy interactive mathematical research agent")
    parser.add_argument("--model", help="model identity (or set HARDY_MODEL)")
    parser.add_argument("--base-url", default=os.environ.get("HARDY_BASE_URL", "https://api.openai.com/v1"))
    parser.add_argument("--api-key-env", default="OPENAI_API_KEY")
    parser.add_argument("--lean-command", default="lake env lean")
    parser.add_argument("--latex-command", default="pdflatex -interaction=nonstopmode -halt-on-error")
    subparsers = parser.add_subparsers(dest="command")
    chat = subparsers.add_parser("chat", help="start or resume an interactive session")
    chat.add_argument("--workspace", type=Path, default=Path(".hardy"))
    batch = subparsers.add_parser("prove", help="run the earlier one-shot proof experiment")
    batch.add_argument("request", type=Path)
    batch.add_argument("--output", type=Path, default=Path("hardy-output"))
    batch.add_argument("--max-turns", type=int, default=8)
    batch.add_argument("--wall-seconds", type=float, default=300)
    args = parser.parse_args()
    # No subcommand is intentionally the primary interactive experience.
    if args.command in {None, "chat"}:
        if not hasattr(args, "workspace"):
            args.workspace = Path(".hardy")
        return _chat(args, parser)
    return _batch(args, parser)


if __name__ == "__main__":
    raise SystemExit(main())
