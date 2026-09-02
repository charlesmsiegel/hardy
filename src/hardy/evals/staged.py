"""Staged mode for the set runner: an approving user, and a reader of two Lean statements (spec §3.2)."""
from __future__ import annotations

import contextlib
import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import model_validator

from ..domain import FrozenClaim, FrozenModel, RunPhase, schema_text
from ..prompts import canonical_prompt, claim_signature
from .problems import Entry


class ApprovingTerminal:
    """A user who acknowledges, approves the first proposal that elaborates, and watches."""

    def __init__(self) -> None:
        self.proposals: list[Any] = []
        self.verdicts: list[Any] = []
        self.manifest: Any = None

    def acknowledge_unsafe_execution(self) -> bool:
        return True

    def show_formalization(self, proposal: Any, elaboration: Any) -> None:
        self.proposals.append((proposal, getattr(elaboration, "success", None)))

    def choose_approval(self) -> Literal["approve", "revise", "cancel"]:
        return "approve"

    def revision_text(self) -> str:
        return ""

    def show_faithfulness(self, verdict: Any) -> None:
        self.verdicts.append(verdict)

    def show_result(self, manifest: Any) -> None:
        self.manifest = manifest


class CanonicalReview(FrozenModel):
    equivalent: bool
    canonical_entails_model: bool
    model_entails_canonical: bool
    divergences: tuple[str, ...] = ()
    notes: str = ""

    @property
    def agrees(self) -> bool:
        return self.equivalent and self.canonical_entails_model and self.model_entails_canonical and not self.divergences and not self.notes.strip()


class CanonicalVerdict(FrozenModel):
    schema_version: Literal[1] = 1
    claim_sha256: str | None
    entry_id: str
    canonical_declaration: str
    model_signature: str | None
    reviewer_model: str
    reviewer_backend: str
    prompt_sha256: str | None
    response_schema_sha256: str | None
    outcome: Literal["agreed", "disputed", "unavailable"]
    review: CanonicalReview | None = None
    detail: str = ""
    usage: dict[str, Any]

    @model_validator(mode="after")
    def outcome_must_follow_the_review(self) -> CanonicalVerdict:
        """Refuse a verdict whose summary can disagree with its own evidence.

        Mirrors `FaithfulnessVerdict.outcome_must_follow_the_review`
        (`domain.py`): `_canonical_issues` loads this with
        `model_validate_json`, so a `canonical.json` rewritten to say
        `outcome: "agreed"` beside a disputed or absent review fails to parse
        at all, and the validator reports it as a finding rather than
        crediting a tampered outcome.
        """
        if self.outcome == "unavailable":
            if self.review is not None:
                raise ValueError("an unavailable verdict carries no review")
            return self
        if self.review is None:
            raise ValueError(f"a {self.outcome} verdict requires the review it grades")
        if self.review.agrees is not (self.outcome == "agreed"):
            raise ValueError("verdict does not follow from the review it names")
        # `unavailable` is the only outcome the no-formalization path
        # (`compare_canonical`, no `formalization.json`) can produce, and
        # that is the only place these four fields are ever left `None`. An
        # `agreed` or `disputed` verdict binds a specific review to a
        # specific claim, prompt and schema; leaving any of these `None`
        # would let a reader trajectory copied from comparing a *different*
        # formalization supply the agreeing review here, with nothing tying
        # it back to this row's frozen statement.
        missing = [name for name in ("claim_sha256", "model_signature", "prompt_sha256", "response_schema_sha256") if getattr(self, name) is None]
        if missing:
            raise ValueError(f"a {self.outcome} verdict requires " + ", ".join(missing))
        return self


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write(row_dir: Path, name: str, text: str) -> str:
    path = row_dir / name
    path.write_bytes(text.encode("utf-8"))
    return _sha(path)


