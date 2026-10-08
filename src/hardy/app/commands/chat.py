"""`hardy chat`: the terminal session, and the human's own `/cas` cells."""
from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from typing import Any

from hardy.algebra.cas import CasError
from hardy.algebra.export import export_session
from hardy.app import config as configuration
from hardy.app.commands.launch import _launch, _requested_chat
from hardy.app.projects import offer_registration, prepare_layout
from hardy.workflows import layout
from hardy.workflows.interactive.session import MathematicsSession, SchemaError


def _chat(
    config: configuration.Config,
    *,
    plain: bool = False,
    parser: argparse.ArgumentParser | None = None,
    args: argparse.Namespace | None = None,
) -> int:
    from hardy.app.tui import run_session

    def _report(error: Exception) -> None:
        # Every other `LayoutError` a run can hit -- a bad `--project`, a bad
        # value in a config file -- reaches `_config` and goes through
        # `parser.error`, which prints a clean message and exits 2. Both this
        # and `SchemaError` below are raised later, once a session is
        # actually opening, so without this they were the paths where the
        # same kind of error surfaced as a raw traceback (or, for the schema
        # refusal reached through the interactive shell, a misleading
        # "Falling back to the plain session" line followed by one) instead.
        # `parser` is optional because a direct caller (tests, or any future
        # non-CLI embedding) has no parser to hand it and is better served by
        # the real exception than a swallowed one.
        if parser is None:
            raise error
        parser.error(str(error))

    # `--chat` is a per-launch choice like `--fresh-thread`: applied to the
    # config here, before anything reads `config.layout`, so every path below
    # -- `prepare_layout`, the CAS log, the session itself -- already points at
    # the requested chat rather than `main`.
    requested = getattr(args, "chat", None)
    if requested:
        try:
            config = _requested_chat(config, requested)
        except layout.LayoutError as error:
            _report(error)

    try:
        prepare_layout(config)
    except layout.LayoutError as error:
        _report(error)

    # A TTY on both ends, not just stdin: stdout piped to a file or another
    # process means there is nowhere for the prompt to be seen, so treating
    # that as interactive would print a question no one can answer and then
    # read whatever arrives on stdin as if it were the reply.
    notice = offer_registration(
        config,
        interactive=sys.stdin.isatty() and sys.stdout.isatty(),
        choice=getattr(args, "register_lakefile", None),
    )
    if notice:
        print(notice)

    opener, build, close = _launch(config, args)

    try:
        return run_session(config, build, plain=plain, reopen=opener)
    except (SchemaError, layout.LayoutError) as error:
        # Reaches here whichever path `run_session` took: the plain path
        # raises it straight out of `build`, and the interactive path (see
        # `tui.run_session`) refuses to let its fallback-on-any-exception
        # catch swallow this one and misreport it as a rendering problem.
        #
        # `LayoutError` for the same reason and from a later moment still: a
        # `WriteGuard` refusing a symlinked `transcript.jsonl` or a
        # `cells.jsonl` that leaves the project raises while the session is
        # opening, or in the middle of one, and the user is owed the sentence
        # naming the path rather than a traceback out of an append.
        # `_report` always either raises or exits -- nothing here returns.
        # `from None`: the AssertionError is a statement about this function's
        # control flow, not a failure caused by `error`, and chaining it would
        # print the original traceback under a claim about unreachability.
        _report(error)
        raise AssertionError("unreachable: _report always raises or exits") from None
    finally:
        close()


def _read_block(ask: Callable[[str], str] = input) -> str:
    """Read a multi-line cell, terminated by a line reading `/end`.

    Deliberately not the chat loop's `input().strip()`: stripping a cell would
    destroy Python's indentation and silently change what the user wrote.
    """
    lines: list[str] = []
    while True:
        try:
            line = ask("cas| ")
        except (EOFError, KeyboardInterrupt):
            return ""
        if line.strip() == "/end":
            return "\n".join(lines)
        lines.append(line)


_BLOCK = object()


def _cas_target(argument: str) -> tuple[str | None, Any]:
    """`(path, source)` for a `/cas` line; `_BLOCK` for a source still to be read."""
    words = argument.split(None, 1)
    if len(words) == 2 and words[0] == "run":
        return words[1].strip(), None
    if len(words) == 2 and words[0] == "file":
        return words[1].strip(), _BLOCK
    if len(words) == 1 and words[0] in {"run", "file"}:
        raise CasError(f"/cas {words[0]} takes the path of a file under cas/")
    return None, argument if argument else _BLOCK


def cas_command(
    argument: str,
    session: MathematicsSession,
    *,
    ask: Callable[[str], str] = input,
    out: Callable[[str], Any] = print,
) -> None:
    """The human's own way into the same kernel the model is using."""
    if session.cas is None:
        out("No computer algebra backend is available. `hardy doctor` says why.")
        return
    argument = argument.strip()
    try:
        if argument == "state":
            state = session.cas.state()
            out(f"{state.backend} {state.version or '?'} — kernel {state.kernel}, "
                f"segment {state.segment}, {state.seconds_spent}s spent, "
                f"{state.process_seconds_remaining}s left in this process")
            for line in state.accepted:
                out(f"  {line}")
            return
        if argument == "reset":
            session.cas.reset(author="human")
            out("CAS session reset; the next cell starts a clean kernel.")
            return
        if argument == "export":
            report = export_session(session.cas.session, session.workspace / "cas")
            out(f"Wrote {report.script_path} and {report.notebook_path}")
            out(f"Replay: {report.verified} verified, {report.diverged} diverged, "
                f"{report.failed} failed, {report.unverified} unverified")
            out(f"Script, run as a whole: {report.script_verdict}"
                + (f" — {report.script_detail}" if report.script_detail else ""))
            return
        # Every cell is a file: `run <path>` reruns one, `file <path>` reads a
        # block into one, and a typed cell is filed under `cas/typed/`.
        path, source = _cas_target(argument)
        if path is None:
            path = session.cas.typed_path()
        if source is _BLOCK:
            source = _read_block(ask)
        if source is not None and not source.strip():
            return
        # Human cells go into the same log, under the same lock, and are
        # replayed and exported exactly like the model's.
        result = session.cas.run(path, source, author="human")
        # Hardy's own commentary, ahead of the kernel's: the cell below ran in
        # a rebuilt kernel, which the human should know before reading it.
        if result.restart_note:
            out(result.restart_note)
        for stream in (result.stdout, result.stderr):
            if stream.strip():
                out(stream.rstrip())
        if result.value_repr:
            out(result.value_repr)
        if result.note:
            out(f"({result.note})")
    except CasError as error:
        out(f"CAS: {error}")
