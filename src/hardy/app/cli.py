"""The `hardy` command line: the parser, configuration resolution and dispatch.

What each command does lives in `hardy.app.commands`, one module per command;
this module builds the parser, resolves the configuration every command reads,
and hands the parsed arguments to the owner. Names the command modules own
are re-exported here for the callers and tests that reach them through
`hardy.app.cli`.
"""
from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from hardy.algebra import tools as cas_tools  # noqa: F401 - re-exported
from hardy.app import config as configuration
from hardy.app import doctor
from hardy.app.commands.accept import _find_run_dir as _find_run_dir
from hardy.app.commands.accept import run_accept
from hardy.app.commands.batch import DEFAULT_LADDER, _batch
from hardy.app.commands.batch import _closer_ladder as _closer_ladder
from hardy.app.commands.chat import _chat
from hardy.app.commands.chat import _read_block as _read_block
from hardy.app.commands.chat import cas_command as cas_command
from hardy.app.commands.latency import DEFAULT_PROBE_TIMEOUT, run_latency
from hardy.app.commands.latency import MAX_CALLS as MAX_CALLS
from hardy.app.commands.prove import run_prove
from hardy.app.commands.setup import run_setup
from hardy.app.commands.web import _web, _web_target
from hardy.app.project_registry import Entry, ProjectRegistry
from hardy.app.projects import offer_registration as offer_registration
from hardy.app.projects import prepare_layout as prepare_layout
from hardy.app.terminal import ConsoleTerminal as ConsoleTerminal
from hardy.app.wiring import build_prove_workflow as build_prove_workflow
from hardy.app.wiring import runtime_factory as runtime_factory  # re-export
from hardy.app.wiring import session_runtime as session_runtime
from hardy.formal import latency
from hardy.formal import search as search_tools  # noqa: F401 - re-exported
from hardy.formal.closers import CLOSERS
from hardy.workflows import layout
from hardy.workflows.batch import run as run
from hardy.workflows.interactive.session import MathematicsSession as MathematicsSession
from hardy.workflows.interactive.session import SchemaError as SchemaError


def choose_project(present: list[str], ask: Callable[[str], str] = input) -> str | None:
    """Ask which recorded problem to open, or None to keep the default.

    Only reached from a launch with a terminal on both ends -- see
    `_project_prompt`. Several recorded problems with nothing naming one is a
    real ambiguity, and Hardy used to resolve it in silence by opening, or
    creating, `main`: a user with `sylow/` and `burnside/` on disk got a third
    empty problem and never learned the other two were there.

    A number or a name, because a slug is a directory name and typing one is
    the obvious thing to try; an empty line declines and leaves the old
    default in place. Anything unrecognised declines too rather than looping:
    this runs before a session exists, and a prompt that cannot be escaped at
    startup is worse than one that gives up and can be answered with
    `--project`.
    """
    print("Several problems are recorded here and none is configured as active:")
    for index, slug in enumerate(present, start=1):
        print(f"  {index}. {slug}")
    answer = ask(
        f"Which one? [number, name, or Enter for {layout.DEFAULT_SLUG}] "
    ).strip()
    if not answer:
        return None
    if answer.isdigit() and 1 <= int(answer) <= len(present):
        return present[int(answer) - 1]
    if answer in present:
        return answer
    print(f"{answer!r} is not one of them; opening {layout.DEFAULT_SLUG}. Use --project to be explicit.")
    return None


def _project_prompt(args: argparse.Namespace) -> Callable[[list[str]], str | None] | None:
    """The project chooser, when there is a terminal for it and a session to open.

    A TTY on both ends, for `_chat`'s reason: stdout piped somewhere means
    there is nowhere for the question to be seen, so asking would print into a
    file and then read the next thing on stdin as the answer. Only for a
    launch that opens a session -- the terminal's or the browser's -- too:
    `doctor`, `latency` and `batch` resolve the same configuration, and
    stopping any of them to ask which problem is active would make a scripted
    invocation hang on a question its author never asked for. Not `web`
    either: the browser opens a registered project or nothing, and the
    question a root can pose -- which of its problems -- is one it never asks.
    """
    if getattr(args, "command", None) not in (None, "chat"):
        return None
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return None
    return choose_project


