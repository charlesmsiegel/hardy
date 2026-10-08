"""`hardy accept`: the checked-in acceptance problems, or an audit of recorded runs."""
from __future__ import annotations

import argparse
import dataclasses
import json
from importlib.resources import files
from pathlib import Path
from typing import Any

from hardy.app.commands.settings import _load_config_argument
from hardy.app.terminal import ConsoleTerminal
from hardy.app.wiring import build_prove_workflow


def run_accept(args: argparse.Namespace) -> int:
    from hardy.documents.contracts import DocumentStatus
    from hardy.formal.contracts import FormalStatus
    from hardy.workflows.acceptance import run_deterministic_experiment, validate_run_consistency
    from hardy.workflows.contracts import FaithfulnessStatus, RunPhase, TerminalReason
    from hardy.workflows.prove import ProveRequest

    recorded = getattr(args, "recorded", None)
    if recorded:
        from hardy.workflows.acceptance import validate_recorded_run

        all_passed = True
        for run_dir in recorded:
            issues = validate_recorded_run(Path(run_dir))
            print(f"Recorded run: {run_dir}")
            for issue in issues:
                print("CONSISTENCY ERROR: " + issue)
            all_passed = all_passed and not issues
        print("All recorded runs are self-consistent." if all_passed else "A recorded run failed its audit.")
        return 0 if all_passed else 1

    config, config_path = _load_config_argument(getattr(args, "config", None))
    reviewer = getattr(args, "faithfulness_model", None)
    if reviewer:
        config = dataclasses.replace(config, faithfulness_model=str(reviewer))
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
        files("hardy.workflows").joinpath("acceptance_problems.json").read_text(encoding="utf-8")
    )
    # Any number of problems, each with an id and an input: the set grows as
    # the acceptance test does (it began with two trivial statements), and a
    # count check here refused the third before it could run.
    problems = payload.get("problems", [])
    if payload.get("schema_version") != 1 or not problems or any(
        not isinstance(item, dict) or not item.get("id") or not item.get("input") for item in problems
    ):
        print("The checked-in acceptance problem set is invalid.")
        return 2
    all_passed = True
    for problem in problems:
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
