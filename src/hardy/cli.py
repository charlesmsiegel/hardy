from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import sys
from collections.abc import Callable
from importlib import metadata
from importlib.resources import files
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from typing import Any

from . import cas_tools, claude_runtime, doctor, lakefile, latency, layout, search_tools
from . import config as configuration
from .cas import CasError
from .cas_export import export_session
from .chat import MathematicsSession, SchemaError
from .lean import LeanTools
from .models import Request
from .runner import WARNING, run
from .tui.ports import Choice


def confirm_assumption(ui: Any) -> Callable[[dict[str, str]], bool]:
    """The axiom gate, reached from an SDK tool thread.

    `MathematicsSession` calls this synchronously from inside a tool call
    (`chat.py`'s `_tool`, itself dispatched from whichever thread the SDK ran
    the tool on), so it must not touch the terminal application directly --
    it goes through `ui.from_thread`, which marshals the prompt onto the
    event loop and blocks this thread for the answer. A decline still
    hard-gates the assumption: `picked is None` (Esc, or the prompt could not
    be shown at all) is treated exactly like an explicit "No", never as
    approval. Every non-approval path returns `False`, including an
    unexpected exception from `blocking` itself -- a bug in the prompting
    path must not be able to fail this gate open.
    """

    def confirm(proposal: dict[str, str]) -> bool:
        blocking = ui.from_thread
        try:
            # The goal first, and the absence of one shown rather than hidden.
            # Nobody can judge whether an assumption is too strong without the
            # assignment in front of them, and Hardy does not judge it for them:
            # the session that approved `no_simple_nonabelian_composite_orders`
            # -- the assignment itself, for 28 of the orders -- spent 170
            # seconds on a well-argued paragraph with nothing beside it.
            goal = proposal.get("goal") or ""
            blocking.write("Goal, as you stated it:", style="normal")
            blocking.write(f"  {goal}" if goal else "  not set -- /goal sets one")
            blocking.write("Hardy wants to introduce an assumption:", style="warning")
            blocking.write(f"  Informal: {proposal['informal_statement']}")
            blocking.write(f"  Lean: axiom {proposal['formal_name']} : {proposal['lean_statement']}")
            blocking.write(f"  Source: {proposal['source']}")
            blocking.write(f"  Reason: {proposal['reason']}")
            blocking.write(f"  Checked: {proposal.get('checked', 'not checked')}")
            picked = blocking.choose(
                f"Approve the assumption {proposal['formal_name']}?",
                [Choice("no", "No, decline it"), Choice("yes", "Yes, approve it")],
                current=0,
            )
        except Exception:  # noqa: BLE001 - every non-approval path is a decline, never a crash
            return False
        return picked is not None and picked.value == "yes"

    return confirm


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
    file and then read the next thing on stdin as the answer. Only for the
    interactive session, too -- `doctor`, `latency` and `batch` resolve the
    same configuration, and stopping any of them to ask which problem is
    active would make a scripted invocation hang on a question its author
    never asked for.
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
        )
    except (ValueError, OSError) as error:
        parser.error(str(error))


def runtime_factory(default_model: str) -> Callable[..., Any]:
    """A way for the session to build its runtime once it can offer the tools."""

    def make(model: str | None = None, **context: Any) -> Any:
        return claude_runtime.ClaudeAgentRuntime(model or default_model, **context)

    return make


def prepare_layout(config: configuration.Config) -> None:
    """Make the project's directories and ignore rules exist before anything writes.

    Called for its side effects at the start of every path that opens a
    project. Without it `Layout.ensure` is reachable only from its own tests,
    and a real run leaves the build tree and the machine-local state as
    ordinary trackable files -- which is the whole thing this layout exists to
    prevent.
    """
    config.layout.ensure()
    config.layout.unignore_tooling(config.root / ".gitignore")


