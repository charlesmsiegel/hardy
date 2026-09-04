"""Cheap Lean closers before a model turn is spent (#23).

The feature is only honest if two things hold: a tactic's proof goes in by the
same door a model's does, and the record says which of the two a result came
from. Both are tested here, along with the default that keeps a scoreboard
comparable — nobody gets a ladder they did not ask for.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from hardy import closers
from hardy.lean import LeanTools
from hardy.models import Request
from hardy.runner import run


class FakeRuntime:
    model = "fake-model@test"
    backend = "claude"
    endpoint = "fake"

    def __init__(self, script, **context):
        self.script, self.context = list(script), context
        self.asked = False

    def ask(self, text: str) -> str:
        self.asked = True
        for name, arguments in self.script:
            self.context["dispatch"](name, arguments)
        return ""


def factory(script, seen=None):
    def make(model=None, **context):
        runtime = FakeRuntime(script, **context)
        if seen is not None:
            seen.append(runtime)
        return runtime

    return make


@pytest.fixture
def proof_request() -> Request:
    return Request.from_dict(
        {"declaration": "theorem HardyTarget : True", "informal_claim": "True is true."}
    )


@pytest.fixture
def lean(proof_request: Request) -> LeanTools:
    fake = Path(__file__).parents[1] / "fake_lean.py"
    return LeanTools(proof_request, (sys.executable, str(fake)))


def test_the_ladder_stops_at_the_first_tactic_that_is_accepted() -> None:
    tried: list[str] = []

    def submit(proof: str) -> tuple[bool, str]:
        tried.append(proof)
        return proof == "by simp", "said so"

    outcome = closers.close(submit, ("rfl", "simp", "aesop"))

    assert tried == ["by rfl", "by simp"]
    assert outcome.closed_by == "simp"
    assert [item.ok for item in outcome.attempts] == [False, True]


def test_a_ladder_that_closed_nothing_still_records_what_it_tried() -> None:
    # "Nothing closed it" is a measurement. A record holding only successes
    # cannot tell a ladder that ran and failed from one that never ran.
    outcome = closers.close(lambda proof: (False, "no"), ("rfl", "simp"))

    assert not outcome.closed
    assert [item.tactic for item in outcome.attempts] == ["rfl", "simp"]
    assert outcome.as_dict()["closed_by"] is None
    assert outcome.as_dict()["enabled"] is True


def test_a_spent_budget_stops_the_ladder_paying_for_elaborations() -> None:
    calls: list[str] = []
    allowed = iter([True, False])

    outcome = closers.close(
        lambda proof: (calls.append(proof), (False, "no"))[1],
        ("rfl", "simp", "aesop"),
        keep_going=lambda: next(allowed, False),
    )

    assert calls == ["by rfl"]
    assert not outcome.closed


def test_no_ladder_runs_unless_one_was_asked_for(tmp_path: Path, proof_request: Request, lean: LeanTools) -> None:
    seen: list[FakeRuntime] = []
    run(proof_request, factory([], seen), lean, tmp_path)

    trajectory = json.loads((tmp_path / "trajectory.json").read_text(encoding="utf-8"))

    # Explicit, not absent: a missing key reads as a harness with no closers,
    # and this one has them and was told not to use them.
    assert trajectory["closers"] == {
        "enabled": False, "tactics": [], "attempts": [], "closed_by": None, "seconds": 0.0
    }
    assert seen[0].asked is True


def test_a_closed_statement_never_reaches_the_model(tmp_path: Path, proof_request: Request, lean: LeanTools) -> None:
    seen: list[FakeRuntime] = []
    result = run(
        proof_request,
        factory([], seen),
        lean,
        tmp_path,
        closers=("exact True.intro",),
    )

    assert result.terminal_reason == "verified"
    assert seen[0].asked is False
    trajectory = json.loads((tmp_path / "trajectory.json").read_text(encoding="utf-8"))
    assert trajectory["closers"]["closed_by"] == "exact True.intro"
    assert any(event.get("type") == "declined_turn" for event in trajectory["events"])


def test_a_run_that_asked_nobody_anything_is_billed_for_nothing(tmp_path: Path, proof_request: Request, lean: LeanTools) -> None:
    result = run(proof_request, factory([]), lean, tmp_path, closers=("exact True.intro",))

    # One exchange with nothing stated about it is what a run that *asked* and
    # got no report looks like. A run that asked nothing spent nothing.
    assert result.usage["exchanges"] == 0


def test_a_ladder_that_closes_nothing_hands_the_turn_to_the_model(tmp_path: Path, proof_request: Request, lean: LeanTools) -> None:
    seen: list[FakeRuntime] = []
    result = run(
        proof_request,
        factory([("submit_proof", {"proof": "by exact True.intro"})], seen),
        lean,
        tmp_path,
        closers=("nonsense_tactic",),
    )

    assert seen[0].asked is True
    assert result.terminal_reason == "verified"
    trajectory = json.loads((tmp_path / "trajectory.json").read_text(encoding="utf-8"))
    assert trajectory["closers"]["closed_by"] is None
    assert [item["tactic"] for item in trajectory["closers"]["attempts"]] == ["nonsense_tactic"]


def test_a_tactic_goes_through_the_same_audit_a_model_would(tmp_path: Path, proof_request: Request, lean: LeanTools) -> None:
    # The stand-in reports whatever axioms a marker names, and the audit
    # refuses `sorryAx` from a tactic exactly as it refuses it from a model:
    # the ladder is a decision about whose turn it is, never a second route to
    # a verdict.
    result = run(
        proof_request,
        factory([]),
        lean,
        tmp_path,
        closers=("exact True.intro -- axioms: sorryAx",),
    )

    assert result.terminal_reason == "axioms_rejected"
    assert result.proof is None


def test_the_trajectory_names_who_kept_each_bound(tmp_path: Path, proof_request: Request, lean: LeanTools) -> None:
    run(proof_request, factory([]), lean, tmp_path)

    limits = json.loads((tmp_path / "trajectory.json").read_text(encoding="utf-8"))["limits"]

    # The SDK backend does not enforce the turn bound, and the record says so
    # rather than claiming a guarantee the harness cannot make.
    assert limits["turns_enforced_by"] == "provider sdk"
    assert limits["wall_clock_enforced_by"] == "hardy"
    assert "issue #23" in limits["note"]


def test_a_harness_owned_loop_says_hardy_kept_both(tmp_path: Path, proof_request: Request, lean: LeanTools) -> None:
    def make(model=None, **context):
        runtime = FakeRuntime([], **context)
        runtime.enforcement = {"turns": "hardy", "wall_clock": "hardy"}
        return runtime

    run(proof_request, make, lean, tmp_path)

    limits = json.loads((tmp_path / "trajectory.json").read_text(encoding="utf-8"))["limits"]

    assert limits["turns_enforced_by"] == "hardy"
    # And no note pointing at the open issue: there is nothing left of it to
    # point at on this transport.
    assert "note" not in limits


def test_the_ladder_spends_the_runs_clock_and_not_a_clock_of_its_own(tmp_path: Path, proof_request: Request, lean: LeanTools) -> None:
    """A ladder that used the whole budget must not hand the model a fresh one.

    Left as it was, the command could take the ladder's time plus the entire
    declared `wall_seconds` again, and the figure in the trajectory would bound
    neither half.
    """
    seen: list[FakeRuntime] = []

    result = run(
        proof_request,
        factory([], seen),
        lean,
        tmp_path,
        # Already spent by the time the one closer has run.
        wall_seconds=0.0001,
        closers=("nonsense_tactic",),
    )

    assert seen[0].asked is False
    assert result.terminal_reason == "wall_clock_limit"
    trajectory = json.loads((tmp_path / "trajectory.json").read_text(encoding="utf-8"))
    assert any(
        event.get("type") == "limit" and event.get("limit") == "wall_seconds"
        for event in trajectory["events"]
    )


def test_the_model_gets_only_what_the_ladder_left(tmp_path: Path, proof_request: Request, lean: LeanTools) -> None:
    seen: list[FakeRuntime] = []

    run(proof_request, factory([], seen), lean, tmp_path, wall_seconds=300, closers=("nonsense_tactic",))

    # Not the declared 300: what remains of it after the ladder elaborated.
    assert 0 < seen[0].context["wall_seconds"] < 300


def test_what_the_ladder_cost_is_recorded(tmp_path: Path, proof_request: Request, lean: LeanTools) -> None:
    # A run that spent four minutes elaborating tactics and then reported a
    # model turn limit is not readable without this.
    run(proof_request, factory([]), lean, tmp_path, closers=("nonsense_tactic",))

    trajectory = json.loads((tmp_path / "trajectory.json").read_text(encoding="utf-8"))

    assert trajectory["closers"]["seconds"] > 0


def test_a_closer_whose_proof_lands_after_the_deadline_closes_nothing(tmp_path: Path, proof_request: Request, lean: LeanTools) -> None:
    """A check that began inside the deadline and finished outside it is
    discarded, so naming its tactic in `closed_by` would credit a closer for a
    run that terminates with no verified proof."""
    seen: list[FakeRuntime] = []

    result = run(
        proof_request,
        factory([], seen),
        lean,
        tmp_path,
        # Expired before the closer's own Lean call can finish.
        wall_seconds=0.0001,
        closers=("exact True.intro",),
    )

    trajectory = json.loads((tmp_path / "trajectory.json").read_text(encoding="utf-8"))

    assert result.terminal_reason != "verified"
    assert trajectory["closers"]["closed_by"] is None
    assert not any(event.get("type") == "declined_turn" for event in trajectory["events"])


def test_a_run_the_ladder_finishes_never_needs_the_providers_credentials(
    tmp_path: Path, proof_request: Request, lean: LeanTools, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Lean has already accepted the proof by the time the runtime is built.
    Building the API client there turned a missing key into a crash, with none
    of the artifacts the run had earned. The runtime is still constructed --
    the record names the backend a run was configured for even when it spoke
    to nobody -- but nothing that never happens may fail."""
    from hardy.api_runtime import ApiRuntime

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    def make(model=None, **context):
        return ApiRuntime(model or "claude-test", **context)

    result = run(proof_request, make, lean, tmp_path, closers=("exact True.intro",))

    assert result.terminal_reason == "verified"
    assert (tmp_path / "proof.lean").exists()
    trajectory = json.loads((tmp_path / "trajectory.json").read_text(encoding="utf-8"))
    # And the record still says which backend this run was configured for.
    assert trajectory["backend"] == "anthropic-api"
