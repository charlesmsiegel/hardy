"""Selection policy shared by experiment execution and recorded validation."""
from __future__ import annotations

from ..corpus.problems import Entry, ProblemSet
from .contracts import RefusedRun
from .sweep import Baseline


def select(problems: ProblemSet, baseline: Baseline, *, only: list[str] | None, tiers: list[int] | None, twins: bool) -> tuple[Entry, ...]:
    # `only`'s own order, not the set's: a caller who names entries explicitly
    # is choosing a run order, not just a subset (spec §3.2 "select"). Repeats
    # are folded to their first occurrence -- `--only t,t` must not run `t`
    # twice into the same row directory -- and a name the list does not carry
    # is a gate this refuses before anything runs, not a selection silently
    # narrowed by ignoring it.
    if only is None:
        ids = [entry.id for entry in problems.entries]
    else:
        seen: set[str] = set()
        ids = []
        for id_ in only:
            if id_ not in seen:
                seen.add(id_)
                ids.append(id_)
        known = {entry.id for entry in problems.entries}
        unknown = [id_ for id_ in ids if id_ not in known]
        if unknown:
            raise RefusedRun("--only names entries not in the list: " + ", ".join(unknown))
    if tiers is not None:
        unbaselined = [id_ for id_ in ids if id_ not in baseline.entries]
        if unbaselined:
            # A KeyError here read as a crash; it is a selection the baseline
            # cannot tier, which is a refusal naming what to sweep.
            raise RefusedRun(
                "--tiers needs a baseline row for: " + ", ".join(sorted(unbaselined))
                + "; re-run `hardy evals baseline` for them"
            )
    chosen = []
    for id_ in ids:
        entry = problems.by_id(id_)
        if tiers is not None and baseline.entries[entry.id].tier not in tiers:
            continue
        if entry.expected == "false" and not twins:
            continue
        chosen.append(entry)
    return tuple(chosen)