def offer_registration(
    config: configuration.Config,
    *,
    interactive: bool,
    choice: bool | None,
    ask: Callable[[str], str] = input,
) -> str | None:
    """Register this problem with a host Lake project, if asked to.

    Never reads stdin. `choice` is what a flag or a TTY prompt already decided;
    None off a TTY means declined, because asking on a piped launch would block
    at EOF or take the first chat message for an answer. Declining is always
    safe -- Hardy's own resolution does not depend on registration.
    """
    host = config.root / "lakefile.toml"
    if not host.is_file() or choice is False:
        return None
    # Before the host lakefile is touched at all. A launch that was going to
    # decline anyway must not be able to fail on the host's file: a malformed
    # `lakefile.toml` made `registered_libraries` raise `RegistrationRefused`
    # out of a startup path that had not yet asked anybody anything, so Hardy
    # would not start in a directory it never needed to read. Hardy's own
    # resolution does not depend on registration, which is what makes an early
    # return the honest answer rather than a dodge.
    if choice is None and not interactive:
        return None
    slug = config.project
    source = f"{slug}/lean"
    try:
        existing = lakefile.registered_libraries(host)
    except lakefile.RegistrationRefused as refusal:
        # A file Hardy cannot read, or one that is a symlink to another
        # project's build definition, is a reason to decline out loud -- not a
        # traceback, and not a silent skip that leaves `--register-lakefile`
        # looking like it worked.
        return f"Not registering {slug} with {host.name}: {refusal}"
    # Idempotent ONLY when the existing entry is the one we would write. A
    # library of this name pointing somewhere else is a conflict the user needs
    # told about, and returning here would swallow `register`'s refusal and
    # leave `--register-lakefile` silently doing nothing.
    if existing.get(slug) == source:
        return None
    if choice is None:
        # The offer this function exists to make. Without it registration is
        # reachable only through the flag, and the promise that Hardy "offers
        # to register" is never kept on the interactive path it was written for.
        answer = ask(f"Register {slug}/lean with {host.name} so `lake build` sees it? [y/N] ")
        if answer.strip().lower() not in {"y", "yes"}:
            return None
    try:
        stanza = lakefile.register(host, config.root, slug)
        # Through `lakefile.append_stanza`, which re-proves the file is the
        # root's own at the moment of the write: `host.open("a")` follows a
        # symlink, so `<root>/lakefile.toml -> ../other/lakefile.toml` had
        # Hardy register a library in somebody else's project.
        lakefile.append_stanza(host, stanza)
    except lakefile.RegistrationRefused as refusal:
        return f"Not registering {slug} with {host.name}: {refusal}"
    return f"Registered {slug} with {host.name} as a lean_lib; `lake build` now sees its modules."


class ProjectOpener:
    """Open another problem in this root, in the process already running.

    What is rebuilt and what is kept is the whole design. A problem's record,
    transcript, approved assumptions, Lean namespace and computer algebra
    kernel are its own, and reopening builds all of them fresh -- that is what
    keeps two problems in one folder from sharing an axiom approval or a
    trajectory. The pinned Lake project and the Mathlib environment behind the
    search tools belong to the ROOT, not to any problem, and cost tens of
    seconds to establish, so they are carried across untouched. Without that
    distinction `/project switch` would be `exit` with extra steps, which is
    exactly the workaround the layout work set out to replace.

    Holds the live CAS runtime because someone has to: `_chat` closes it when
    the session ends, and after a switch the one to close is the new one. It
    holds no configuration, deliberately: `/model` moves the live session by
    replacing the terminal's `State.config` and touching nothing here, so a
    copy kept from launch would be stale from the first `/model` onwards and
    would silently reopen on the old model. The configuration to continue from
    is an argument to every call instead -- there is no second copy to go out
    of step.
    """

    def __init__(
        self,
        args: argparse.Namespace | None,
        cas: Any,
        *,
        search: Any,
        search_detail: str,
    ):
        self._args = args
        self._search = search
        self._search_detail = search_detail
        self.cas = cas

    def _configure(self, slug: str, current: configuration.Config) -> configuration.Config:
        """The same resolution `_config` does, for a slug already chosen.

        No `choose`: the user has just named the problem, so there is no
        ambiguity left to ask about. `current` is the configuration the
        session is actually running, and the model comes from it rather than
        from the file: `/model` may have moved the session since launch, and
        an explicit override here wins over the file even when the user also
        saved it, so re-reading would quietly reopen on the wrong model.
        """
        args = self._args
        return configuration.load(
            getattr(args, "config", None),
            root=getattr(args, "root", None) or current.root,
            project=slug,
            model=current.model,
            lean_command=getattr(args, "lean_command", None),
            lean_project=getattr(args, "lean_project", None),
            latex_command=getattr(args, "latex_command", None),
        )

    def __call__(
        self, slug: str, ui: Any, current: configuration.Config
    ) -> tuple[configuration.Config, Any]:
        config = self._configure(slug, current)
        prepare_layout(config)
        # A kernel per problem, logging into that problem's `cas/`. Sharing one
        # would put two problems' cells in one `cells.jsonl` and one export.
        cas, cas_detail = cas_tools.build_runtime(
            backend_name=config.cas_backend,
            command=config.cas_command,
            limits=config.limits,
            log_path=config.layout.cas / "cells.jsonl",
            cwd=config.layout.cas,
        )
        try:
            session = MathematicsSession(
                config.layout.problem,
                runtime_factory(str(config.model)),
                config.lean_command,
                config.latex_command,
                confirm_assumption(ui),
                lean_project=config.lean_project,
                lean_timeout=config.lean_timeout,
                cas=cas,
                cas_detail=cas_detail,
                search=self._search,
                search_detail=self._search_detail,
            )
        except BaseException:
            # The kernel this call started, and only that one. The session the
            # user is already in keeps its own -- a refused record or a
            # symlinked transcript must not take the working problem down with
            # the one that could not be opened.
            if cas is not None:
                cas.session.close()
            raise
        if self.cas is not None:
            self.cas.session.close()
        self.cas = cas
        self._remember(config)
        return config, session

    def _remember(self, config: configuration.Config) -> None:
        """Record which problem is active, where the project layer reads it.

        `<root>/.hardy/config.toml` exists to say which problem a checkout is
        working on (`config.PROJECT_SETTINGS`), and this is the moment that
        answer changes. Without it a switch is forgotten at exit and the next
        launch reopens the old problem, or asks again.

        Through `write_project_setting`, which writes it under a `WriteGuard`.
        This file is inside the checkout and arrives with a clone, so the
        directory, the file and the temporary the write goes through are all
        attacker-chosen -- see that function for the hole a fixed `<name>.tmp`
        leaves open.

        A failure here is reported and swallowed: neither an unwritable config
        file nor a refused one is a reason to undo a switch that has already
        happened. The problem IS open; only the note saying so for next time
        was lost.
        """
        destination = config.root / layout.HARDY_DIR / "config.toml"
        try:
            configuration.write_project_setting(config.root, "project", config.project)
        except (OSError, layout.LayoutError) as error:
            print(f"Could not record the active project in {destination}: {error}")


