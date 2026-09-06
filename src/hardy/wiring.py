"""How a run is wired together: which runtime drives the turn loop, and how the staged workflow is assembled.

Separated from `cli.py` because a benchmark's pooling key digests every module
that can change a run's outcome. These two functions can; the argument parsing
that surrounds them in `cli.py` cannot, and folding the whole CLI into that
digest would stale a pool whenever an unrelated command grew a flag.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from importlib import metadata
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from typing import Any

from . import cas_tools, claude_runtime, doctor
from . import config as configuration


def runtime_factory(default_model: str, backend: str = configuration.DEFAULT_BACKEND) -> Callable[..., Any]:
    """A way for the session to build its runtime once it can offer the tools.

    `backend` chooses the transport, and with it who owns the turn loop. The
    default authenticates through the Claude Code agent SDK and needs no API
    key, at the cost of the SDK running the loop (issue #23); `api` runs the
    loop in Hardy and needs `ANTHROPIC_API_KEY`. Imported where it is chosen
    rather than at module scope, so a machine with neither the Anthropic SDK
    nor a key installed still starts on the default.
    """

    def make(model: str | None = None, **context: Any) -> Any:
        if backend == "api":
            from .api_runtime import ApiRuntime

            return ApiRuntime(model or default_model, **context)
        return claude_runtime.ClaudeAgentRuntime(model or default_model, **context)

    return make


def build_prove_workflow(config: configuration.Config, config_path: Path, *, backend: str = "claude"):
    """Assemble the staged workflow around the chosen backend."""
    from . import lean as lean_module
    from . import retrieval
    from .declarations import DeclarationIndex
    from .lean import LeanService
    from .mcp_server import LeanToolRuntime
    from .prompts import PROMPT_SET_SHA256
    from .verifier import FinalVerifier
    from .workflow import ProveWorkflow
    from .writeup import RunIdentities, build_writeup, tectonic_version

    # Identified by the Lean the verifier will run -- `config.lake env lean`,
    # exactly as `FinalVerifier` spells it -- so the identity the claim is
    # frozen under names the compiler that checks it and not the one on PATH.
    try:
        environment = lean_module.environment_identity(
            config.lean_project,
            lean_command=(str(config.lake), "env", "lean"),
            timeout_seconds=config.limits.lean_process_seconds,
        )
    except (ValueError, OSError, KeyError, StopIteration, json.JSONDecodeError) as error:
        # A Lean that cannot be identified is a setup failure, and a setup
        # failure is a run: the workflow writes a manifest and a trajectory
        # saying so, where a traceback here would leave nothing on disk.
        return _unidentified_workflow(config, str(error) or type(error).__name__)
    # Asked of the binary once, here, and written into every document: a
    # version literal beside a real bundle digest read as a pinned toolchain
    # while describing whatever release the machine happened to have.
    compiler_version = tectonic_version(config.tectonic, config.limits)
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
            tectonic_version=compiler_version,
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
        # One declaration index for the whole run: it is read-only once built,
        # and the one-time source scan should not be paid per proving stage.
        declarations = DeclarationIndex(config.lean_project)
        return ClaudeStagedRuntime(
            store=store,
            lean_runtime_factory=lambda claim, allowed=(): LeanToolRuntime(
                claim=claim,
                allowed=allowed,
                service=lean,
                store=store,
                official_checks=config.limits.official_checks,
                observation_bytes=config.limits.model_observation_bytes,
                # One retriever per proving stage, because the retrieval budget
                # is spent across the stage rather than per call.
                retriever=retrieval.build_retriever(lean, config.limits, declarations),
                declarations=declarations,
            ),
            cas_runtime=cas_runtime,
            cas_directory=cas_directory,
        )

    def staged_doctor(value: configuration.Config) -> Any:
        # The backend this workflow is actually building, not the one the
        # global config names for interactive and batch work. They are
        # different settings and a staged run is entitled to have its own
        # credentials checked rather than somebody else's.
        checks = doctor.run_checks(value, backend=backend)
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


def _unidentified_workflow(config: configuration.Config, reason: str):
    """A staged workflow that can only fail setup, because its Lean has no identity.

    The doctor is the workflow's own boundary for an unusable machine, so the
    reason is reported through it and the run ends `setup_failure` with a
    manifest that names no environment -- rather than one that names a
    compiler nobody identified.
    """
    from .workflow import ProveWorkflow

    def unusable(_: configuration.Config) -> Any:
        return SimpleNamespace(healthy=False, authenticated=True, detail=reason)

    def refuse(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("the Lean toolchain could not be identified: " + reason)

    return ProveWorkflow(
        config=config,
        environment=None,
        doctor=unusable,
        lean=SimpleNamespace(check_proof=refuse),
        runtime_factory=refuse,
        verifier=SimpleNamespace(verify=refuse),
        writeup_builder=refuse,
        identities_factory=refuse,
    )


def _sdk_version(backend: str) -> str:
    package = "openai-codex" if backend == "codex" else "claude-agent-sdk"
    try:
        return metadata.version(package)
    except metadata.PackageNotFoundError:
        return "not installed"