def _config(args: argparse.Namespace, parser: argparse.ArgumentParser) -> configuration.Config:
    try:
        return configuration.load(
            args.config,
            root=getattr(args, "root", None),
            project=getattr(args, "project", None),
            choose=_project_prompt(args),
            model=args.model,
            lean_command=args.lean_command,
            lean_project=args.lean_project,
            latex_command=args.latex_command,
            # None rather than True when the flag is absent, so that omitting
            # it leaves the config file and `HARDY_PROJECT_CONTEXT` alone
            # instead of overriding them with a default nobody asked for.
            project_context=False if getattr(args, "no_project_context", False) else None,
        )
    except (ValueError, OSError) as error:
        parser.error(str(error))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Hardy interactive mathematical research agent")
    parser.add_argument("--config", type=Path, help=f"settings file (default {configuration.default_config_path()})")
    parser.add_argument("--model", help="model identity (or set model in the config file, or HARDY_MODEL)")
    parser.add_argument("--lean-command", help=f"command that elaborates a Lean file (default {configuration.DEFAULT_LEAN_COMMAND!r})")
    parser.add_argument("--lean-project", type=Path, help="Lake project whose imports Lean should resolve")
    parser.add_argument("--latex-command", help=f"command that compiles a LaTeX file (default {configuration.DEFAULT_LATEX_COMMAND!r})")
    parser.add_argument(
        "--plain",
        action="store_true",
        help="use the line-based session with no terminal control",
    )
    # Top-level rather than under `chat`, like `--plain`: an invocation with no
    # subcommand is the primary interactive experience and has to be able to
    # ask to stop reading the file too.
    parser.add_argument(
        "--no-project-context",
        action="store_true",
        help="do not read the project's AGENTS.md or HARDY.md (or set project_context = false, or HARDY_PROJECT_CONTEXT=0)",
    )
    # A flag only, deliberately: unlike every entry in `config.SETTINGS`,
    # "always start fresh" is not a coherent standing preference -- persisted,
    # it would silently discard the conversation on every launch -- so there is
    # no config key and no HARDY_* variable behind this one. Orthogonal to
    # `--no-project-context`: each names the one thing it governs, and the pair
    # composes into the fully clean interactive condition.
    parser.add_argument(
        "--fresh-thread",
        action="store_true",
        help="start this session on a new provider conversation; the workspace, its record and the spend ledger continue unchanged",
    )
    subparsers = parser.add_subparsers(dest="command")
    chat = subparsers.add_parser("chat", help="start or resume an interactive session")
    chat.add_argument("--root", type=Path, help="project root (default: the current directory)")
    chat.add_argument("--project", help=f"which problem to open (default: the active one, or {layout.DEFAULT_SLUG})")
    chat.add_argument("--chat", help=f"which chat of the problem to open (default {layout.DEFAULT_CHAT}); the browser creates others")
    registration = chat.add_mutually_exclusive_group()
    registration.add_argument(
        "--register-lakefile",
        dest="register_lakefile",
        action="store_true",
        default=None,
        help="add this problem to the host lakefile.toml as a lean_lib",
    )
    registration.add_argument(
        "--no-register-lakefile",
        dest="register_lakefile",
        action="store_false",
        help="never touch the host lakefile.toml",
    )
    web = subparsers.add_parser("web", help="serve the browser client on 127.0.0.1")
    web.add_argument(
        "--project",
        help="a registered project to open first, by name or path (default: the last one opened in the browser)",
    )
    web.add_argument("--chat", help=f"which chat of that project to open first (default {layout.DEFAULT_CHAT})")
    web.add_argument("--port", type=int, default=0, help="port to listen on (default: an ephemeral one)")
    web.add_argument("--open", action="store_true", help="open the page in the default browser")
    check = subparsers.add_parser("doctor", help="check that Lean, LaTeX, and the model are usable")
    check.add_argument("--deep", action="store_true", help="also compile a Mathlib probe file, which can take minutes")
    root_check = subparsers.add_parser(
        "check", help="check a root's problem ledgers against each other, the files, and the status rules"
    )
    root_check.add_argument("--root", type=Path, help="the root to check (default: the configured root, else the current directory)")
    root_check.add_argument("--mermaid", action="store_true", help="also print each problem's dependency graph as a Mermaid flowchart")
    # The evidence the interactive-session page (docs/design/interactive-session.md)
    # and issue #54 defer warm pools until. Separate from `doctor` because it
    # answers a design question rather than reporting whether the machine works,
    # and because each probe pays a full Mathlib import.
    measure = subparsers.add_parser(
        "latency", help="measure the fixed Lean import cost a warm pool would recover (issue #54)"
    )
    measure.add_argument("--import", dest="imports", action="append", metavar="MODULE", help="module to import in the probe (repeatable; default Mathlib)")
    measure.add_argument("--repeats", type=int, default=latency.DEFAULT_REPEATS, help=f"probes to time (default {latency.DEFAULT_REPEATS})")
    measure.add_argument("--calls", type=int, help="Lean calls in an observed run that imported the probed set, for a verdict")
    measure.add_argument("--total-seconds", type=float, help="wall time of that observed run, for a verdict")
    # A pool of N pays the prelude N times, not once: #54 asks for a pool of
    # workers, and the estimate credited exactly one first import regardless.
    measure.add_argument("--workers", type=int, default=1, help="warm processes the hypothetical pool would hold (default 1)")
    measure.add_argument("--threshold", type=float, default=latency.DEFAULT_THRESHOLD, help=f"recoverable share that warrants a pool (default {latency.DEFAULT_THRESHOLD})")
    # Its own bound rather than `lean_timeout`: the probe exists because a
    # Mathlib import is slow, and the ordinary check timeout (`lean_timeout`,
    # 180s by default) would kill every probe and report the cost as
    # unmeasurable.
    measure.add_argument("--timeout", type=float, default=DEFAULT_PROBE_TIMEOUT, help=f"seconds one probe may take (default {DEFAULT_PROBE_TIMEOUT:.0f})")
    prove = subparsers.add_parser("prove", help="stage one claim from statement to document")
    prove.add_argument("claim", nargs="?", help="the claim in ordinary language")
    prove.add_argument("--backend", choices=("claude", "codex"), default="claude")
    prove.add_argument("--strategy", choices=("iterative", "best-first"), default="iterative")
    prove.add_argument(
        "--history-mode", choices=("full", "replay-full", "compact"), default="full",
        help="native full history, or authenticated full/compact replay in fresh proof contexts",
    )
    prove.add_argument(
        "--assume",
        type=Path,
        help=(
            "JSON file declaring the axioms this run may stand on: "
            '{"assumptions": [{"name": ..., "statement": ..., "source": ...}]}. '
            "A proof using them is graded verified_modulo and the manifest names exactly "
            "the ones it used. The run writes its own assumptions.json as a bare list of "
            "the same entries, which is what the release audit reads back."
        ),
    )
    # SUPPRESS so that omitting it here leaves the global --model alone rather
    # than overwriting it with this subparser's default.
    prove.add_argument("--model", default=argparse.SUPPRESS)
    # Per invocation, because `faithfulness_model` is one global setting and
    # the backends do not share model names. A config naming a Claude reviewer
    # would otherwise be handed to a `--backend codex` run, whose reader would
    # fail on an identity that backend cannot serve -- halting every approved
    # claim with no way to repair the invocation, since `--model` sets the
    # run's model and not the reviewer's.
    prove.add_argument(
        "--faithfulness-model",
        default=None,
        help="who reads the translation back; defaults to the run's own model",
    )
    accept = subparsers.add_parser("accept", help="run the checked-in acceptance problems")
    accept.add_argument("--backend", choices=("claude", "codex"), default="claude")
    accept.add_argument("--model", default=argparse.SUPPRESS)
    # The same trap as `prove`: this builds the selected backend from the
    # global config, so a configured Claude reviewer would be handed to a
    # `--backend codex` acceptance run and halt both problems as unavailable.
    accept.add_argument(
        "--faithfulness-model",
        default=None,
        help="who reads the translation back; defaults to the run's own model",
    )
    subparsers.add_parser("setup", help="discover, install, and record the pinned toolchain")
    accept.add_argument(
        "--force-budget-exhaustion-test",
        action="store_true",
        help="run the deterministic no-model path instead, and check its artifacts",
    )
    # The audit alone, over runs already on disk: no model, no network, no
    # toolchain. This is how the recorded acceptance runs under
    # `acceptance/recorded/` are rechecked without being re-run.
    accept.add_argument(
        "--recorded",
        type=Path,
        nargs="+",
        metavar="RUN_DIR",
        help="cross-check these recorded run directories (batch or staged) and run nothing",
    )
    batch = subparsers.add_parser("batch", help="run the earlier one-shot proof experiment")
    batch.add_argument("request", type=Path)
    batch.add_argument("--output", type=Path, default=Path("hardy-output"))
    batch.add_argument("--max-turns", type=int, default=8)
    batch.add_argument("--wall-seconds", type=float, default=300)
    batch.add_argument(
        "--closers",
        action="append",
        nargs="?",
        const=DEFAULT_LADDER,
        default=None,
        metavar="TACTIC",
        help=(
            "try this Lean tactic against the statement before spending a model turn; "
            f"repeat the flag for more (bare flag means {', '.join(CLOSERS)}). One tactic "
            "per flag, never a comma-separated list: `simp [Nat.add_comm, Nat.add_left_comm]` "
            "is one tactic and splitting it would submit two invalid ones. Off by default: "
            "a result a tactic ladder reached and a result a model reached are not the same "
            "experiment, and the trajectory records which it was either way."
        ),
    )

    from hardy.app.evals import add_parser as add_evals_parser
    from hardy.app.library import add_parser as add_library_parser

    add_evals_parser(subparsers)
    add_library_parser(subparsers)
    return parser