def _chat(
    config: configuration.Config,
    *,
    plain: bool = False,
    parser: argparse.ArgumentParser | None = None,
    args: argparse.Namespace | None = None,
) -> int:
    from .tui import run_session

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

    # Built once, here -- not inside `build` below -- because `run_session`
    # can call its `session_factory` a second time (the interactive shell
    # falling back to the plain session after failing to start) and a second
    # kernel process is not what that fallback should cost. `finally` closes
    # it exactly once regardless of which path `run_session` actually took,
    # or how it ended.
    cas, cas_detail = cas_tools.build_runtime(
        backend_name=config.cas_backend,
        command=config.cas_command,
        limits=config.limits,
        log_path=config.layout.cas / "cells.jsonl",
        cwd=config.layout.cas,
    )

    # Built here for the same reason the CAS runtime is: `run_session` can call
    # its factory twice when the interactive shell falls back to the plain one,
    # and reading the Lake manifest and hashing it twice is waste. Unlike the
    # CAS runtime a None here is still offered to the model -- as a tool that
    # refuses and says why.
    search, search_detail = search_tools.build_runtime(config)

    # How `/project switch` opens another problem without ending the process.
    # It owns the live CAS runtime from here on, because a switch replaces it
    # and the `finally` below has to close whichever one is current.
    opener = ProjectOpener(args, cas, search=search, search_detail=search_detail)

    def build(confirm: Callable[[dict[str, str]], bool]) -> MathematicsSession:
        return MathematicsSession(
            config.layout.problem,
            runtime_factory(str(config.model)),
            config.lean_command,
            config.latex_command,
            confirm,
            lean_project=config.lean_project,
            lean_timeout=config.lean_timeout,
            cas=cas,
            cas_detail=cas_detail,
            search=search,
            search_detail=search_detail,
        )

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
        # Not reached at all if a forced double-Ctrl+C exit inside the shell
        # reaches `os._exit` -- that bypasses every `finally` in the process,
        # not just this one. Accepted for the same reason a forced exit
        # already leaves Lean/LaTeX subprocesses orphaned: the user was
        # warned before pressing Ctrl+C a second time.
        #
        # `opener.cas`, not `cas`: a `/project switch` replaced the kernel, and
        # closing the one this function built would leave the live one running
        # and the session's own process behind.
        if opener.cas is not None:
            opener.cas.session.close()


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
                f"segment {state.segment}, {state.seconds_remaining}s left")
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
        source = argument or _read_block(ask)
        if not source.strip():
            return
        # Human cells go into the same log, under the same lock, and are
        # replayed and exported exactly like the model's.
        result = session.cas.run(source, author="human")
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




def _batch(args: argparse.Namespace, config: configuration.Config, parser: argparse.ArgumentParser) -> int:
    request = Request.from_dict(json.loads(args.request.read_text(encoding="utf-8")))
    lean = LeanTools(request, config.lean_command, timeout=config.lean_timeout, project=config.lean_project)
    # Refused here rather than at the first submission. An anonymous `example`
    # has no name for `#print axioms`, so the audit can never establish anything
    # about it and the run can never verify -- and finding that out at the end
    # costs a whole billable model run to reach a conclusion available now.
    if lean.target_name is None:
        parser.error(f"batch needs a named theorem or lemma to audit, not: {request.declaration!r}")
    result = run(request, runtime_factory(str(config.model)), lean, args.output, max_turns=args.max_turns, wall_seconds=args.wall_seconds)
    print(json.dumps(result.as_dict(), indent=2))
    return 0 if result.terminal_reason == "verified" else 1


