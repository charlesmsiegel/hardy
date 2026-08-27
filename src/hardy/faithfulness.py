"""The statement faithfulness gate: an independent read of one translation.

Kernel acceptance says a Lean statement was proved. It says nothing about
whether that statement is the claim the user made, and a proof of the wrong
theorem is the most expensive failure this harness can produce -- expensive
precisely because every other signal reads green. The only defence is a second
reader of the translation, so this runs one before any proof search begins.

Independence here means independence of *context*, not merely of weights. The
reader is started on its own thread, with no Lean tools, and is handed the
user's words and the frozen Lean signature and nothing else: not the
formalization conversation, not the proposal's own restatement or
interpretation choices. A reader given the account that produced a translation
is reading the translation through that account, which is the shared-fate bias
that makes most self-checks theatrical. `Config.faithfulness_model` can point
the read at a different model as well, when independent weights are wanted too.

The gate is fail-closed and asymmetric on purpose. A pass can be wrong -- the
reader may have missed the divergence -- but a halt is never wasted, because
surfacing a real mismatch for a human costs one question and proving the wrong
theorem costs the whole run. So a disputed translation stops the run, and so
does a reader that could not be reached or answered with something that is not
a review: neither is a pass, and there is no third option that proceeds.
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import Callable
from pathlib import PurePosixPath
from typing import Any

from .domain import (
    FaithfulnessOutcome,
    FaithfulnessReview,
    FaithfulnessVerdict,
    FrozenClaim,
    RunPhase,
)
from .prompts import faithfulness_prompt
from .storage import RunStore

ARTIFACT = PurePosixPath("faithfulness.json")
PROMPT_ARTIFACT = PurePosixPath("faithfulness-prompt.md")
SCHEMA_ARTIFACT = PurePosixPath("faithfulness-schema.json")


def review_translation(
    claim: FrozenClaim,
    *,
    runtime: Any,
    model: str,
    store: RunStore,
    phase: RunPhase,
    wall_seconds: float | None = None,
    on_thread: Callable[[Any], None] | None = None,
) -> FaithfulnessVerdict:
    """Ask an independent reader whether the frozen Lean says what the user said.

    Reads the frozen claim rather than the proposal it came from, so what is
    checked is byte-identical to what will be proved, and the verdict can name
    the claim hash a later reader can match it against.

    `on_thread` is handed the reader's thread the moment it exists, so the
    caller's cancellation path has a handle to it. Without that, a Ctrl+C
    during the read would reach the formalizing thread while the reader's
    provider thread stayed live -- still able to append to the trajectory
    after the manifest that hashes it has been written.
    """
    prompt = faithfulness_prompt(claim)
    # The question as asked, kept beside the answer. `prompt_sha256` would
    # otherwise be self-asserted -- a hash of something no longer in the run
    # directory, which the release audit could not recompute and a reader
    # could not check. Written first, so the record of what was asked survives
    # a reader that never answers.
    #
    # Byte-for-byte what is sent below, with no trailing newline added. A file
    # tidied with one hashes differently from the string the reader received,
    # which would leave `prompt_sha256` reproducible and yet not the identity
    # of the question -- the one thing it exists to be.
    asked = store.write_text(PROMPT_ARTIFACT, prompt)
    schema = store.write_text(SCHEMA_ARTIFACT, _schema_source())
    identity = {
        "claim_sha256": claim.content_hash,
        "reviewer_model": model,
        "reviewer_backend": str(getattr(runtime, "backend", "unknown")),
        # Asked of the runtime rather than assumed of the gate. A backend that
        # cannot confine its reader reports nothing, and the verdict says so.
        "reviewer_isolation": getattr(runtime, "isolation_guarantee", None),
        "prompt_sha256": asked.sha256,
        # The other half of what was asked. Every backend makes the reader
        # answer this schema -- Claude by appending it to the prompt, Codex by
        # handing it to the SDK -- and it is generated from
        # `FaithfulnessReview` rather than written in a template, so
        # `prompt_set_sha256` does not cover it. Without this, editing that
        # model would change the request and could change the answer while
        # every recorded hash stayed the same.
        #
        # Kept as a file for the same reason the prompt is: a digest the
        # release audit can only recompute from today's code would flag every
        # run made under an earlier schema, while one taken over an artifact in
        # the run directory is checkable against what that run actually used.
        "response_schema_sha256": schema.sha256,
    }
    # A thread of its own, isolated: no tools, and for the backends whose
    # agent has its own file access, no sight of the run directory either.
    # This is a reading of two texts, and a reader that can reach the
    # conversation it is auditing is not an independent one.
    thread: Any = None
    try:
        thread = runtime.start(
            model=model,
            run_dir=store.path,
            claim=None,
            isolated=True,
            phase=phase,
            # This read is one call with no loop around it, so a provider that
            # accepts the connection and then stalls would block here forever
            # -- and the verdict below, which exists to make the gate
            # fail-closed, would never be written at all. A deadline is what
            # turns "the reader never answered" into an answer.
            wall_seconds=wall_seconds,
        )
        if on_thread is not None:
            on_thread(thread)
        review = runtime.run_structured(thread, "faithfulness", prompt, FaithfulnessReview)
    # Every way a provider can fail, not the two the structured-output path
    # raises. A transport error -- `ConnectionError`, `TimeoutError`, an
    # `OSError` from a dead pipe -- used to reach the workflow's generic
    # handler, which graded the run `agent_runtime_failure` with no
    # `faithfulness.json` and no faithfulness gap: fail-closed, since nothing
    # proceeded to proving, but a record that did not say an approved claim
    # had been left unread. `KeyboardInterrupt` is not an `Exception` and
    # still propagates, which is what keeps cancellation cancelling.
    except Exception as error:
        # Stop the thread before the verdict is returned. An unavailable
        # verdict always halts the run, and `ProveWorkflow._finalize` hashes
        # every file in the run directory as soon as it does -- but only the
        # cancellation path settles a provider worker that outlived its turn
        # and seals the trajectory against it. Without this, a lingering
        # daemon could append after `trajectory.jsonl` was hashed, leaving a
        # manifest that does not describe the directory it names.
        if thread is not None:
            _stop(runtime, thread)
        verdict = FaithfulnessVerdict(
            **identity,
            outcome=FaithfulnessOutcome.UNAVAILABLE,
            detail=f"{type(error).__name__}: {error}",
        )
    else:
        verdict = FaithfulnessVerdict(
            **identity,
            outcome=(
                FaithfulnessOutcome.AGREED if review.agrees else FaithfulnessOutcome.DISPUTED
            ),
            review=review,
        )
    # Written before it is acted on, and written whichever way it went: a
    # verdict that only survives when it agrees is a record of nothing.
    store.write_json(ARTIFACT, verdict)
    store.append("faithfulness.verdict", verdict.model_dump(mode="json"), phase=phase)
    return verdict


def _schema_source() -> str:
    """The response schema the reader has to satisfy, canonically rendered."""
    return json.dumps(
        FaithfulnessReview.model_json_schema(),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _stop(runtime: Any, thread: Any) -> None:
    """Cancel a reader whose turn failed, without letting that failure spread.

    Best-effort on purpose: the verdict is already decided by the time this
    runs, and a runtime that cannot be cancelled must not turn a recorded
    unavailable review into an unrecorded crash.
    """
    cancel = getattr(runtime, "cancel", None)
    if cancel is None:
        return
    with contextlib.suppress(Exception):
        cancel(thread)


def dispute_gaps(verdict: FaithfulnessVerdict) -> tuple[str, ...]:
    """What a halted run's known gaps say, in the words the reader used."""
    if verdict.outcome is FaithfulnessOutcome.UNAVAILABLE:
        return (
            "The independent faithfulness review could not be obtained: " + verdict.detail,
        )
    divergences = verdict.review.divergences if verdict.review else ()
    if not divergences:
        # A reader that answered "no" to an entailment without naming a
        # difference still refused the translation, and the run still halts.
        return ("The independent faithfulness review did not accept the translation.",)
    return tuple(
        "The independent faithfulness review disputed the translation: " + text
        for text in divergences
    )
