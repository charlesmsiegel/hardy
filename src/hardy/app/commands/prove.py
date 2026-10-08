"""`hardy prove`: one claim staged from statement to document."""
from __future__ import annotations

import argparse
import dataclasses
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from hardy.app.commands.settings import _load_config_argument
from hardy.app.terminal import ConsoleTerminal
from hardy.app.wiring import build_prove_workflow


def run_prove(
    args: argparse.Namespace,
    *,
    workflow_factory: Callable[..., Any] = build_prove_workflow,
    input_fn: Callable[[str], str] = input,
) -> int:
    from hardy.workflows.prove import ProveRequest

    strategy = getattr(args, "strategy", "iterative")
    history_mode = getattr(args, "history_mode", "full")
    if history_mode != "full" and strategy != "best-first":
        print("History replay requires --strategy best-first.")
        return 2
    config, config_path = _load_config_argument(getattr(args, "config", None))
    # Flags outrank the config file, the way every other setting resolves.
    reviewer = getattr(args, "faithfulness_model", None)
    if reviewer:
        config = dataclasses.replace(config, faithfulness_model=str(reviewer))
    claim = args.claim or input_fn("State the theorem in ordinary language: ").strip()
    if not claim:
        print("A nonempty theorem statement is required.")
        return 2
    try:
        assumptions = _declared_assumptions(getattr(args, "assume", None))
    except (OSError, ValueError) as error:
        # Refused before the run starts, not during it. A malformed
        # declaration is a mistake in the invocation, and reporting it as a
        # failed run would bury it in a manifest.
        print(f"The declared assumptions could not be read: {error}")
        return 2
    # Through `tui.prove`, which `/prove` uses too: the run directory a claim
    # lands in must not depend on which surface asked for it.
    from hardy.app.tui.prove import problem_slug

    slug = problem_slug(claim)
    terminal = ConsoleTerminal(input_fn=input_fn)
    workflow = workflow_factory(config, config_path, backend=getattr(args, "backend", "claude"))
    manifest = workflow.run(
        ProveRequest(
            text=claim,
            model=str(args.model or config.model),
            problem_slug=slug,
            assumptions=assumptions,
            strategy=strategy,
            history_mode=history_mode,
        ),
        terminal,
    )
    print(f"Artifacts: {config.runs_root}")
    return 0 if manifest.phase.value == "completed" else 1


def _declared_assumptions(path: Path | None) -> tuple[Any, ...]:
    """What a run declared it may stand on, read from one file.

    Validated here rather than trusted, because every field is load-bearing
    downstream: the name is written into the source the kernel checks, and
    the source is what a reader of the artifact follows to decide whether to
    believe an assumed result. A declaration with no provenance is an axiom
    somebody could have invented.
    """
    from hardy.formal.contracts import DeclaredAssumption

    if path is None:
        return ()
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("assumptions"), list):
        raise ValueError(f"{path} must hold an object with an `assumptions` list")
    declared = tuple(DeclaredAssumption.model_validate(item) for item in payload["assumptions"])
    if not declared:
        raise ValueError(f"{path} declares no assumptions")
    # The same rules the verifier applies before it writes a declaration into
    # the source the kernel reads. Run here so a malformed file costs nothing:
    # inside the verifier they land after formalization, the faithfulness read
    # and the whole proving loop.
    from hardy.workflows.admission import AdmissionPolicy, AdmissionRequest, TrustRequestKind

    policy = AdmissionPolicy()
    request = AdmissionRequest(TrustRequestKind.PREAUTHORIZED_RUN_ASSUMPTION)
    for item in declared:
        violation = policy.preauthorized_declaration(request, item)
        if violation is not None:
            raise ValueError(violation)
    return declared