class ConsoleTerminal:
    """The staged workflow's conversation with the person running it."""

    def __init__(
        self,
        *,
        input_fn: Callable[[str], str] = input,
        output: Callable[[str], Any] = print,
    ) -> None:
        self._input = input_fn
        self._output = output

    def acknowledge_unsafe_execution(self) -> bool:
        # A typed acknowledgement rather than a keystroke: running unsandboxed
        # generated code is worth one deliberate sentence.
        # Every kind of generated code this run can execute is named. A staged
        # run has no chat banner, so this sentence is the only place a user
        # learns that computer algebra cells run here too.
        self._output(
            f"WARNING: {WARNING} LaTeX and computer algebra cells are also "
            "executed without isolation."
        )
        return self._input("Type I UNDERSTAND to continue: ").strip() == "I UNDERSTAND"

    def show_formalization(self, proposal: Any, elaboration: Any) -> None:
        self._output("\nProposed interpretation")
        self._output(proposal.restatement)
        for label, values in (
            ("Domains", proposal.domains),
            ("Quantifiers", proposal.quantifiers),
            ("Assumptions", proposal.assumptions),
            ("Interpretation choices", proposal.interpretation_choices),
        ):
            self._output(f"{label}: {', '.join(values) if values else 'none'}")
        binders = f" {proposal.binders.strip()}" if proposal.binders.strip() else ""
        self._output(f"theorem {proposal.theorem_name}{binders} : {proposal.proposition}")
        # A statement that elaborates has been type-checked, not proved. Saying
        # so here is the whole point of showing it.
        self._output(
            "statement elaborates; this is not proof evidence"
            if elaboration.success
            else "statement does not elaborate"
        )

    def choose_approval(self) -> str:
        while True:
            choice = self._input("Choose approve, revise, or cancel: ").strip().lower()
            if choice in {"approve", "revise", "cancel"}:
                return choice
            self._output("Please enter approve, revise, or cancel.")

    def revision_text(self) -> str:
        return self._input("Describe the required interpretation change: ").strip()

    def show_result(self, manifest: Any) -> None:
        self._output("\nHardy result")
        self._output(f"Run ID: {manifest.run_id}")
        self._output(f"Phase: {manifest.phase.value}")
        self._output(f"Formal: {manifest.grades.formal.value}")
        self._output(f"Faithfulness: {manifest.grades.faithfulness.value}")
        self._output(f"Informal: {manifest.grades.informal.value}")
        self._output(f"Document: {manifest.grades.document.value}")
        for gap in manifest.grades.known_gaps:
            self._output(f"Known gap: {gap}")
        if manifest.terminal_reason is not None:
            self._output(f"Terminal reason: {manifest.terminal_reason.value}")


def _common_locations() -> dict[str, tuple[Path, ...]]:
    """Where these tools land when their own installers put them there."""
    local = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    elan_bin = Path.home() / ".elan" / "bin"
    suffix = ".exe" if os.name == "nt" else ""
    return {
        "elan": (elan_bin / f"elan{suffix}",),
        "lake": (elan_bin / f"lake{suffix}",),
        "tectonic": (local / "Hardy" / "tools" / "tectonic" / "0.16.9" / "tectonic.exe",),
    }


def _print_report(report: Any) -> None:
    for tool in report.tools:
        state = "OK" if tool.healthy else "MISSING/FAILED"
        location = str(tool.path) if tool.path else "not registered"
        version = tool.version or "unknown version"
        print(f"{tool.name:9} {state:14} {version} [{location}] - {tool.detail}")
    print(f"mathlib   {'OK' if report.mathlib_ready else 'MISSING/FAILED'}")


def _confirm(prompt: str) -> bool:
    return input(prompt + " [y/N] ").strip().lower() in {"y", "yes"}


