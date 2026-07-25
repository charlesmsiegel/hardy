from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import os
import re
import shutil
from collections.abc import Callable
from importlib import metadata
from importlib.resources import files
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from . import catalog, claude_runtime, doctor
from . import config as configuration
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
        self._output(f"WARNING: {WARNING} LaTeX is also executed without isolation.")
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
        from .staged import ClaudeStagedRuntime

        return ClaudeStagedRuntime(
            store=store,
            lean_runtime_factory=lambda claim: LeanToolRuntime(
                claim=claim,
                service=lean,
                store=store,
                official_checks=config.limits.official_checks,
                observation_bytes=config.limits.model_observation_bytes,
            ),
        )

    def staged_doctor(value: configuration.Config) -> Any:
        checks = doctor.run_checks(value)
        return SimpleNamespace(
            healthy=all(check.ok for check in checks),
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


def build_parser() -> argparse.ArgumentParser:
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
    if args.command == "prove":
        return run_prove(args)
    if args.command == "accept":
        return run_accept(args)
    if args.command == "setup":
        return run_setup(args)
    if args.command == "batch":
        return _batch(args, config, parser)
    # No subcommand is intentionally the primary interactive experience.
    return _chat(config, parser)


if __name__ == "__main__":
    raise SystemExit(main())
