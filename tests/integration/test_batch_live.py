"""The batch runner against a real model and a real Lean, end to end.

Everything else that covers `hardy batch` drives it with a scripted runtime and
a stand-in Lean, so it answers "does the harness do what the script said" and
cannot answer "does a model use these tools as intended". Those are different
questions, and only the second one decides whether `submit_proof` is ever
called -- which is the whole difference between `verified` and
`no_proof_submitted`. See issue #26.

Billable, so never implicit: `HARDY_LIVE` has to be set on purpose. The marker
alone would not do, because the hermetic CI job runs `pytest` with no `-m`
filter and would spend a subscription on every push.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

from hardy.cli import runtime_factory
from hardy.config import DEFAULT_MODEL
from hardy.lean import LeanTools
from hardy.models import Request
from hardy.runner import run

pytestmark = [pytest.mark.live, pytest.mark.real_toolchain]

ROOT = Path(__file__).parents[2]
# Long enough for a cold `import Mathlib`, which costs tens of seconds before
# the first tactic is even read.
LEAN_TIMEOUT = 180.0
WALL_SECONDS = 300.0
# Terminal reasons that honestly describe a run which produced no proof. A
# `verified` here would mean the harness graded a false statement as proved.
HONEST_FAILURES = {"no_proof_submitted", "axioms_rejected", "turn_limit", "wall_clock_limit"}


def _lean_project() -> Path:
    configured = os.environ.get("HARDY_LEAN_PROJECT")
    return Path(configured) if configured else ROOT / "lean_project"


@pytest.fixture(scope="module")
def live_lean_project() -> Path:
    if not os.environ.get("HARDY_LIVE"):
        pytest.skip("set HARDY_LIVE=1 to spend a subscription on these tests")
    if shutil.which("claude") is None:
        pytest.skip("the Claude Code CLI the agent SDK drives is not installed")
    if shutil.which("lake") is None:
        pytest.skip("lake is not installed")
    project = _lean_project()
    if not (project / "lake-manifest.json").exists():
        pytest.skip(f"no built Lean project at {project}; run `hardy setup`")
    return project


def _tools(request: Request, project: Path) -> LeanTools:
    return LeanTools(request, ("lake", "env", "lean"), timeout=LEAN_TIMEOUT, project=project)


def _run(request: Request, project: Path, output: Path, **limits):
    model = os.environ.get("HARDY_MODEL", DEFAULT_MODEL)
    return run(request, runtime_factory(model), _tools(request, project), output, **limits)


def _trajectory(output: Path) -> dict:
    return json.loads((output / "trajectory.json").read_text(encoding="utf-8"))


def test_a_real_model_proves_a_real_theorem_through_the_kernel(live_lean_project: Path, tmp_path: Path):
    """Reaching `verified` means the model chose `submit_proof`, not just `check_proof`."""
    request = Request.from_dict(json.loads((ROOT / "examples" / "true.json").read_text(encoding="utf-8")))
    result = _run(request, live_lean_project, tmp_path, max_turns=8, wall_seconds=WALL_SECONDS)

    assert result.terminal_reason == "verified"
    assert result.formalization == "kernel verified"
    # Kernel verified and standing on nothing but Lean's own axioms -- the two
    # halves of the claim, checked separately because the audit can refuse a
    # proof Lean accepted.
    assert result.axioms["status"] == "clean"
    assert result.axioms["declarations"][0]["name"] == "HardyTarget"

    # The artifact, not the exit code. A `proof.lean` without its audit line is
    # a file nobody can recheck.
    source = (tmp_path / "proof.lean").read_text(encoding="utf-8")
    assert "theorem HardyTarget : True :=" in source
    assert "#print axioms HardyTarget" in source
    assert result.proof is not None and result.proof in source

    writeup = (tmp_path / "writeup.md").read_text(encoding="utf-8")
    assert "Formalization: **kernel verified**" in writeup
    assert "No completed artifact" not in writeup

    trajectory = _trajectory(tmp_path)
    assert trajectory["terminal_reason"] == "verified"
    assert any(event.get("name") == "submit_proof" for event in trajectory["events"])
    # The provider ran the loop and said how many turns it took.
    assert isinstance(result.turns, int) and result.turns >= 1


def test_a_statement_that_cannot_be_proved_fails_honestly(live_lean_project: Path, tmp_path: Path):
    """A false statement must cost a refusal, not a grade."""
    request = Request.from_dict(
        {
            "informal_claim": "Every natural number equals its own successor.",
            "declaration": "theorem HardyTarget : ∀ n : ℕ, n = n + 1",
            "imports": ["Mathlib"],
        }
    )
    result = _run(request, live_lean_project, tmp_path, max_turns=6, wall_seconds=WALL_SECONDS)

    assert result.terminal_reason in HONEST_FAILURES
    assert result.formalization == "not formalized"
    assert result.proof is None
    # Nothing was proved, so nothing may be presented as a proof.
    assert not (tmp_path / "proof.lean").exists()
    # Partial artifacts all the same: a failed run still owes a record.
    assert f"Terminal reason: `{result.terminal_reason}`" in (tmp_path / "writeup.md").read_text(encoding="utf-8")
    assert _trajectory(tmp_path)["events"]


def test_a_starved_wall_clock_is_recorded_as_a_budget_not_a_provider_error(
    live_lean_project: Path, tmp_path: Path
):
    """Five seconds cannot outlast one `import Mathlib`, so the budget decides the run.

    The distinction this pins is between Hardy's own limit and a provider
    failure. `runtime_error` here would blame Anthropic for a deadline Hardy set.
    """
    request = Request.from_dict(json.loads((ROOT / "examples" / "true.json").read_text(encoding="utf-8")))
    result = _run(request, live_lean_project, tmp_path, max_turns=8, wall_seconds=5)

    assert result.terminal_reason == "wall_clock_limit"
    assert result.proof is None
    assert not (tmp_path / "proof.lean").exists()
    # The provider's final result never arrives on this path, so there is no
    # turn count to report -- and 0 would be a measurement nobody took.
    assert result.turns is None

    limits = _trajectory(tmp_path)["limits"]
    assert limits["wall_seconds"] == 5
    assert limits["wall_clock_enforced_by"] == "hardy"
    # Hardy cancels the exchange; it does not kill a Lean check already running
    # on a worker thread, and that thread is waited on during shutdown. So the
    # run overruns its budget, and `elapsed_seconds` says so rather than
    # reporting the budget back as if it had been kept.
    assert limits["elapsed_seconds"] >= limits["wall_seconds"]