class _Store:
    """The one thing `ClaudeStagedRuntime` actually calls on a store: `append`.

    `start`, `run_structured` and `cancel` (`hardy/staged.py:191-434`) never
    read `store.path`, `store.run_id`, or call `store.write_text` -- the only
    store method reached from any of them is `self._store.append(kind,
    payload, phase=...)`, used to record provider events into the trajectory.
    So that is the only method this shim provides; `RunStore`'s wider surface
    is not needed here and is not faked.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self.run_id = uuid4()

    def append(self, kind: str, payload: dict[str, Any], *, phase: RunPhase) -> None:
        with (self._path / "canonical-trajectory.jsonl").open("a", encoding="utf-8") as sink:
            sink.write(json.dumps({"kind": kind, "phase": phase.value, "payload": payload}, ensure_ascii=False) + "\n")


def compare_canonical(entry: Entry, run_dir: Path, row_dir: Path, *, runtime_factory: Callable[[Any], Any], model: str, wall_seconds: float) -> CanonicalVerdict:
    """Ask an independent reader whether the model's frozen statement is the canonical one.

    Written beside the nested run directory, never inside it, so the run's
    own manifest keeps describing exactly the files it hashed.
    """
    row_dir.mkdir(parents=True, exist_ok=True)
    claim_path = run_dir / "formalization.json"
    base: dict[str, Any] = dict(entry_id=entry.id, canonical_declaration=entry.declaration(), reviewer_model=model, usage={})
    if not claim_path.exists():
        verdict = CanonicalVerdict(claim_sha256=None, model_signature=None, reviewer_backend="unknown", prompt_sha256=None,
                                   response_schema_sha256=None, outcome="unavailable", detail="the run has no formalization.json to compare", **base)
        (row_dir / "canonical.json").write_text(json.dumps(verdict.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return verdict
    claim = FrozenClaim.model_validate_json(claim_path.read_text(encoding="utf-8"))
    signature = claim_signature(claim)
    prompt = canonical_prompt(entry.declaration(), signature)
    prompt_sha = _write(row_dir, "canonical-prompt.md", prompt)
    schema_sha = _write(row_dir, "canonical-schema.json", schema_text(CanonicalReview))
    runtime = runtime_factory(_Store(row_dir))
    identity = dict(claim_sha256=claim.content_hash, model_signature=signature, reviewer_backend=str(getattr(runtime, "backend", "unknown")),
                    prompt_sha256=prompt_sha, response_schema_sha256=schema_sha, **base)
    thread = None
    try:
        thread = runtime.start(model=model, run_dir=row_dir, claim=None, isolated=True, phase=RunPhase.AWAITING_APPROVAL, wall_seconds=wall_seconds)
        review = runtime.run_structured(thread, "canonical", prompt, CanonicalReview)
    except Exception as error:
        if thread is not None:
            cancel = getattr(runtime, "cancel", None)
            if cancel is not None:
                with contextlib.suppress(Exception):
                    cancel(thread)
        verdict = CanonicalVerdict(outcome="unavailable", detail=f"{type(error).__name__}: {error}", **identity)
    else:
        verdict = CanonicalVerdict(outcome="agreed" if review.agrees else "disputed", review=review, **identity)
    verdict = verdict.model_copy(update={"usage": dict(getattr(runtime, "usage", {}) or {})})
    (row_dir / "canonical.json").write_text(json.dumps(verdict.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return verdict


def staged_runner(config: Any, *, backend: str) -> Callable[[Entry, Path, str], None]:
    """(entry, row_dir, model): run `hardy prove` non-interactively under `row_dir`, then compare canonically."""
    import dataclasses

    from ..cli import build_prove_workflow
    from ..staged import ClaudeStagedRuntime
    from ..workflow import ProveRequest

    def run_one(entry: Entry, row_dir: Path, model: str) -> None:
        scoped = dataclasses.replace(config, runs_root=row_dir)
        workflow = build_prove_workflow(scoped, scoped.config_path, backend=backend)
        workflow.run(ProveRequest(text=entry.input, model=model, problem_slug=entry.id), ApprovingTerminal())
        runs = [p for p in row_dir.iterdir() if p.is_dir() and (p / "manifest.json").exists()]
        if len(runs) != 1:
            return
        reader_model = config.faithfulness_model or model
        compare_canonical(entry, runs[0], row_dir,
                          runtime_factory=lambda store: ClaudeStagedRuntime(store=store, lean_runtime_factory=lambda claim: None),
                          model=reader_model, wall_seconds=float(config.limits.lean_process_seconds))

    return run_one