def run_setup(args: argparse.Namespace, *, confirmer: Callable[[str], bool] = _confirm) -> int:
    """Discover the pinned toolchain, offer to install what is missing, record it."""
    from .installers import download_file, install_elan, install_tectonic, prepare_mathlib
    from .process import run_process
    from .setup import discover_environment

    config, config_path = _load_config_argument(getattr(args, "config", None))
    report = discover_environment(config, common_locations=_common_locations())
    statuses = {item.name: item for item in report.tools}
    if not statuses["elan"].healthy:
        winget = shutil.which("winget")
        if winget:
            print(
                install_elan(
                    winget=Path(winget),
                    cwd=Path.cwd(),
                    confirmer=confirmer,
                    runner=run_process,
                ).manual_instructions
            )
        else:
            print(
                "elan was not found. Install it with the platform installer under scripts/, "
                "then rerun `hardy setup`."
            )
    if not statuses["tectonic"].healthy:
        if os.name == "nt":
            local = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
            print(
                install_tectonic(
                    destination_root=local / "Hardy" / "tools",
                    confirmer=confirmer,
                    downloader=download_file,
                ).manual_instructions
            )
        else:
            print(
                "tectonic was not found. Install it from your package manager or "
                "https://tectonic-typesetting.github.io, then rerun `hardy setup`."
            )
    rediscovered = discover_environment(config, common_locations=_common_locations())
    tools = {item.name: item for item in rediscovered.tools}
    for setting in ("elan", "lake", "tectonic"):
        found = tools[setting].path
        if found is not None:
            configuration.write_setting(config_path, setting, str(found))
    if tools["lake"].path is not None and config.lean_project and not rediscovered.mathlib_ready:
        print(
            prepare_mathlib(
                lake=tools["lake"].path,
                lean_project=config.lean_project,
                confirmer=confirmer,
                runner=run_process,
            ).manual_instructions
        )
    final = discover_environment(
        configuration.load(config_path), common_locations=_common_locations()
    )
    _print_report(final)
    print(f"Configuration saved to {config_path}")
    return 0 if final.healthy else 1


def build_prove_workflow(config: configuration.Config, config_path: Path, *, backend: str = "claude"):
    """Assemble the staged workflow around the chosen backend."""
    from . import lean as lean_module
    from . import retrieval
    from .lean import LeanService
    from .mcp_server import LeanToolRuntime
    from .prompts import PROMPT_SET_SHA256
    from .verifier import FinalVerifier
    from .workflow import ProveWorkflow
    from .writeup import RunIdentities, build_writeup

    environment = lean_module.environment_identity(config.lean_project)
    lean = LeanService(
        lake=config.lake,
        lean_project=config.lean_project,
        environment=environment,
        limits=config.limits,
    )
    verifier = FinalVerifier(
        lake=config.lake,
        lean_project=config.lean_project,
        environment=environment,
        limits=config.limits,
    )

    def identities(run_id: Any, model: str) -> Any:
        return RunIdentities(
            run_id=run_id,
            model=model,
            backend=backend,
            runtime_sdk_version=_sdk_version(backend),
            prompt_set_sha256=PROMPT_SET_SHA256,
            lean_version=environment.lean_version,
            mathlib_revision=environment.mathlib_revision,
            tectonic_version="0.16.9",
            tectonic_executable=config.tectonic,
            tectonic_bundle=config.tectonic_bundle,
            tectonic_bundle_sha256=config.tectonic_bundle_sha256,
        )

    def runtime_factory(store: Any) -> Any:
        if backend == "codex":
            from openai_codex import Codex

            from .codex_runtime import CodexRuntime

            return CodexRuntime(client=Codex(), store=store, config_path=config_path)
        from .domain import RunPhase
        from .staged import ClaudeStagedRuntime

        def observe_cas(event: dict[str, Any]) -> None:
            # `cas_run` (and `cas_reset`) publish a completed cell record here;
            # without this the trajectory shows the tool was *requested* but
            # never what the kernel actually returned.
            #
            # The event's own `type` is already "cas" -- that is the name chat
            # files these under in its transcript -- so prefixing it produced
            # the trajectory kind "cas.cas", which names the subsystem twice
            # and the thing recorded not at all. What the event carries is a
            # completed cell.
            kind = str(event.get("type", "event"))
            store.append(
                "cas.cell" if kind == "cas" else f"cas.{kind}", event, phase=RunPhase.PROVING
            )

        cas_directory = store.path / "cas"
        cas_runtime, _ = cas_tools.build_runtime(
            backend_name=config.cas_backend,
            command=config.cas_command,
            limits=config.limits,
            log_path=cas_directory / "cells.jsonl",
            cwd=cas_directory,
            spill=lambda name, text: store.write_text(
                PurePosixPath(f"process/{name}"), text
            ).relative_path,
            observe=observe_cas,
        )
        return ClaudeStagedRuntime(
            store=store,
            lean_runtime_factory=lambda claim: LeanToolRuntime(
                claim=claim,
                service=lean,
                store=store,
                official_checks=config.limits.official_checks,
                observation_bytes=config.limits.model_observation_bytes,
                # One retriever per proving stage, because the retrieval budget
                # is spent across the stage rather than per call.
                retriever=retrieval.build_retriever(lean, config.limits),
            ),
            cas_runtime=cas_runtime,
            cas_directory=cas_directory,
        )

    def staged_doctor(value: configuration.Config) -> Any:
        checks = doctor.run_checks(value)
        return SimpleNamespace(
            healthy=all(check.ok for check in checks if check.required),
            authenticated=all(check.ok for check in checks if "login" in check.name.lower()),
        )

    return ProveWorkflow(
        config=config,
        environment=environment,
        doctor=staged_doctor,
        lean=lean,
        runtime_factory=runtime_factory,
        verifier=verifier,
        writeup_builder=build_writeup,
        identities_factory=identities,
    )