def _utf8_streams(*streams: Any) -> None:
    """Write UTF-8 whatever the stream would otherwise pick.

    Hardy prints mathematics: `ℕ`, `∀`, `⟨⟩` in every formalization it shows.
    A Windows console already takes UTF-8, but a redirected stdout (`hardy
    prove ... > log`) is opened in the ANSI codepage, and the first `ℕ` ended
    the run with a UnicodeEncodeError -- recorded as an agent failure, after
    the model had been paid. Anything that is not a text wrapper, such as a
    test's capture, is left as it is.
    """
    for stream in streams:
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None and str(getattr(stream, "encoding", "")).lower().replace("-", "") != "utf8":
            reconfigure(encoding="utf-8")


def main() -> int:
    _utf8_streams(sys.stdout, sys.stderr)
    parser = build_parser()
    args = parser.parse_args()
    registry: ProjectRegistry | None = None
    entry: Entry | None = None
    if args.command == "web":
        # Before the configuration is resolved, because the root the config
        # layers are read against is the registered project's own -- never
        # the current directory, which is what `HARDY_ROOT` and the config's
        # `root` would otherwise supply and what a launcher cannot control.
        registry = ProjectRegistry()
        entry = _web_target(args, parser, registry)
        args.root = entry.root if entry is not None else registry.default_root
        args.project = entry.slug if entry is not None else None
    config = _config(args, parser)
    if args.command == "doctor":
        return doctor.report(doctor.run_checks(config, deep=args.deep))
    if args.command == "check":
        from hardy.app.check import main as check_main

        return check_main(args, config)
    if args.command == "latency":
        return run_latency(args, config)
    if args.command == "prove":
        return run_prove(args)
    if args.command == "accept":
        return run_accept(args)
    if args.command == "setup":
        return run_setup(args)
    if args.command == "evals":
        from hardy.app.evals import main as evals_main

        return evals_main(args, config)
    if args.command == "batch":
        return _batch(args, config, parser)
    if args.command == "library":
        from hardy.app.library import main as library_main

        return library_main(args, config)
    if args.command == "web" and registry is not None:
        return _web(config, parser=parser, args=args, registry=registry, entry=entry)
    # No subcommand is intentionally the primary interactive experience.
    return _chat(config, plain=args.plain, parser=parser, args=args)


if __name__ == "__main__":
    raise SystemExit(main())
