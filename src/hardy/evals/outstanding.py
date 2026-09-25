"""What is left to do, under the pooling key this checkout would produce.

Read-only and free: a control agent asks this before deciding what to spend on,
and `evals run`/`evals baseline` ask it to fill in a selection nobody named.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from hardy.evals import sweep
from hardy.formal.contracts import EnvironmentIdentity


def environment_digest_of_board(board: dict[str, Any]) -> str:
    """A board's own environment digest, computed the way a baseline's is.

    Over its recorded `environment` and `host` (`sweep.environment_digest_of`)
    -- a board stores those two components, not a precomputed digest -- so
    this is the single place that turns them into the same digest `evals run`
    would produce today. `pool.py` imports this rather than defining a second
    copy, so the two can never silently disagree on what "the same
    environment" means.
    """
    environment = EnvironmentIdentity.model_validate(board.get("environment") or {})
    host = board.get("host") or {}
    return sweep.environment_digest_of(environment, host)


#: Marks a finding only the board-level exposure check made. `evals pool`
#: runs `scoreboard_self_issues` and not that check, so a board refused for
#: this alone is one pool would accept; `evals todo` still counts none of it,
#: since its exposure record does not authenticate.
BOARD_EXPOSURE = "board-level exposure check: "


def matching_boards(scoreboards_root: Path, *, key: tuple[str | None, str]) -> list[str]:
    """The label of every board under `scoreboards_root` whose condition and
    recorded environment together equal `key`, sorted.

    The cheap half of what `evals pool` asks of a board, and nothing more:
    the pooling key. `poolable_boards` adds the other half, the board's own
    audit, before any row is claimed as evidence or any board is counted. A
    board is not filtered here for anything that audit judges -- its exposure
    artifacts included -- because a board dropped here never reaches
    `boards_refused`, and its entries were selected again with nothing said.

    A board carrying no `run_procedure_digest` matches nothing: it was
    written before this gate existed, so nothing establishes which code
    produced it, and treating a blank as agreement would make the gate
    decorative -- the same rule `staleness` already applies to a blank
    environment digest. An unreadable or malformed board is not evidence of
    anything either, and is skipped rather than raised.
    """
    if not scoreboards_root.exists():
        return []
    matched: list[str] = []
    for board_path in sorted(scoreboards_root.glob("*/scoreboard.json")):
        try:
            board = json.loads(board_path.read_text(encoding="utf-8"))
            condition = board.get("condition") or {}
            run_digest = condition.get("run_procedure_digest")
            if run_digest is None:
                continue
            if (run_digest, environment_digest_of_board(board)) != key:
                continue
        except (OSError, ValueError, KeyError, TypeError):
            continue
        matched.append(board_path.parent.name)
    return sorted(matched)


def poolable_boards(scoreboards_root: Path, *, key: tuple[str | None, str], problems_path: Path,
                    baseline_path: Path) -> tuple[list[str], dict[str, list[str]]]:
    """The boards `evals pool` would admit under `key`, and those it would refuse.

    Returns the admitted labels, sorted, and each refused label with the
    findings its own audit (`scoreboard_self_issues`, the check `pool.pool`
    applies) reported. A board that fails that audit can never be pooled, so
    none of its rows is evidence that an entry was run, and `evals todo`
    must not count it: that would drop the entry from every default run
    while nothing poolable exists for it.

    The audit re-derives every row from its run directory and reads the
    corpus and tier file, so it costs more than the key match; it runs only
    on the boards that already match. It reads each row's exposure journal
    too; the board-level exposure check (`board_exposure_issues`) is asked as
    well, and whatever either finds refuses the board with the finding.
    """
    from hardy.evals.exposure import board_exposure_issues
    from hardy.evals.scoreboard import scoreboard_self_issues

    admitted: list[str] = []
    refused: dict[str, list[str]] = {}
    for label in matching_boards(scoreboards_root, key=key):
        directory = scoreboards_root / label
        try:
            issues = list(scoreboard_self_issues(directory, problems_path=problems_path,
                                                 baseline_path=baseline_path))
            if not issues:
                board = json.loads((directory / "scoreboard.json").read_text(encoding="utf-8"))
                issues = [BOARD_EXPOSURE + issue for issue in board_exposure_issues(directory, board)]
        except (OSError, ValueError, KeyError, TypeError) as error:
            issues = [f"the board's own audit could not run: {type(error).__name__}: {error}"]
        if issues:
            refused[label] = issues
        else:
            admitted.append(label)
    return admitted, refused


def board_slots(scoreboards_root: Path, label: str) -> set[tuple[str, int]]:
    """Every `(id, repeat)` a board's rows claim, `invalid` ones included:
    `evals pool` refuses a slot two boards claim whatever its outcome."""
    board = json.loads((scoreboards_root / label / "scoreboard.json").read_text(encoding="utf-8"))
    return {(str(row.get("id")), int(row.get("repeat") or 0)) for row in board.get("rows") or []}


def conflicting_boards(scoreboards_root: Path, *, boards: list[str]
                       ) -> tuple[list[str], dict[str, list[str]], set[str], dict[str, list[str]]]:
    """Split admitted `boards` into those `evals pool` accepts together and those it cannot.

    `evals pool` refuses a set of boards in which two claim the same
    `(id, repeat)` slot, so each board passing its own audit is not enough:
    with one repeat, A holding x and y and B holding y and z are refused
    together, and a union of their rows counted x, y and z complete. Every
    board that shares a slot with another is left out -- which of them a
    human keeps is theirs to decide, by setting the others aside -- and the
    rest share no slot pairwise, so `evals pool` accepts them together.

    Returns the boards still counted, sorted; each conflicting board with
    the slots it shares and the boards it shares them with; the entry ids
    any conflicting board holds a slot of; and each board that could not be
    read again here, with why. That last is refused, not conflicting: it
    conflicts with nothing anyone can name, and is never counted.
    """
    slots: dict[str, set[tuple[str, int]]] = {}
    unreadable: dict[str, list[str]] = {}
    for label in boards:
        try:
            slots[label] = board_slots(scoreboards_root, label)
        except (OSError, ValueError, TypeError, AttributeError) as error:
            unreadable[label] = [f"the board could not be read again: {type(error).__name__}: {error}"]
    claimed: dict[tuple[str, int], list[str]] = {}
    for label in sorted(slots):
        for slot in slots[label]:
            claimed.setdefault(slot, []).append(label)
    conflicts: dict[str, list[str]] = {}
    for (id_, repeat), holders in sorted(claimed.items()):
        if len(holders) < 2:
            continue
        for label in holders:
            others = ", ".join(other for other in holders if other != label)
            conflicts.setdefault(label, []).append(
                f"{id_} repeat {repeat} is also claimed by {others}; `evals pool` refuses them together"
            )
    held = {id_ for label in conflicts for id_, _ in slots.get(label, ())}
    counted = sorted(label for label in boards if label not in conflicts and label not in unreadable)
    return counted, conflicts, held, unreadable


def evaluated_ids(scoreboards_root: Path, *, boards: list[str]) -> tuple[set[str], set[str]]:
    """The entry ids `boards` fully cover, and those they cover only in part.

    `boards` are labels `poolable_boards` admitted and `conflicting_boards`
    kept, so `evals pool` accepts them together. An id is complete when
    its rows across them, leaving out `invalid` ones, fill every repeat slot
    `range(condition.repeats)`; `repeats` is in the pooling key, so every
    admitted board shares it. An id with some slots but not all is partial:
    an interrupted board holds `(X, 0)` of three, say. Counting it done would
    pool one sample beside other entries' three, the unbalanced design the
    key keeps `repeats` in to prevent.
    """
    slots: dict[str, set[int]] = {}
    repeats = 1
    for label in boards:
        try:
            board = json.loads((scoreboards_root / label / "scoreboard.json").read_text(encoding="utf-8"))
            repeats = max(repeats, int((board.get("condition") or {}).get("repeats") or 1))
            for row in board.get("rows") or []:
                if row.get("outcome") == "invalid":
                    continue   # recorded, but not a sample of anything
                slots.setdefault(str(row.get("id")), set()).add(int(row.get("repeat") or 0))
        except (OSError, ValueError, TypeError, AttributeError):
            continue      # an unreadable board is not evidence of anything
    complete = {id_ for id_, have in slots.items() if set(range(repeats)) <= have}
    return complete, set(slots) - complete


def unbaselined_active(problems: Any, baseline: Any | None) -> list[str]:
    """Active entries with no usable baseline row -- what `evals baseline`'s default resweeps.

    Usable is `sweep.row_carries`: a row measured against another statement
    (or recording no statement digest), or a twin's row with no negation
    sweep, is one `staleness` refuses and the sweep would not reuse, so it
    counts as unbaselined here too. Otherwise the repair `staleness` names --
    re-running `hardy evals baseline` -- would find nothing to do.

    Needs no run digest: a baseline sweep is Lean-only, gated by the corpus
    and the toolchain, not by which model, mode or limits a run would use.
    `baseline=None` (no tier file written yet) means every active entry is
    unbaselined.
    """
    return [
        e.id for e in problems.entries
        if e.status == "active" and (baseline is None or not sweep.row_carries(baseline, e, e.statement_digest()))
    ]


def moved_identity(prior: Any | None, *, environment_digest: str, procedure_digest: str) -> str | None:
    """Why no row of `prior` can be carried into a sweep made today, or
    `None` when rows can be (or there is no prior file at all)."""
    if prior is None or sweep.reusable(prior, environment_digest=environment_digest, procedure_digest=procedure_digest):
        return None
    reasons = []
    if prior.environment_digest != environment_digest:
        reasons.append("its environment digest is not this toolchain and machine's")
    if prior.procedure_digest != procedure_digest:
        reasons.append("its procedure digest is not this build's")
    if not prior.statement_digests:
        reasons.append("it records no statement digests")
    return "; ".join(reasons)


def baseline_default(problems: Any, prior: Any | None, *, environment_digest: str,
                     procedure_digest: str) -> tuple[list[str], str | None]:
    """What a bare `hardy evals baseline` sweeps now, in corpus order, and why
    no prior row can be carried when that is the case.

    - Under a prior file whose environment and procedure still match: the
      active entries with no row the sweep would carry
      (`unbaselined_active`), plus any entry, active or not, whose row has
      an attempt that never ran. `staleness` refuses such a row file-wide,
      so the default must be able to clear it whatever the entry's status.
    - Under a prior file swept under another environment or procedure: every
      entry the file holds a row for, plus the active entries it lacks. No
      prior row may be carried or restamped, so they are all measured again.

    `evals baseline` and `evals todo` both call this, so what `todo` reports
    is what the command would do.
    """
    moved = moved_identity(prior, environment_digest=environment_digest, procedure_digest=procedure_digest)
    wanted = set(unbaselined_active(problems, prior))
    held = prior.entries if prior is not None else {}
    for entry in problems.entries:
        row = held.get(entry.id)
        if row is not None and (moved is not None or sweep.never_ran(row)):
            wanted.add(entry.id)
    return [e.id for e in problems.entries if e.id in wanted], moved


def outstanding(problems: Any, baseline: Any, scoreboards_root: Path, *, key: tuple[str | None, str],
                problems_path: Path, baseline_path: Path, procedure_digest: str) -> dict[str, Any]:
    """What is left under `key`, judged by the boards `evals pool` would admit.

    - `boards_counted`: the boards admitted as evidence, a set `evals pool`
      accepts together; `boards_refused`: those matching `key` that fail
      their own audit, with its findings; `boards_conflicting`: those that
      pass it but claim an `(id, repeat)` slot another admitted board claims
      too, with the slots and the other boards. `evals pool` refuses such a
      pair together, so neither is counted until one is set aside.
    - `baseline_sweeps`: what a bare `evals baseline` would sweep now
      (`baseline_default`, under `key`'s environment digest and the sweep's
      `procedure_digest`), and `baseline_moved`: why no prior row can be
      carried, or `None`. `unbaselined_active` is its active part.
    - `unevaluated_active`: active entries with no valid sample on an
      admitted board -- what the default `evals run` selects.
    - `partially_evaluated_active`: active entries holding some of their
      repeats but not all. The default run leaves them out, because a fresh
      board repeating their slots would not pool with the one holding them.
    - `conflicted_active`: active entries a conflicting board holds a slot
      of, and no counted board completes. Left out of the default run for
      the same reason: a rerun would claim a slot a conflicting board still
      holds. Setting boards aside is the remedy, not a rerun.

    Only `active` entries: a `candidate` has not been checked by a human yet,
    and spending model time on one would benchmark a draft.
    """
    admitted, refused = poolable_boards(scoreboards_root, key=key, problems_path=problems_path,
                                        baseline_path=baseline_path)
    counted, conflicting, held, unreadable = conflicting_boards(scoreboards_root, boards=admitted)
    refused = {**refused, **unreadable}
    complete, partial = evaluated_ids(scoreboards_root, boards=counted)
    blocked = held - complete
    active = [e.id for e in problems.entries if e.status == "active"]
    sweeps, moved = baseline_default(problems, baseline, environment_digest=key[1], procedure_digest=procedure_digest)
    return {
        "boards_counted": counted,
        "boards_refused": refused,
        "boards_conflicting": conflicting,
        "baseline_sweeps": sweeps,
        "baseline_moved": moved,
        "unbaselined_active": [id_ for id_ in sweeps if id_ in set(active)],
        "unevaluated_active": [id_ for id_ in active
                               if id_ not in complete and id_ not in partial and id_ not in blocked],
        "partially_evaluated_active": [id_ for id_ in active if id_ in partial and id_ not in blocked],
        "conflicted_active": [id_ for id_ in active if id_ in blocked],
    }
