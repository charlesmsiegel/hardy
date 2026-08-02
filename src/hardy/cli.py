from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
from collections.abc import Callable
from importlib import metadata
from importlib.resources import files
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from typing import Any

from . import cas_tools, claude_runtime, doctor, latency
from . import config as configuration
from .cas import CasError
from .cas_export import export_session
from .chat import MathematicsSession
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
            blocking.write("Hardy wants to introduce an assumption:", style="warning")
            blocking.write(f"  Informal: {proposal['informal_statement']}")
            blocking.write(f"  Lean: axiom {proposal['formal_name']} : {proposal['lean_statement']}")
            blocking.write(f"  Source: {proposal['source']}")
            blocking.write(f"  Reason: {proposal['reason']}")
            picked = blocking.choose(
                f"Approve the assumption {proposal['formal_name']}?",
                [Choice("no", "No, decline it"), Choice("yes", "Yes, approve it")],
                current=0,
            )
        except Exception:  # noqa: BLE001 - every non-approval path is a decline, never a crash
            return False
        return picked is not None and picked.value == "yes"

    return confirm


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


def _chat(config: configuration.Config, *, plain: bool = False) -> int:
    from .tui import run_session

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
        log_path=config.workspace / "cas" / "cells.jsonl",
        cwd=config.workspace / "cas",
    )

    def build(confirm: Callable[[dict[str, str]], bool]) -> MathematicsSession:
        return MathematicsSession(
            config.workspace,
            runtime_factory(str(config.model)),
            config.lean_command,
            config.latex_command,
            confirm,
            lean_project=config.lean_project,
            lean_timeout=config.lean_timeout,
            cas=cas,
            cas_detail=cas_detail,
        )

    try:
        return run_session(config, build, plain=plain)
    finally:
        # Not reached at all if a forced double-Ctrl+C exit inside the shell
        # reaches `os._exit` -- that bypasses every `finally` in the process,
        # not just this one. Accepted for the same reason a forced exit
        # already leaves Lean/LaTeX subprocesses orphaned: the user was
        # warned before pressing Ctrl+C a second time.
        if cas is not None:
            cas.session.close()


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


def _environment_identity(config: configuration.Config) -> Any:
    """Identify the exact Lean environment a run is frozen against."""
    from .domain import EnvironmentIdentity

    if config.lean_project is None:
        raise ValueError("a staged run needs lean_project set to a built Lake project")
    manifest_path = config.lean_project / "lake-manifest.json"
    if not manifest_path.exists():
        raise ValueError(f"{manifest_path} is missing; run the installer to build the project")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    mathlib = next(item for item in manifest["packages"] if item["name"] == "mathlib")
    return EnvironmentIdentity(
        lean_version="4.32.0",
        lean_commit="8c9756b28d64dab099da31a4c09229a9e6a2ef35",
        mathlib_revision=mathlib["rev"],
        lake_manifest_sha256=hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        imports=("Mathlib",),
    )


def build_prove_workflow(config: configuration.Config, config_path: Path, *, backend: str = "claude"):
    """Assemble the staged workflow around the chosen backend."""
    from .lean import LeanService
    from .mcp_server import LeanToolRuntime
    from .prompts import PROMPT_SET_SHA256
    from .verifier import FinalVerifier
    from .workflow import ProveWorkflow
    from .writeup import RunIdentities, build_writeup

    environment = _environment_identity(config)
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


def run_latency(args: argparse.Namespace, config: configuration.Config) -> int:
    """Measure the fixed Lean import cost, for the gate in DESIGN.md and #54.

    Runs where the ordinary checks run -- inside the configured Lake project,
    through the configured Lean command -- because an import cost measured
    against a different Mathlib is not the cost this harness pays.
    """
    imports = tuple(args.imports or ("Mathlib",))
    total_ms = None if args.total_seconds is None else round(args.total_seconds * 1_000)
    # Checked before probing, not after: each probe pays a full Mathlib import,
    # and rejecting a negative --calls once minutes have been spent is a
    # traceback where a usage error belongs.
    if args.calls is not None and args.calls < 0:
        print("--calls cannot be negative")
        return 2
    if total_ms is not None and total_ms < 0:
        print("--total-seconds cannot be negative")
        return 2
    if args.timeout <= 0:
        print("--timeout must be positive")
        return 2
    project = config.lean_project if config.lean_project is not None else Path.cwd()
    # A deleted project makes `Popen` raise `FileNotFoundError` for the working
    # directory, which would otherwise be reported as a missing Lean; a project
    # path that is a regular file raises `NotADirectoryError` and escaped as a
    # traceback. Checked up front, as `LeanTools._run` and `doctor` both do.
    if not project.is_dir():
        print(f"Lean project directory not found: {project}")
        return 1
    try:
        cost = latency.measure_import_cost(
            imports,
            argv=(*config.lean_command, "--json"),
            cwd=project,
            timeout_seconds=args.timeout,
            repeats=args.repeats,
        )
    except FileNotFoundError:
        print(f"Lean executable not found: {config.lean_command[0]}")
        return 1
    except ValueError as error:
        print(str(error))
        return 2
    return latency.report(
        cost, calls=args.calls, total_ms=total_ms, threshold=args.threshold
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
    chat.add_argument("--workspace", type=Path, help=f"workspace directory (default {configuration.DEFAULT_WORKSPACE})")
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
    measure.add_argument("--calls", type=int, help="Lean calls made by an observed run, for a verdict")
    measure.add_argument("--total-seconds", type=float, help="wall time of that observed run, for a verdict")
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
    return _chat(config, plain=args.plain)


if __name__ == "__main__":
    raise SystemExit(main())
