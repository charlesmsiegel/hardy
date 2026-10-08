"""Read-only consistency checks for `hardy batch` output directories.

The four artifacts were written by one run, so agreeing with each other
proves less than it looks. What these refuse is the record that could not
have happened as described: a verdict with no proof behind it, a closer
ladder or a sketch that no event produced, a refusal that was not one.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from hardy.agents.spend_budget import budget_record_issues
from hardy.formal import audit
from hardy.formal.lean import DECLARATION_HEAD, LeanTools, scannable
from hardy.formal.syntax import declared_name
from hardy.formal.verifier import (
    ALLOWED_AXIOMS,
    FORBIDDEN_TOKEN,
    axiom_report_line,
    proof_body_violation,
)
from hardy.workflows.recorded.common import _exact_source, _toolchain_issues, _usage_issues

# Batch terminal reasons that describe a run which produced no proof, honestly.
BATCH_FAILURES = frozenset({"no_proof_submitted", "axioms_rejected", "turn_limit", "wall_clock_limit", "runtime_error", "provider_budget_limit"})

# How a batch run on a false statement may end (acceptance run 3). Not a
# budget: a run that ran out of turns or time shows Hardy stopped waiting,
# which is a different fact from the model giving up or the gate refusing.
REFUSALS = frozenset({"no_proof_submitted", "axioms_rejected"})


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def refusal_issues(output_dir: Path) -> tuple[str, ...]:
    """Every way a batch run on a false statement failed to refuse it.

    Empty means refused. A `verified`, an accepted `submit_proof`, or a
    `check_proof` Lean accepted without a hole are each a way the harness
    would have graded a false claim -- and a partial grade is no better. A
    run that cannot be read is not a refusal either, so a missing or
    malformed artifact is reported rather than raised.
    """
    result_path = output_dir / "result.json"
    trajectory_path = output_dir / "trajectory.json"
    issues: list[str] = []
    for path in (result_path, trajectory_path):
        if not path.exists():
            issues.append(f"{path.name} is missing")
    if issues:
        return tuple(issues)
    documents: dict[Path, Any] = {}
    for path in (result_path, trajectory_path):
        try:
            documents[path] = _read_json(path)
        except ValueError:
            issues.append(f"{path.name} is not valid JSON")
    if issues:
        return tuple(issues)
    for path in (result_path, trajectory_path):
        if not isinstance(documents[path], dict):
            issues.append(f"{path.name} is not a JSON object")
    if issues:
        return tuple(issues)
    result, trajectory = documents[result_path], documents[trajectory_path]
    reason = result.get("terminal_reason")
    if reason not in REFUSALS:
        issues.append(f"terminal reason {reason!r} is not a refusal ({', '.join(sorted(REFUSALS))})")
    for event in trajectory.get("events", []):
        if event.get("type") != "tool":
            continue
        name = event.get("name")
        if name not in ("submit_proof", "check_proof", "sketch_proof"):
            continue
        result = event.get("result")
        if not isinstance(result, dict) or not isinstance(result.get("ok"), bool):
            # A missing/non-dict result, or a result whose `ok` is not a
            # bool, used to be silently skipped -- so a run 3 audit here
            # (`validate_batch_consistency`) never required these two tool
            # events to have a readable shape, and a run whose trajectory
            # cannot say whether Lean accepted a proof attempt could still be
            # graded `refused` (item 7).
            issues.append(f"a {name} event carries no readable result")
            continue
        ok = result["ok"]
        if name == "submit_proof" and ok:
            issues.append("a submit_proof was accepted")
        if name in ("check_proof", "sketch_proof") and ok and not LeanTools.has_holes(str(result.get("source", ""))):
            # A sketch is exempt for exactly as long as it has a hole in it.
            # `sketch_proof` forgives the hole and nothing else, so a hole-free
            # skeleton Lean accepted is an elaborated proof of the claim
            # whatever tool asked for it -- and on a false statement that is
            # the finding this audit exists to make, not a lenient case.
            issues.append(f"a {name} Lean accepted carried no hole")
    return tuple(issues)


def _closer_issues(trajectory: dict[str, Any], events: list[dict[str, Any]], reason: Any) -> list[str]:
    """Re-derive the closer ladder from the events, and refuse a block that differs.

    The `closers` block exists to say which experimental condition a run was:
    a result a tactic ladder reached and a result a model reached are not the
    same thing, and a scoreboard reads that field. A field nothing cross-checks
    is a field a record can simply assert, so it is checked against the event
    the runner wrote when the ladder actually ran.

    Absent means a record from before the field existed. Those are kept runs
    from paid experiments; the cross-check that cannot be made on them is
    skipped rather than faked, and every run that carries the block is checked
    in full.
    """
    ladder = trajectory.get("closers")
    if ladder is None:
        return []
    if not isinstance(ladder, dict):
        return ["trajectory closers is not an object"]
    issues: list[str] = []
    recorded = [event for event in events if event.get("type") == "closers"]
    attempts = ladder.get("attempts")
    if not isinstance(attempts, list):
        return ["trajectory closers states no attempts list"]
    enabled, closed_by = ladder.get("enabled"), ladder.get("closed_by")
    # The ladder's own declines, not every decline. A run whose submission was
    # accepted mid-exchange declines the next provider turn too, and counting
    # that as a closer event made this refuse an otherwise verified record --
    # either as a decline on a run with closers disabled, or as one for a
    # ladder that closed nothing. Absent `stage` reads as `closers`, which is
    # what every decline in a record from before the gate existed was.
    declined = [
        event for event in events
        if event.get("type") == "declined_turn" and event.get("stage", "closers") == "closers"
    ]
    if enabled is False:
        # Nothing ran, so nothing may be recorded as having run.
        if recorded:
            issues.append("closers are recorded as disabled beside a closers event")
        if attempts or ladder.get("tactics") or closed_by is not None:
            issues.append("closers are recorded as disabled beside a ladder that ran")
        # And nothing may be recorded as having been declined for it. Returning
        # here without this let a closer-produced record be relabelled as the
        # no-closer condition by blanking the block and deleting one event,
        # while the decline it could not have made stayed behind.
        if declined:
            issues.append("a turn was declined on a run whose closers are recorded as disabled")
        return issues
    if enabled is not True:
        return [f"trajectory closers state an unreadable enabled flag: {enabled!r}"]
    if len(recorded) != 1:
        issues.append(f"an enabled ladder recorded {len(recorded)} closers events, not one")
    elif {key: recorded[0].get(key) for key in ladder} != ladder:
        issues.append("the closers block differs from the event the runner recorded")
    tried = [item.get("tactic") for item in attempts if isinstance(item, dict)]
    if len(tried) != len(attempts):
        issues.append("a closer attempt is not an object with a tactic")
    elif ladder.get("tactics") != tried:
        issues.append("the closers block lists tactics its attempts do not account for")
    else:
        # Every attempt against the submission it made, in order, not just the
        # one that closed it. Compared against the block and its duplicated
        # event alone, the tactic names and their failure output could all be
        # rewritten together while the proofs the run actually submitted stayed
        # where they were -- certifying a different experimental condition from
        # the one that ran.
        issues.extend(_attempt_issues(attempts, events))
    # The ladder's shape, which `closers.close` fixes: it returns on the first
    # submission the run keeps, so exactly one attempt succeeds and it is the
    # last. Any other arrangement -- a success in the middle, two successes --
    # is a record no run could have produced, and without this a hand-edited or
    # merged trajectory could certify a ladder order and a cost that never
    # happened.
    kept = [index for index, item in enumerate(attempts) if isinstance(item, dict) and item.get("ok") is True]
    if len(kept) > 1:
        issues.append("the closers block records more than one attempt the run kept")
    elif kept and kept[0] != len(attempts) - 1:
        issues.append("the closers block records a kept attempt the ladder went on past")
    if closed_by is not None:
        if not any(item.get("tactic") == closed_by and item.get("ok") is True for item in attempts if isinstance(item, dict)):
            issues.append(f"closers claim `{closed_by}` closed the statement with no attempt saying so")
        # And the proof went in by the ordinary door and was *accepted* there.
        # Matching the text alone let a refused submission stand behind a
        # `closed_by`: a duplicated block claiming the attempt succeeded, plus
        # the decline the check below wants, and a run where nothing was ever
        # accepted certified a closer.
        submitted = [
            event for event in events
            if event.get("type") == "tool"
            and event.get("name") == "submit_proof"
            and _proof_argument(event) == f"by {closed_by}"
        ]
        accepted = [
            event for index, event in enumerate(events)
            if event in submitted
            and isinstance(event.get("result"), dict)
            and event["result"].get("ok") is True
            and not _discarded(events, index)
        ]
        if not submitted:
            issues.append(f"closers claim `{closed_by}` closed the statement with no matching submit_proof")
        elif not accepted:
            issues.append(f"closers claim `{closed_by}` closed the statement with no submission that was accepted and kept")
        elif reason != "verified":
            # A closer that closed it produced a verified run, by definition:
            # the submission it made went through the audit like any other.
            issues.append(f"closers claim `{closed_by}` closed the statement but the run is {reason!r}")
    elif any(item.get("ok") is True for item in attempts if isinstance(item, dict)):
        issues.append("a closer attempt was accepted but the block names no tactic that closed it")
    # The claim that no model was needed is the one the field exists to make,
    # so it is the one worth checking against the rest of the record -- and in
    # both directions. Asking only "if a turn was declined, does the rest agree"
    # let the decline itself be deleted, which took the provider-exchange check
    # down with it.
    exchanges = [event for event in events if event.get("type") == "result"]
    if closed_by is not None:
        # `closed_by` is set only for a submission the run kept, which is
        # exactly the case where no model turn is spent.
        if len(declined) != 1:
            issues.append(f"a ladder that closed the statement records {len(declined)} declined turns, not one")
        if exchanges:
            issues.append("a run the ladder closed records a provider exchange")
    elif declined:
        issues.append("a turn was declined for a ladder that closed nothing")
    return issues


def _attempt_issues(attempts: list[Any], events: list[dict[str, Any]]) -> list[str]:
    """Each recorded closer attempt against the `submit_proof` it produced.

    The ladder submits `by <tactic>` for each tactic in turn and stops at the
    first the run keeps, so the attempts are the submissions the runner made
    before it wrote the `closers` event -- matched by position, because that is
    the only thing an editor cannot rewrite without also rewriting what Lean
    was asked.

    Counted in both directions. Checking each claimed attempt against the
    submission behind it leaves the converse open: trailing attempts could be
    deleted from the block and from its duplicated event together, and the
    submissions they made would sit in the trajectory unaccounted for -- a run
    that tried seven tactics recertified as the cheaper three-tactic condition,
    with the four elaborations it actually paid for still on the record. So the
    ladder's share of the submissions is bounded by the `closers` event the
    runner wrote after it, and a surplus is refused by count.
    """
    boundary = next(
        (index for index, event in enumerate(events) if event.get("type") == "closers"),
        len(events),
    )
    submissions = [
        (index, event) for index, event in enumerate(events[:boundary])
        if event.get("type") == "tool" and event.get("name") == "submit_proof"
    ]
    issues: list[str] = []
    if len(submissions) > len(attempts):
        issues.append(
            f"{len(submissions)} proofs were submitted before the closers event, "
            f"which records {len(attempts)} attempts"
        )
    for index, attempt in enumerate(attempts):
        if index >= len(submissions):
            issues.append(f"closer attempt {index + 1} has no submit_proof behind it")
            continue
        position, event = submissions[index]
        expected = f"by {attempt.get('tactic')}"
        if _proof_argument(event) != expected:
            issues.append(f"closer attempt {index + 1} does not match the proof submitted for it")
        result = event.get("result")
        # Against whether the run *kept* the submission, not against Lean's raw
        # answer. `runner.submit` reports a tactic as having failed when the
        # check began inside the deadline and finished outside it -- the proof
        # is not kept -- while the event beside it still carries Lean's
        # `ok: true` behind the runner's discard marker. Compared to the raw
        # flag, that honest `wall_clock_limit` artifact was refused as
        # inconsistent with itself.
        if not isinstance(result, dict):
            issues.append(f"closer attempt {index + 1} states no result for its submission")
            continue
        kept = result.get("ok") is True and not _discarded(events, position)
        if kept is not attempt.get("ok"):
            issues.append(f"closer attempt {index + 1} disagrees with how its submission came out")
        # And in Lean's words, not only in its verdict. `ok` alone left the
        # diagnostic free: the `output` of a failed attempt could be rewritten
        # in the block and in its duplicated event together while the
        # `submit_proof` that produced it kept the real one, so a record could
        # say a tactic failed for a reason Lean never gave. The attempt's
        # `output` is that submission's `output` -- the ladder returns the
        # dispatch's own result text -- so they are compared as the one string
        # they are.
        elif result.get("output") != attempt.get("output"):
            issues.append(f"closer attempt {index + 1} reports output its submission did not produce")
    return issues


def _sketch_source(trajectory: dict[str, Any], proof: str, *, audited: bool = False) -> str | None:
    """The file a check on `proof` would have handed Lean.

    Rebuilt from the request the trajectory records, the same way the verified
    path rebuilds `proof.lean`. None when the request is not readable enough to
    rebuild from, which is reported by its own check rather than by a
    comparison against a guess.

    `audited` for a `submit_proof`, which is the same file with the axiom
    report appended -- and the retained candidate can now have come through
    that door. Rebuilding the plain body for it would fail an honest record for
    the two lines `submit_proof` adds itself.
    """
    request = trajectory.get("request")
    if not isinstance(request, dict):
        return None
    declaration = str(request.get("declaration") or "")
    imports = request.get("imports")
    if not declaration or not isinstance(imports, list) or not imports:
        return None
    header = "\n".join(f"import {name}" for name in imports)
    body = f"{header}\n\n{declaration} := {proof.strip()}\n"
    if not audited:
        return body
    # Named the way Lean names it back, which is what `LeanTools.target_name`
    # exists to get right: `theorem _root_.bar` is printed as `bar`, and an
    # audit source built with the unnormalised name matches nothing.
    found = DECLARATION_HEAD.match(declaration)
    name = declared_name(found.group(1)) if found else None
    if name is None:
        return None
    return LeanTools.with_audit(body, (f"axioms {name}",))


def _sketch_issues(
    result: dict[str, Any],
    trajectory: dict[str, Any],
    events: list[dict[str, Any]],
    writeup: str,
    reason: Any,
) -> list[str]:
    """The kept sketch, in all three places it appears, or in none of them.

    A partial result is valid only when its remaining holes are explicit, so
    the three representations -- `result.json`, `trajectory.json`, and the
    `writeup.md` section a human reads -- have to agree with each other and
    with the `sketch_proof` event that produced them. Otherwise a recorded run
    could have its holes edited out of whichever copy a reader opens.

    Absent from both records means a run from before sketches existed.
    """
    def _kept(names: tuple[str, ...]) -> list[dict[str, Any]]:
        return [
            event for index, event in enumerate(events)
            if event.get("type") == "tool"
            and event.get("name") in names
            and isinstance(event.get("result"), dict)
            and event["result"].get("ok") is True
            and not _discarded(events, index)
        ]

    # Two lists, because the runner has two rules. `sketch_proof` is what puts
    # a run into sketch mode -- so it alone decides whether a record *should*
    # carry one. A `check_proof` Lean accepted can only replace what is already
    # retained, and that is what makes the newest development the kept one: a
    # model that sketched with holes, closed them, and ran out of turns before
    # submitting would otherwise leave the older skeleton published as the
    # run's remaining work, with the trajectory two events above proving it was
    # not.
    def _submitted(event: dict[str, Any]) -> bool:
        """A submission Lean accepted and the axiom audit then refused.

        The third door a candidate comes through, and the one `ok` cannot
        answer for: the audit rewrites a submission's result, so a body Lean
        refused and a body Lean accepted and the audit refused both reach the
        record as `ok: false`. `lean_accepted` is what the runner writes to
        tell them apart. A submission the audit *passed* is a verified run and
        carries no sketch at all, so this is exactly the set the runner retains
        from.
        """
        return (
            event.get("name") == "submit_proof"
            and event.get("lean_accepted") is True
            and isinstance(event.get("result"), dict)
            and event["result"].get("ok") is False
        )

    accepted_events = _kept(("sketch_proof",))
    # In the order they happened, because "the last skeleton Lean accepted" is
    # a question about the sequence: a `check_proof` and a refused submission
    # sorted apart would name whichever list ran longer rather than whichever
    # event came last.
    latest_events = [
        event for index, event in enumerate(events)
        if event.get("type") == "tool"
        and not _discarded(events, index)
        and (
            (
                event.get("name") in ("sketch_proof", "check_proof")
                and isinstance(event.get("result"), dict)
                and event["result"].get("ok") is True
            )
            or _submitted(event)
        )
    ]
    if "sketch" not in trajectory and "sketch" not in result:
        # The legacy-record exception, and only where it is genuinely one. A
        # trajectory holding an accepted sketch is a record from this code
        # whose fields have been removed -- and taking the exception there let
        # the human-facing artifact drop every remaining hole while the
        # trajectory still proved an unfinished skeleton had been produced.
        if accepted_events:
            return ["the run accepted a sketch but the record carries no sketch fields"]
        return []
    issues: list[str] = []
    if result.get("sketch") != trajectory.get("sketch"):
        issues.append("the kept sketch differs between result.json and trajectory.json")
    sketch = result.get("sketch")
    # `_discarded` was applied above for the same reason submission validation
    # uses it: a skeleton that elaborated after the deadline carries the
    # runner's discard marker and is not part of the run's result. Counted as
    # accepted, an honest timeout was refused for "accepting a sketch no record
    # carries" -- the record was right and the audit was wrong.
    accepted = accepted_events
    from hardy.documents.batch import SKETCH_HEADING, sketch_section

    carried = SKETCH_HEADING in writeup
    if reason == "verified":
        # A verified run has the proof to show. A skeleton recorded beside it
        # invites a reader to weigh the two against each other.
        if sketch is not None:
            issues.append("a verified run records a sketch beside its proof")
        if carried:
            issues.append("a verified run's writeup carries a sketch section")
        return issues
    if sketch is None:
        if accepted:
            issues.append("the run accepted a sketch that no record carries")
        if carried:
            issues.append("writeup.md carries a sketch the record does not")
        return issues
    if not isinstance(sketch, dict):
        return [*issues, "the kept sketch is not an object"]
    # Recomputed from the skeleton rather than compared between copies. Edited
    # consistently everywhere -- `proof: "by sorry"` beside `holes: []` in all
    # three artifacts and the event -- the copies agree with each other and
    # conceal the hole from every one of them. Lean's own rule is the only
    # thing outside that agreement.
    expected = [item.model_dump(mode="json") for item in LeanTools.holes(str(sketch.get("proof", "")))]
    if sketch.get("holes") != expected:
        issues.append("the kept sketch's holes are not the ones its own skeleton contains")
    # And the shape before anything renders it. `sketch_section` indexes
    # `proof` and reads `keyword` and `line` off every hole, so a truncated or
    # hand-edited record -- `holes` a string, or a list of strings, or `proof`
    # missing entirely -- took the audit down with a `TypeError` or a
    # `KeyError` two comparisons later. "This artifact is invalid" is the
    # finding; a crash is the one answer a validator may not give. Returned on
    # rather than carried past, because every check below this point either
    # renders the sketch or compares something already known to be wrong.
    if not _renderable(sketch):
        issues.append("the kept sketch is not shaped like one: it cannot be rendered or compared")
        return issues
    if not accepted:
        issues.append("a sketch is recorded that no accepted sketch_proof produced")
    else:
        last = latest_events[-1] if latest_events else accepted[-1]
        if _proof_argument(last) != str(sketch.get("proof", "")):
            issues.append("the kept sketch is not the last skeleton Lean accepted")
        # Asked of `sketch_proof` alone, because it is the only tool that
        # reports a hole list: `check_proof` answers whether Lean accepted the
        # body and says nothing about holes, so an empty list there is silence
        # rather than a claim of none. What the sketch's own holes are checked
        # against in every case is the skeleton itself, a dozen lines above --
        # this is the second witness, not the only one.
        if last.get("name") == "sketch_proof" and last["result"].get("holes") != sketch.get("holes"):
            issues.append("the kept sketch's holes are not the ones Lean reported")
        # And the door it came through, because the writeup says a different
        # true thing about each. `submitted` is what makes the section report
        # an axiom refusal rather than a body nothing has audited, so a record
        # that set it either way would choose its own wording.
        if bool(sketch.get("submitted")) != (last.get("name") == "submit_proof"):
            issues.append("the kept sketch does not agree about whether it was submitted")
        # And the file Lean actually elaborated. Everything above compares the
        # record against itself; this compares it against the one thing in the
        # trajectory that came out of Lean -- the source it was given and the
        # hash of it. A skeleton swapped consistently through every artifact
        # still leaves this describing the program that was really checked.
        # Required, not merely compared when present. Conditional checks let
        # missing evidence pass: an event stripped of its `source`, or a
        # request edited until it could not be rebuilt from, skipped the one
        # comparison that reaches outside the record -- and a sketch nothing
        # can tie to a Lean run is a sketch with no evidence behind it.
        rebuilt = _sketch_source(
            trajectory, str(sketch.get("proof", "")), audited=last.get("name") == "submit_proof"
        )
        recorded = str(last["result"].get("source") or "")
        digest = last["result"].get("source_sha256")
        if rebuilt is None:
            issues.append("the trajectory's request cannot rebuild the sketch's source")
        elif not recorded or not digest:
            issues.append("the accepted sketch event records no source for Lean to have elaborated")
        else:
            if rebuilt != recorded:
                issues.append("the kept sketch is not the source Lean was given")
            if hashlib.sha256(rebuilt.encode("utf-8")).hexdigest() != digest:
                issues.append("the kept sketch does not hash to the source Lean recorded")
    if not carried:
        issues.append("writeup.md does not carry the sketch the record kept")
    elif sketch_section(sketch) not in writeup:
        # The whole section, not the code block alone. Checking only that the
        # skeleton appears somewhere let an honest writeup be edited from
        # "1 hole" to "0 holes" with the Lean untouched -- and the writeup is
        # the artifact a reader opens, so that is exactly where a partial
        # result would most usefully conceal its remaining work.
        issues.append("writeup.md's sketch section is not the one the record implies")
    return issues


def _renderable(sketch: dict[str, Any]) -> bool:
    """Whether a recorded sketch is shaped like one.

    Asked before anything renders it. `runner.sketch_section` is required
    verbatim by the writeup check, and it indexes `proof` and reads `keyword`
    and `line` off every hole -- so a malformed record has to become a finding
    here rather than an exception out of the audit.
    """
    holes = sketch.get("holes")
    return isinstance(sketch.get("proof"), str) and isinstance(holes, list) and all(
        isinstance(item, dict) and "keyword" in item and "line" in item for item in holes
    )


def _proof_argument(event: Mapping[str, Any]) -> str:
    """The `proof` a recorded tool event was called with, or "".

    `or {}` is not a guard: a truthy non-mapping -- a string, from a truncated
    or hand-merged trajectory -- passes straight through it and raises
    `AttributeError` on `.get`, which turns "this record is invalid" into a
    crash. A validator may report anything except that.
    """
    arguments = event.get("arguments")
    return str(arguments.get("proof", "")) if isinstance(arguments, Mapping) else ""


def _discarded(events: list[dict[str, Any]], index: int) -> bool:
    """Whether the tool event at `index` carries the runner's discard marker.

    The runner appends the marker immediately before the tool event of a
    submission that finished after the deadline, so the marker belongs to the
    event that follows it and to no other. Looking on both sides was tried
    and is wrong: an on-time acceptance followed by a late one reads
    `tool, discarded, tool`, and the marker then condemned the valid one.
    """
    previous = events[index - 1] if index > 0 else {}
    return previous.get("type") == "discarded" and previous.get("name") == events[index].get("name")


def _axiom_line(
    events: list[dict[str, Any]], name: str, source_sha256: str
) -> tuple[str, ...] | None:
    """What Lean printed for `#print axioms <name>` on the accepted submission.

    Read from the diagnostics the trajectory kept of the last `submit_proof`
    that Lean accepted, the deadline did not discard, and whose source hash
    is `proof.lean`'s -- the runner records the hash of what each check
    elaborated, so an accepted event about some other source is not a
    witness for this file. Those diagnostics are Lean's own output as the
    runner recorded it, which is the nearest a directory without Lean in it
    has to an independent witness for the verdict in `result.json`. None
    when no such line was recorded.
    """
    accepted = [
        event
        for index, event in enumerate(events)
        if event.get("type") == "tool" and event.get("name") == "submit_proof"
        and isinstance(event.get("result"), dict) and event["result"].get("ok")
        and event["result"].get("source_sha256") == source_sha256
        # An acceptance the deadline discarded was never graded, so it cannot
        # be the one the grade rests on; the runner writes the marker beside it.
        and not _discarded(events, index)
    ]
    if not accepted:
        return None
    diagnostics = accepted[-1]["result"].get("diagnostics") or []
    spoken = "\n".join(str(item.get("message", "")) for item in diagnostics if isinstance(item, dict))
    reports = audit.parse(spoken, (name,))
    return reports[0].axioms if reports else None


def validate_batch_consistency(output_dir: Path) -> tuple[str, ...]:
    """Report every way a `hardy batch` output directory disagrees with itself.

    The four artifacts were written by one run, so agreeing with each other
    proves less than it looks; what the check refuses is the run that could
    not have happened as described -- a `verified` with no `proof.lean`, a
    `proof.lean` that does not end in the audit line the verdict rests on, a
    proof body carrying a hole the audit never saw, a turn count for a run the
    wall clock cut off before the provider could report one.
    """
    issues: list[str] = []
    result_path = output_dir / "result.json"
    trajectory_path = output_dir / "trajectory.json"
    writeup_path = output_dir / "writeup.md"
    proof_path = output_dir / "proof.lean"
    for path in (result_path, trajectory_path, writeup_path):
        if not path.exists():
            issues.append(f"{path.name} is missing")
    if issues:
        return tuple(issues)
    try:
        result = _read_json(result_path)
        trajectory = _read_json(trajectory_path)
    except ValueError as error:
        return (f"a batch record is not readable JSON: {error}",)
    if not isinstance(result, dict) or not isinstance(trajectory, dict):
        return ("a batch record is valid JSON but not an object",)
    writeup = writeup_path.read_text(encoding="utf-8")
    reason = result.get("terminal_reason")
    if trajectory.get("terminal_reason") != reason:
        issues.append("terminal reason differs between result.json and trajectory.json")
    issues.extend(budget_record_issues(output_dir, trajectory.get("provider_budget")))
    from hardy.workflows.batch_recording import attempt_record_issues

    issues.extend(attempt_record_issues(output_dir))
    # A mapping or nothing. `or {}` forgave a falsy value and kept a truthy
    # one of any type, so a hand-edited or half-merged trajectory whose
    # `request` is a string took the validator down with an `AttributeError`
    # two lines later. "This record is invalid" is the finding; a crash is the
    # one answer a validator may not give.
    recorded = trajectory.get("request")
    request = recorded if isinstance(recorded, dict) else {}
    if recorded is not None and not isinstance(recorded, dict):
        issues.append("the trajectory's request is not an object")
    # The event list, once, before anything walks it. Every traversal below
    # calls `.get` on each entry, so a truncated or hand-edited trajectory
    # whose `events` is a string -- iterating one yields characters -- or holds
    # a bare number took the validator down with an `AttributeError` rather
    # than reporting the run invalid. Checked here rather than at each
    # traversal, because the shape is one fact and a dozen guards is a dozen
    # chances to forget one.
    listed = trajectory.get("events")
    events = listed if isinstance(listed, list) and all(isinstance(item, dict) for item in listed) else []
    if listed is not None and not events and listed != []:
        issues.append("the trajectory's events are not a list of objects")
    declaration = str(request.get("declaration", ""))
    head = DECLARATION_HEAD.match(declaration)
    name = declared_name(head.group(1)) if head else None
    if name is None:
        issues.append("trajectory names no auditable declaration")

    issues.extend(_toolchain_issues(trajectory.get("toolchain"), "trajectory"))
    if result.get("toolchain") != trajectory.get("toolchain"):
        issues.append("toolchain identity differs between result.json and trajectory.json")
    # The human-facing copy too. Nothing hashes a batch writeup, so a stale or
    # edited one could name another Lean beside a record that names this one.
    from hardy.documents.batch import describe_toolchain

    if describe_toolchain(trajectory.get("toolchain")) not in writeup:
        issues.append("writeup.md names a different toolchain from the record")
    # And the statement itself: a writeup swapped in from another run on the
    # same toolchain would otherwise pass on its grade marker alone.
    claim_block = f"## Claim\n\n{request.get('informal_claim', '')}\n\n## Exact Lean statement\n\n```lean\n{declaration}\n```"
    if claim_block not in writeup:
        issues.append("writeup.md does not state the recorded claim and Lean statement")
    issues.extend(_usage_issues(result.get("usage"), "result"))
    if result.get("usage") != trajectory.get("usage"):
        issues.append("usage differs between result.json and trajectory.json")
    if "turns" not in result:
        issues.append("result states no turn count (absent is not null)")
    limits = trajectory.get("limits") or {}
    # A run that asked no provider anything -- the ladder closed the statement,
    # or it used the whole budget first -- has a turn count, and it is zero.
    # The runner says which of the two happened in an event of its own, and
    # neither can be true of a run that reached a provider: an exchange that
    # happened leaves a `result` event whatever it reported. Both are required,
    # so a `declined_turn` from a mid-exchange decline on a loop Hardy owns --
    # where turns really were spent -- does not reach this.
    unasked = not any(event.get("type") == "result" for event in events) and any(
        (
            event.get("type") == "limit"
            and event.get("limit") == "wall_seconds"
            and ("no model turn was spent" in str(event.get("detail", ""))
             or "before a model turn could be spent" in str(event.get("detail", "")))
        )
        or (
            event.get("type") == "declined_turn"
            and event.get("stage", "closers") == "closers"
            and "before a model turn was spent" in str(event.get("why", ""))
        )
        for event in events
    )
    if unasked:
        if result.get("turns") != 0:
            issues.append("a run that asked no provider anything reports a turn count other than zero")
    elif (
        "turns" in result
        and reason == "wall_clock_limit"
        and result["turns"] is not None
        and limits.get("turns_enforced_by") != "hardy"
    ):
        # The *provider's* count rides on its final result, which a run Hardy's
        # clock cancelled never receives, so a count there was invented. A
        # harness-owned loop counts its own provider calls and publishes them
        # however the exchange ended, so on that backend the count is the
        # honest one and refusing it would fail every truthful API timeout.
        issues.append("a wall-clock-cancelled run reports a turn count the provider never delivered")
    for field in ("wall_seconds", "elapsed_seconds", "max_turns"):
        if field not in limits:
            issues.append(f"trajectory limits do not state {field}")
    if not trajectory.get("model") or not trajectory.get("backend"):
        issues.append("trajectory does not name the model and backend that ran")
    issues.extend(_closer_issues(trajectory, events, reason))
    issues.extend(_sketch_issues(result, trajectory, events, writeup, reason))

    if reason == "verified":
        issues.extend(_verified_batch_issues(result, events, name, declaration, request, proof_path, writeup))
    else:
        if reason not in BATCH_FAILURES:
            issues.append(f"unknown terminal reason: {reason!r}")
        # A failure reason names an event the runner recorded when it
        # happened; a record relabelled after the fact has no such event.
        errors = [str(event.get("error", "")) for event in events if event.get("type") == "error"]
        limits_hit = [event.get("limit") for event in events if event.get("type") == "limit"]
        if reason == "wall_clock_limit" and not any(text.startswith("TimeoutError") for text in errors) and "wall_seconds" not in limits_hit:
            # Either the exchange timed out, which raises, or the closers used
            # the budget before a model turn was spent, which does not raise
            # and records the limit as its own event. Both are the wall clock
            # ending the run; requiring the exception would have made the
            # second look like a relabelled record.
            issues.append("a wall_clock_limit run records no TimeoutError event or wall_seconds limit")
        if reason == "turn_limit" and "max_turns" not in limits_hit:
            issues.append("a turn_limit run records no max_turns limit event")
        if reason == "provider_budget_limit":
            budget = trajectory.get("provider_budget")
            if not isinstance(budget, dict) or not budget.get("ending_limit") or budget["ending_limit"] not in limits_hit:
                issues.append("a provider_budget_limit run lacks its authenticated ending limit")
        if reason == "runtime_error" and not any(not text.startswith("TimeoutError") for text in errors):
            issues.append("a runtime_error run records no error event")
        if result.get("formalization") != "not formalized":
            issues.append("a run that did not verify is graded as formalized")
        if result.get("proof") is not None:
            issues.append("a run that did not verify names a proof")
        if proof_path.exists():
            issues.append("a run that did not verify left a proof.lean")
        if "No completed artifact" not in writeup or f"Terminal reason: `{reason}`" not in writeup:
            issues.append("writeup.md does not say the run produced no artifact and why")
        accepted = [
            index for index, event in enumerate(events)
            if event.get("type") == "tool" and event.get("name") == "submit_proof"
            and isinstance(event.get("result"), dict) and event["result"].get("ok")
        ]
        for index in accepted:
            # An acceptance the deadline discarded is recorded as such, beside
            # it, and one that was not is a verified proof graded as nothing.
            if not _discarded(events, index):
                issues.append("an accepted submission was recorded but the run is not verified")
        axioms = result.get("axioms") or {}
        if axioms.get("status") == "clean":
            issues.append("a run that did not verify carries a clean axiom audit")
    return tuple(issues)


def _verified_batch_issues(
    result: dict[str, Any],
    events: list[dict[str, Any]],
    name: str | None,
    declaration: str,
    request: dict[str, Any],
    proof_path: Path,
    writeup: str,
) -> list[str]:
    from hardy.formal.contracts import Request
    from hardy.formal.lean import LeanTools

    issues: list[str] = []
    proof = result.get("proof")
    if result.get("formalization") != "kernel verified":
        issues.append("a verified run is not graded kernel verified")
    if not isinstance(proof, str) or not proof.strip():
        issues.append("a verified run names no proof")
        return issues
    if FORBIDDEN_TOKEN.search(scannable(proof)):
        issues.append("the verified proof carries a forbidden token")
    violation = proof_body_violation(proof)
    if violation is not None:
        issues.append(f"the verified proof is not a proof body: {violation}")
    if not proof_path.exists():
        issues.append("a verified run has no proof.lean")
        return issues
    source, error_issue = _exact_source(proof_path, label="proof.lean")
    if error_issue is not None:
        issues.append(error_issue)
        return issues
    if name is not None:
        try:
            tools = LeanTools(
                Request(declaration, str(request.get("informal_claim", "")), tuple(request.get("imports") or ("Mathlib",))),
                ("unused",),
            )
            expected = tools.source(proof, audit=True)
        except ValueError as error:
            issues.append(f"the request cannot be rebuilt: {error}")
        else:
            # Byte for byte: the file is what a reader rechecks, and it must be
            # the request's declaration, the result's proof, and the audit line
            # -- nothing the model chose in between.
            if source != expected:
                issues.append("proof.lean is not the request's declaration, the result's proof, and the audit line")
        if not source.rstrip().endswith(axiom_report_line(name)):
            issues.append("proof.lean does not end with the axiom report the verdict rests on")
    if FORBIDDEN_TOKEN.search(scannable(source)):
        issues.append("proof.lean carries a forbidden token")
    audited = result.get("axioms") or {}
    if audited.get("status") != "clean":
        issues.append("a verified run's axiom audit is not clean")
    declared = audited.get("declarations") or []
    if len(declared) != 1 or (name is not None and declared[0].get("name") != name):
        issues.append("the axiom audit does not report exactly the target declaration")
    else:
        found = tuple(declared[0].get("axioms") or ())
        # A batch run declares nothing -- `--assume` belongs to `hardy prove`
        # -- so Lean's own axioms are the whole allowlist here.
        unexpected = tuple(axiom for axiom in found if axiom not in ALLOWED_AXIOMS)
        if unexpected:
            issues.append("the axiom audit admits unexpected axioms: " + ", ".join(unexpected))
        if name is not None:
            printed = _axiom_line(events, name, hashlib.sha256(source.encode("utf-8")).hexdigest())
            if printed is None:
                issues.append("the trajectory keeps no axiom report from Lean over proof.lean's own bytes")
            elif set(printed) != set(found):
                issues.append("the axiom line Lean printed differs from the audit verdict in result.json")
    if "Formalization: **kernel verified**" not in writeup or "No completed artifact" in writeup:
        issues.append("writeup.md does not grade the run as kernel verified")
    return issues