def _sdk_version(backend: str) -> str:
    package = "openai-codex" if backend == "codex" else "claude-agent-sdk"
    try:
        return metadata.version(package)
    except metadata.PackageNotFoundError:
        return "not installed"


def _load_config_argument(value: str | None) -> tuple[configuration.Config, Path]:
    path = Path(value) if value else configuration.default_config_path()
    settings = configuration.load(path)
    return settings, settings.config_path


def run_prove(
    args: argparse.Namespace,
    *,
    workflow_factory: Callable[..., Any] = build_prove_workflow,
    input_fn: Callable[[str], str] = input,
) -> int:
    from .workflow import ProveRequest

    config, config_path = _load_config_argument(getattr(args, "config", None))
    claim = args.claim or input_fn("State the theorem in ordinary language: ").strip()
    if not claim:
        print("A nonempty theorem statement is required.")
        return 2
    slug = re.sub(r"[^a-z0-9]+", "-", claim.lower()).strip("-")[:48] or "theorem"
    terminal = ConsoleTerminal(input_fn=input_fn)
    workflow = workflow_factory(config, config_path, backend=getattr(args, "backend", "claude"))
    manifest = workflow.run(
        ProveRequest(text=claim, model=str(args.model or config.model), problem_slug=slug),
        terminal,
    )
    print(f"Artifacts: {config.runs_root}")
    return 0 if manifest.phase.value == "completed" else 1


def run_accept(args: argparse.Namespace) -> int:
    from .acceptance import run_deterministic_experiment, validate_run_consistency
    from .domain import (
        DocumentStatus,
        FaithfulnessStatus,
        FormalStatus,
        RunPhase,
        TerminalReason,
    )
    from .workflow import ProveRequest

    config, config_path = _load_config_argument(getattr(args, "config", None))
    if getattr(args, "force_budget_exhaustion_test", False):
        # The deterministic path exists so the pipeline can be checked whole
        # without a model, a network, or a built toolchain.
        result = run_deterministic_experiment(config, outcome="exhausted")
        issues = validate_run_consistency(result.run_dir, result.manifest)
        print(f"Forced no-model run: {result.run_dir}")
        for issue in issues:
            print("CONSISTENCY ERROR: " + issue)
        passed = (
            not issues
            and result.manifest.terminal_reason is TerminalReason.TIMEOUT_BUDGET_EXHAUSTED
            and result.manifest.grades.formal is FormalStatus.PARTIAL
        )
        return 0 if passed else 1

    payload = json.loads(
        files("hardy").joinpath("acceptance_problems.json").read_text(encoding="utf-8")
    )
    if payload.get("schema_version") != 1 or len(payload.get("problems", [])) != 2:
        print("The checked-in acceptance problem set is invalid.")
        return 2
    all_passed = True
    for problem in payload["problems"]:
        problem_id = problem["id"]
        print(f"\nAcceptance problem: {problem_id}")
        workflow = build_prove_workflow(
            config, config_path, backend=getattr(args, "backend", "claude")
        )
        manifest = workflow.run(
            ProveRequest(
                text=problem["input"],
                model=str(args.model or config.model),
                problem_slug=problem_id,
            ),
            ConsoleTerminal(),
        )
        run_dir = _find_run_dir(config.runs_root, manifest.run_id)
        issues = validate_run_consistency(run_dir, manifest)
        if manifest.phase is not RunPhase.COMPLETED:
            issues += ("run did not reach completed phase",)
        if manifest.grades.formal is not FormalStatus.KERNEL_VERIFIED:
            issues += ("formal status is not kernel_verified",)
        if manifest.grades.faithfulness is not FaithfulnessStatus.USER_APPROVED:
            issues += ("faithfulness was not user approved",)
        if manifest.grades.document is not DocumentStatus.TEX_COMPILED:
            issues += ("document did not compile",)
        print(f"Acceptance run artifacts: {run_dir}")
        for issue in issues:
            print("ACCEPTANCE ERROR: " + issue)
        all_passed = all_passed and not issues
    return 0 if all_passed else 1


def _find_run_dir(root: Path, run_id: Any) -> Path:
    suffix = "-" + run_id.hex[:8]
    candidates = [path for path in root.iterdir() if path.is_dir() and path.name.endswith(suffix)]
    if len(candidates) != 1:
        raise RuntimeError(f"could not identify run directory for {run_id}")
    return candidates[0]


