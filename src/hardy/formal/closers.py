"""Cheap Lean closers, tried before a model turn is spent.

`FEATURES.md` has wanted this since the beginning: try `simp`, `omega`,
`aesop`, `exact?` and their neighbours against the statement before paying a
provider for a turn. It could not be built while the loop belonged to a
provider's SDK, because the decision "do not call the model yet" has to be
made *in* the loop — which is issue #23 and why this module arrives with it.

The economics are the whole argument and are worth stating rather than
assuming. A closer costs one Lean elaboration, which against Mathlib is tens
of seconds; a model turn costs a request, its tokens, and the Lean call the
model will make anyway. So a ladder of a handful of tactics is cheap against
one turn and expensive against none — which is why it is off unless asked for,
and why what it tried is written into the trajectory whether it closed
anything or not. A run whose result came from a tactic ladder and a run whose
result came from a model are not the same experiment, and a scoreboard that
cannot tell them apart is worse than one without the feature.

Nothing here relaxes a check. A closer's proof goes through exactly the door a
model's would — `submit_proof`, the axiom audit behind it — so a tactic that
closes a goal by a route the audit refuses is refused in the same words.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

#: The ladder, cheapest first. `exact?` is last because it searches the whole
#: environment and is the one entry here that can cost more than a model turn
#: on a large goal; `decide` is in for the same reason it is in the automation
#: probe, and `omega` because linear arithmetic is where a hand-written proof
#: is most often a waste of a turn.
CLOSERS: tuple[str, ...] = ("rfl", "trivial", "simp", "omega", "decide", "aesop", "exact?")


@dataclass(frozen=True)
class Attempt:
    """One tactic tried, and what came of it."""

    tactic: str
    ok: bool
    output: str

    def as_dict(self) -> dict[str, Any]:
        return {"tactic": self.tactic, "ok": self.ok, "output": self.output}


@dataclass(frozen=True)
class Outcome:
    """What the ladder did, whether or not anything closed.

    `closed_by` is the tactic whose submission was accepted, and None when
    none was. The attempts are kept either way: "nothing closed it" is a
    measurement, and a trajectory that recorded only successes could not
    distinguish a ladder that ran and failed from one that never ran.
    """

    attempts: tuple[Attempt, ...] = ()
    closed_by: str | None = None

    @property
    def closed(self) -> bool:
        return self.closed_by is not None

    def as_dict(self) -> dict[str, Any]:
        return {
            "enabled": True,
            "tactics": [item.tactic for item in self.attempts],
            "attempts": [item.as_dict() for item in self.attempts],
            "closed_by": self.closed_by,
        }


#: What the trajectory says when nobody asked for the ladder. Explicit rather
#: than absent: a missing key reads as a harness that has no closers, and this
#: one has them and was told not to use them.
DISABLED: dict[str, Any] = {"enabled": False, "tactics": [], "attempts": [], "closed_by": None, "seconds": 0.0}


def close(
    submit: Callable[[str], tuple[bool, str]],
    tactics: Sequence[str] = CLOSERS,
    *,
    keep_going: Callable[[], bool] | None = None,
) -> Outcome:
    """Try each tactic through `submit`, stopping at the first that is accepted.

    `submit` takes a proof body and returns `(accepted, what Lean and the
    audit said)`. It is passed in rather than reached for so that the ladder
    goes through the caller's own submission path: the run's deadline, its
    recording, and its axiom audit all apply to a tactic's proof exactly as
    they apply to a model's, and this module never becomes a second door into
    a verdict.

    `keep_going` is asked before each attempt, so a caller whose budget has
    expired stops paying for elaborations it can no longer use.
    """
    attempts: list[Attempt] = []
    for tactic in tactics:
        if keep_going is not None and not keep_going():
            break
        ok, output = submit(f"by {tactic}")
        attempts.append(Attempt(tactic, ok, output))
        if ok:
            return Outcome(tuple(attempts), tactic)
    return Outcome(tuple(attempts), None)
