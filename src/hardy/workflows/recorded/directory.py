"""Audit whatever kept run a directory holds, staged or batch."""
from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError

from hardy.workflows.contracts import RunManifest
from hardy.workflows.recorded.batch import validate_batch_consistency
from hardy.workflows.recorded.staged import _live_staged_issues, validate_run_consistency


def validate_recorded_run(run_dir: Path) -> tuple[str, ...]:
    """Audit a kept run directory of either surface, with no model, network, or toolchain."""
    if not run_dir.is_dir():
        return (f"{run_dir} is not a directory",)
    manifest_path = run_dir / "manifest.json"
    if manifest_path.exists():
        try:
            manifest = RunManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
        except ValidationError as error:
            return (f"manifest.json does not validate: {error.error_count()} error(s)",)
        return tuple(validate_run_consistency(run_dir, manifest)) + tuple(_live_staged_issues(run_dir, manifest))
    if (run_dir / "result.json").exists():
        return validate_batch_consistency(run_dir)
    # A staged run is written under `runs_root/<timestamp>-<slug>-<id>/`, so
    # the directory a reader names -- `acceptance/recorded/prove-verified` --
    # holds the run rather than being it. One nested run is audited as the
    # run; several is an ambiguity reported rather than resolved.
    nested = sorted(
        child for child in run_dir.iterdir()
        if child.is_dir() and ((child / "manifest.json").exists() or (child / "result.json").exists())
    )
    if len(nested) == 1:
        return validate_recorded_run(nested[0])
    if nested:
        return (f"{run_dir} holds {len(nested)} runs; name one of them",)
    return ("not a Hardy run directory: neither manifest.json nor result.json is here",)