DEFAULT_PROBE_TIMEOUT = 300.0

# Far beyond any run `hardy latency` describes, and small enough that every
# derived total still renders. See the bound in `run_latency`.
MAX_CALLS = 1_000_000_000


def run_latency(args: argparse.Namespace, config: configuration.Config) -> int:
    """Measure the fixed Lean import cost, for the gate in DESIGN.md and #54.

    Runs where the ordinary checks run -- inside the configured Lake project,
    through the configured Lean command -- because an import cost measured
    against a different Mathlib is not the cost this harness pays.
    """
    imports = tuple(args.imports or ("Mathlib",))
    # Checked here, not where the probe source is rendered. `import_probe` is
    # called inside `measure_import_cost`, which runs after the toolchain probe
    # has already spent up to a full deadline on a `--version` that may stall —
    # so a malformed module name paid 300s before being told it was malformed.
    try:
        latency.import_probe(imports)
    except ValueError as error:
        print(str(error))
        return 2
    # Checked before probing, not after: each probe pays a full Mathlib import,
    # and rejecting a negative --calls once minutes have been spent is a
    # traceback where a usage error belongs. Checked before the conversion
    # below too -- `round(nan)` raises ValueError and `round(inf)` raises
    # OverflowError, so converting first turns a usage error into a traceback.
    # Bounded above as well as below. Python integers do not overflow, but the
    # report renders milliseconds as seconds, and `10**308 * 12_000 / 1000`
    # exceeds what a float can hold -- so an absurd count completed every
    # expensive probe and then exited with an OverflowError traceback. A billion
    # Lean calls is already far beyond any run this measures.
    if args.calls is not None and not 0 <= args.calls <= MAX_CALLS:
        print(f"--calls must be between 0 and {MAX_CALLS}")
        return 2
    if args.total_seconds is not None and (
        not math.isfinite(args.total_seconds)
        or args.total_seconds < 0
        # Finite is not enough: 1e308 passes, and 1e308 * 1000 is `inf`, so the
        # conversion to milliseconds below raised OverflowError where a usage
        # error belonged.
        or not math.isfinite(args.total_seconds * 1_000)
    ):
        print("--total-seconds must be a finite, non-negative number of seconds")
        return 2
    # Finite, not merely positive: `nan` and `inf` both pass `<= 0`, and
    # `run_process` then builds a deadline that `time.monotonic()` can never
    # reach, so a stalled probe would run forever inside a command whose entire
    # contract is that every call is bounded.
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        print("--timeout must be a finite, positive number of seconds")
        return 2
    total_ms = None if args.total_seconds is None else round(args.total_seconds * 1_000)
    # A threshold argparse accepts as a float but that no share can be compared
    # against meaningfully: negative makes every run "warranted" including one
    # recovering nothing, NaN fails every comparison so nothing is ever
    # warranted, and above 1 can never be reached. Each manufactures a verdict
    # from malformed input rather than from the measurement.
    #
    # Zero belongs with the negatives, which the original bound missed by
    # admitting it. A single Lean call cannot avoid its own import, so it
    # recovers nothing, and `0% >= 0%` reported that as a warranted pool --
    # affirmative evidence for machinery that saves nothing at all.
    if not math.isfinite(args.threshold) or not 0.0 < args.threshold <= 1.0:
        print("--threshold must be a fraction above 0 and at most 1")
        return 2
    # Checked here rather than left to `measure_import_cost`, which only sees
    # it after `probe_toolchain` has already started a child that can sit on
    # the full deadline before the count is ever rejected.
    if args.repeats < 1:
        print("--repeats must be at least 1")
        return 2
    if args.workers < 1:
        print("--workers must be at least 1")
        return 2
    # Both or neither. One alone produced a report that asked for the other and
    # still exited 0, so a script could not tell an unanswered verdict from a
    # real one -- and it asked only after paying for every probe.
    if (args.calls is None) != (args.total_seconds is None):
        print("--calls and --total-seconds are given together or not at all")
        return 2
    project = config.lean_project if config.lean_project is not None else Path.cwd()
    # A deleted project makes `Popen` raise `FileNotFoundError` for the working
    # directory, which would otherwise be reported as a missing Lean; a project
    # path that is a regular file raises `NotADirectoryError` and escaped as a
    # traceback. Checked up front, as `LeanTools._run` and `doctor` both do.
    if not project.is_dir():
        print(f"Lean project directory not found: {project}")
        return 1
    # Resolved once, so the toolchain probe, the measurement, and the recorded
    # provenance all name the same absolute directory rather than a relative
    # path whose meaning depends on where the command happened to be invoked.
    project = project.resolve()
    # Asked of the Lean actually being invoked, not `_environment_identity`,
    # whose version and commit are constants pinned for the staged path and
    # would misattribute a `--lean-command` pointing at a different compiler.
    # Returns None when the toolchain cannot be identified, and the report then
    # says so rather than naming a version nobody verified.
    if not config.lean_command:
        print("no Lean command configured; set lean_command or pass --lean-command")
        return 2
    # Before any child starts, on stderr, flushed. Two reasons beyond habit:
    # a redirected stdout is block-buffered, so a warning printed there could
    # appear only after a multi-minute probe had already elaborated whatever
    # the user named -- and this report is evidence somebody will redirect to a
    # file, which the warning is not part of. AGENTS.md is explicit that Hardy
    # must never let unsandboxed elaboration pass unsaid.
    print(f"WARNING: {WARNING}", file=sys.stderr, flush=True)
    probe = latency.probe_toolchain(
        config.lean_command, project, timeout_seconds=args.timeout
    )
    try:
        cost = latency.measure_import_cost(
            imports,
            argv=(*config.lean_command, "--json"),
            cwd=project,
            timeout_seconds=args.timeout,
            repeats=args.repeats,
            environment=probe.identity,
            identity_note=probe.reason,
            manifest_bound=probe.manifest_bound,
        )
    except FileNotFoundError:
        print(f"Lean executable not found: {config.lean_command[0]}")
        return 1
    except OSError as error:
        # Not only FileNotFoundError. A command that exists but is not
        # executable raises PermissionError, and a wrong-format binary raises
        # OSError; both escaped as tracebacks, past a toolchain probe that had
        # already caught the identical failure and written down why.
        print(f"Lean command could not be run ({config.lean_command[0]}): {error}")
        return 1
    except ValueError as error:
        print(str(error))
        return 2
    return latency.report(
        cost,
        calls=args.calls,
        total_ms=total_ms,
        threshold=args.threshold,
        workers=args.workers,
    )


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
    subparsers = parser.add_subparsers(dest="command")
    chat = subparsers.add_parser("chat", help="start or resume an interactive session")
    chat.add_argument("--root", type=Path, help="project root (default: the current directory)")
    chat.add_argument("--project", help=f"which problem to open (default: the active one, or {layout.DEFAULT_SLUG})")
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
    check = subparsers.add_parser("doctor", help="check that Lean, LaTeX, and the model are usable")
    check.add_argument("--deep", action="store_true", help="also compile a Mathlib probe file, which can take minutes")
    # The evidence DESIGN.md and issue #54 defer warm pools until. Separate from
    # `doctor` because it answers a design question rather than reporting whether
    # the machine works, and because each probe pays a full Mathlib import.
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
    # Mathlib import is slow, and the ordinary 30s check timeout would kill
    # every probe and report the cost as unmeasurable.
    measure.add_argument("--timeout", type=float, default=DEFAULT_PROBE_TIMEOUT, help=f"seconds one probe may take (default {DEFAULT_PROBE_TIMEOUT:.0f})")
    prove = subparsers.add_parser("prove", help="stage one claim from statement to document")
    prove.add_argument("claim", nargs="?", help="the claim in ordinary language")
    prove.add_argument("--backend", choices=("claude", "codex"), default="claude")
    # SUPPRESS so that omitting it here leaves the global --model alone rather
    # than overwriting it with this subparser's default.
    prove.add_argument("--model", default=argparse.SUPPRESS)
    accept = subparsers.add_parser("accept", help="run the checked-in acceptance problems")
    accept.add_argument("--backend", choices=("claude", "codex"), default="claude")
    accept.add_argument("--model", default=argparse.SUPPRESS)
    subparsers.add_parser("setup", help="discover, install, and record the pinned toolchain")
    accept.add_argument(
        "--force-budget-exhaustion-test",
        action="store_true",
        help="run the deterministic no-model path instead, and check its artifacts",
    )
    batch = subparsers.add_parser("batch", help="run the earlier one-shot proof experiment")
    batch.add_argument("request", type=Path)
    batch.add_argument("--output", type=Path, default=Path("hardy-output"))
    batch.add_argument("--max-turns", type=int, default=8)
    batch.add_argument("--wall-seconds", type=float, default=300)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    config = _config(args, parser)
    if args.command == "doctor":
        return doctor.report(doctor.run_checks(config, deep=args.deep))
    if args.command == "latency":
        return run_latency(args, config)
    if args.command == "prove":
        return run_prove(args)
    if args.command == "accept":
        return run_accept(args)
    if args.command == "setup":
        return run_setup(args)
    if args.command == "batch":
        return _batch(args, config, parser)
    # No subcommand is intentionally the primary interactive experience.
    return _chat(config, plain=args.plain, parser=parser, args=args)


if __name__ == "__main__":
    raise SystemExit(main())
