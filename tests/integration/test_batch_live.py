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

`HARDY_LIVE=1` costs more than money. These tests hand model-written Lean to an
unsandboxed subprocess, and Lean elaboration runs metaprogram code -- so this is
the one part of the suite that executes text a model chose, and it belongs in a
disposable development environment. Hardy says so in every artifact it writes;
by then the code has already run, so the warning is also emitted before the
first turn.
"""

from __future__ import annotations

import json
import os
import shutil
import warnings
from pathlib import Path

import pytest

from hardy import config as configuration
from hardy.cli import runtime_factory
from hardy.lean import LeanTools
from hardy.models import Request
from hardy.runner import WARNING, run


def _executable(command: str) -> str | None:
    """The Lean command as it will actually be run, or None if it is not there.

    A configured command can be a bare name to find on PATH or a path to a
    binary, and `shutil.which` answers for both.
    """
    return shutil.which(command)

pytestmark = [pytest.mark.live, pytest.mark.real_toolchain]

ROOT = Path(__file__).parents[2]
# Generous on purpose: a cold page cache turns a single `import Mathlib` into
# well over a minute, and this budget is a stall guard, not a target. A run that
# proves the theorem finishes long before it.
WALL_SECONDS = 900.0
# Values that mean "yes". `HARDY_LIVE=0` is a conventional way to say no, and
# every non-empty string is truthy in Python -- so a plain presence check would
# read a disabling value as an instruction to spend money.
ENABLED = {"1", "true", "yes", "on"}
# Terminal reasons that honestly describe a run which produced no proof. A
# `verified` here would mean the harness graded a false statement as proved.
HONEST_FAILURES = {"no_proof_submitted", "axioms_rejected", "turn_limit", "wall_clock_limit"}


@pytest.fixture(scope="module")
def live_config() -> configuration.Config:
    """The settings `hardy batch` would run under, confirmed to actually work.

    Hardy's own resolution rather than a private one: a model chosen in
    `config.toml` is the experimental condition this exercise is supposed to
    record, and reading only `HARDY_MODEL` would quietly bill a different one.
    """
    if os.environ.get("HARDY_LIVE", "").strip().lower() not in ENABLED:
        pytest.skip(
            "set HARDY_LIVE=1 to spend a subscription on these tests, and to run "
            f"model-written Lean unsandboxed. {WARNING}"
        )
    if shutil.which("claude") is None:
        pytest.skip("the Claude Code CLI the agent SDK drives is not installed")

    config = configuration.load()
    if config.lean_project is None:
        config = configuration.load(lean_project=ROOT / "lean_project")

    # The configured executable, not `lake`. `lean_command` is a setting, and a
    # working direct `lean` or a wrapper around one is a supported way to spell
    # it -- checking for `lake` unconditionally skipped the explicitly requested
    # suite over a binary this run would never have called.
    if _executable(config.lean_command[0]) is None:
        pytest.skip(f"the configured Lean command is not executable: {config.lean_command[0]}")

    # Before the first turn, not only in the artifacts afterwards: by the time a
    # writeup carries this sentence, the Lean it is warning about has run.
    warnings.warn(f"live run: {WARNING}", stacklevel=1)

    if _mathlib_olean(config.lean_project) is None:
        pytest.skip(f"Mathlib is not built in {config.lean_project}; run `hardy setup`")
    return config


def _mathlib_olean(project: Path) -> Path | None:
    """The built `Mathlib.olean`, in either layout, or None.

    A `lake-manifest.json` only proves dependencies were *resolved*. This
    repository's own `real-toolchain` job builds a `lean_project` holding no
    Mathlib at all, and its manifest looks exactly like a built one -- so a
    manifest check passes and the billable run then fails every proof attempt
    on a missing module.

    The artifact rather than an `import Mathlib` elaboration, which was tried
    first and is the worse guard: it costs 10 seconds against a warm page cache
    and over 100 against a cold one, so it exceeded the Lean timeout and skipped
    the whole suite it was added to protect. A probe that refuses the run it is
    guarding is not a cheaper failure than the one it prevents. This proves the
    module was built and not that it loads; the run itself is what says that.
    """
    direct = project / ".lake" / "build" / "lib" / "lean" / "Mathlib.olean"
    if direct.exists():
        return direct
    # A project that merely depends on Mathlib keeps it under `packages`.
    return next(iter(project.glob(".lake/packages/*/.lake/build/lib/lean/Mathlib.olean")), None)


def _request(declaration: str, claim: str) -> Request:
    return Request.from_dict(
        {"declaration": declaration, "informal_claim": claim, "imports": ["Mathlib"]}
    )


def _tools(request: Request, config: configuration.Config) -> LeanTools:
    return LeanTools(
        request,
        config.lean_command,
        timeout=config.lean_timeout,
        project=config.lean_project,
    )


def _run(request: Request, config: configuration.Config, output: Path, **limits):
    return run(request, runtime_factory(str(config.model)), _tools(request, config), output, **limits)


def _example() -> Request:
    return Request.from_dict(
        json.loads((ROOT / "examples" / "true.json").read_text(encoding="utf-8"))
    )


def _trajectory(output: Path) -> dict:
    return json.loads((output / "trajectory.json").read_text(encoding="utf-8"))


def test_a_real_model_proves_a_real_theorem_through_the_kernel(
    live_config: configuration.Config, tmp_path: Path
):
    """Reaching `verified` means the model chose `submit_proof`, not just `check_proof`."""
    result = _run(_example(), live_config, tmp_path, max_turns=8, wall_seconds=WALL_SECONDS)

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
    # What this experiment ran as. A paid result nobody can attribute to a model
    # and a library is an anecdote: `lake env lean` names a command, and two
    # projects on different Mathlib revisions answer to it identically.
    assert trajectory["model"] == str(live_config.model)
    assert trajectory["backend"] == "claude"
    assert trajectory["lean_command"] == list(live_config.lean_command)
    assert trajectory["lean_project"] == str(live_config.lean_project)
    # The compiler and Mathlib revisions behind that project are not recorded
    # here yet; FEATURES.md tracks pinning toolchain identities as outstanding.
    assert _mathlib_olean(live_config.lean_project) is not None
    # The provider ran the loop and said how many turns it took.
    assert isinstance(result.turns, int) and result.turns >= 1


def test_a_statement_that_cannot_be_proved_fails_honestly(
    live_config: configuration.Config, tmp_path: Path
):
    """A false statement must cost a refusal, not a grade."""
    request = _request(
        "theorem HardyTarget : ∀ n : ℕ, n = n + 1",
        "Every natural number equals its own successor.",
    )
    result = _run(request, live_config, tmp_path, max_turns=6, wall_seconds=WALL_SECONDS)

    assert result.terminal_reason in HONEST_FAILURES
    assert result.formalization == "not formalized"
    assert result.proof is None
    # Nothing was proved, so nothing may be presented as a proof.
    assert not (tmp_path / "proof.lean").exists()
    # Partial artifacts all the same: a failed run still owes a record.
    assert f"Terminal reason: `{result.terminal_reason}`" in (tmp_path / "writeup.md").read_text(encoding="utf-8")
    assert _trajectory(tmp_path)["events"]


def test_a_starved_wall_clock_is_recorded_as_a_budget_not_a_provider_error(
    live_config: configuration.Config, tmp_path: Path
):
    """The distinction is between Hardy's own limit and a provider failure.

    `runtime_error` here would blame Anthropic for a deadline Hardy set.

    One second, and not a plausible-looking five: the budget has to be one no
    environment can meet, or the test measures machine speed and fails on a fast
    one by verifying the theorem instead. A turn cannot complete inside it --
    spawning the CLI and reaching the provider costs longer than that on its own,
    before Lean is asked to load Mathlib.
    """
    result = _run(_example(), live_config, tmp_path, max_turns=8, wall_seconds=1)

    assert result.terminal_reason == "wall_clock_limit"
    assert result.proof is None
    assert not (tmp_path / "proof.lean").exists()
    # The provider's final result never arrives on this path, so there is no
    # turn count to report -- and 0 would be a measurement nobody took.
    assert result.turns is None

    limits = _trajectory(tmp_path)["limits"]
    assert limits["wall_seconds"] == 1
    assert limits["wall_clock_enforced_by"] == "hardy"
    # Hardy cancels the exchange; it does not kill a Lean check already running
    # on a worker thread, and that thread is waited on during shutdown. So the
    # run can overrun its budget, and `elapsed_seconds` says so rather than
    # reporting the budget back as if it had been kept.
    assert limits["elapsed_seconds"] >= limits["wall_seconds"]
