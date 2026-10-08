"""The staged controller's whole observable sequence, pinned per scenario.

`test_workflow.py` asserts what each scenario must and must not do. This
records everything each one does: every trajectory event in order with its
phase, the transitions and the terminal record, every provider exchange the
runtime was asked for, every thread opened and the one a cancellation
reached, every verifier call, what the terminal was shown, and the manifest's
phase, reason, grades and artifact names. Restructuring `ProveWorkflow` must
leave every scenario identical -- the same stages in the same order, no extra
model or Lean call, the same finalization on each failure. A deliberate
change regenerates it in the same commit:

    uv run python tests/unit/test_prove_sequence_golden.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest
from test_workflow import Terminal, _proposal, _review, _scripted_controller
from test_workflow_frontier import _runtime as _frontier_runtime

GOLDEN = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "prove-sequence.json"

# Payload keys small and stable enough to pin. Anything holding a hash of
# Hardy's own source (`workflow.strategy`) or a path is reduced to its kind.
PAYLOAD_KEYS = {
    "workflow.transition": ("from", "to"),
    "workflow.terminal": ("phase", "terminal_reason"),
    "user.approval": ("choice",),
    "formalization.malformed": ("proposal",),
    "formalization.rejected": ("proposal", "reason"),
    "workflow.error": ("type", "message"),
    "workflow.cancelled": ("type", "message"),
    "workflow.setup": ("healthy",),
    "workflow.verifier_call": ("official_check_number",),
}


class _Clock:
    """A monotonic clock that advances one second per reading."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        self.now += 1.0
        return self.now


def _scenarios():
    domain = __import__("hardy.workflows.contracts", fromlist=["x"])
    unreachable = RuntimeError("reader unreachable")
    return {
        "verified": {},
        "revise-then-approve": {"terminal": {"decisions": ("revise", "approve"), "revisions": ("Use ints.",)},
                                "proposals": [_proposal(domain), _proposal(domain)]},
        "rejected": {"terminal": {"decisions": ("cancel",)}},
        "not-acknowledged": {"terminal": {"acknowledge": False}},
        "unhealthy": {"healthy": False},
        "unauthenticated": {"healthy": False, "authenticated": False},
        "elaboration-repaired": {"proposals": [_proposal(domain), _proposal(domain)], "elaborations": [False, True]},
        "malformed-exhausts": {"proposals": [ValueError("empty")] * 8},
        "disputed": {"reviews": [_review(domain, agrees=False, divergences=("weaker",))]},
        "reader-unreachable": {"reviews": [unreachable]},
        "proof-repaired": {"proof_results": [False, True]},
        "proof-never-verifies": {"proof_results": [False] * 20},
        "tex-failed": {"document_status": "tex_failed"},
        "runtime-error": {"runtime_error": True},
        **{f"interrupt-{stage}": {"interrupt_stage": stage}
           for stage in ("formalization", "faithfulness", "proof", "writeup")},
        **{f"cancel-{stage}": {"cancel_stage": stage}
           for stage in ("formalization", "faithfulness", "proof", "writeup")},
        **{f"cancel-quietly-{stage}": {"cancel_quietly_at": stage}
           for stage in ("formalization", "faithfulness", "proof", "writeup")},
        "best-first-repaired": {"proof_results": [False, True], "strategy": "best-first"},
        "best-first-replay": {"proof_results": [False, True], "strategy": "best-first",
                              "history_mode": "compact"},
        "best-first-cancel-quietly-faithfulness": {"cancel_quietly_at": "faithfulness",
                                                   "strategy": "best-first"},
    }


def _event(event: dict) -> list:
    kind = event.get("kind")
    payload = event.get("payload") or {}
    kept = {key: payload.get(key) for key in PAYLOAD_KEYS.get(kind, ())}
    return [kind, event.get("phase"), kept] if kept else [kind, event.get("phase")]


def observe(name: str, tmp_path: Path) -> dict:
    options = dict(_scenarios()[name])
    terminal_options = options.pop("terminal", {})
    strategy = options.pop("strategy", "iterative")
    history_mode = options.pop("history_mode", "full")
    clock = _Clock()
    workflow, domain, controller, state = _scripted_controller(tmp_path, monotonic=clock, **options)
    if strategy == "best-first":
        _frontier_runtime(controller, state)
    terminal = Terminal(**terminal_options)
    manifest = controller.run(
        workflow.ProveRequest(text="Two equals two.", model="fixture", strategy=strategy,
                              history_mode=history_mode),
        terminal,
    )
    root = next(tmp_path.rglob("manifest.json")).parent
    events = [json.loads(line) for line in (root / "trajectory.jsonl").read_text(encoding="utf-8").splitlines()]
    grades = manifest.grades
    return {
        "events": [_event(event) for event in events],
        "exchanges": [stage for stage, _ in state.prompts],
        "threads": len(state.starts),
        "claims_on_threads": [claim is not None for claim in state.starts],
        "cancelled_thread": (state.handles.index(state.cancelled) if state.cancelled in state.handles
                             else None),
        "verifier_calls": state.verifier_calls,
        "shown": {"formalizations": len(terminal.shown), "verdicts": len(terminal.verdicts),
                  "result": terminal.result is not None},
        "manifest": {
            "phase": manifest.phase.value,
            "terminal_reason": manifest.terminal_reason.value if manifest.terminal_reason else None,
            "formal": grades.formal.value,
            "faithfulness": grades.faithfulness.value,
            "document": grades.document.value,
            "informal": grades.informal.value,
            "known_gaps": list(grades.known_gaps),
            "reviewed": grades.faithfulness_review is not None,
            "verified_evidence": grades.verification_evidence is not None,
            "claim": manifest.claim_sha256 is not None,
            "timings_ms": manifest.timings_ms,
            # Replay records are named by a fresh uuid each run.
            "artifacts": sorted(re.sub(r"[0-9a-f]{32}", "<id>", name) for name in manifest.artifacts),
        },
    }


def load() -> dict:
    return json.loads(GOLDEN.read_text(encoding="utf-8"))


def test_every_scenario_is_pinned():
    assert sorted(load()) == sorted(_scenarios())


@pytest.mark.parametrize("name", sorted(_scenarios()))
def test_the_controller_does_what_the_golden_file_recorded(name, tmp_path):
    assert observe(name, tmp_path) == load()[name]


def regenerate() -> None:
    import tempfile

    golden = {}
    for name in sorted(_scenarios()):
        with tempfile.TemporaryDirectory() as scratch:
            golden[name] = observe(name, Path(scratch))
    GOLDEN.write_text(json.dumps(golden, indent=1) + "\n", encoding="utf-8")


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    regenerate()
    sys.exit(0)
