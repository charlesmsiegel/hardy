from __future__ import annotations

import json
import os
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any, Protocol

from . import audit, compaction
from . import closers as closer_ladder
from .chat import provenance
from .claude_runtime import TurnLimitReached
from .latency import manifest_binds
from .lean import LeanToolResult, LeanTools, environment_identity
from .models import Request, RunResult, ToolResult
from .prompts import BATCH_SYSTEM_PROMPT, batch_task_prompt
from .usage import Usage

WARNING = "Generated Lean is not sandboxed. Run Hardy only with trusted output in a disposable development environment."


TOOLS = [
    {"type": "function", "function": {"name": "check_proof", "description": "Elaborate a complete candidate proof against the unchanged theorem statement.", "parameters": {"type": "object", "properties": {"proof": {"type": "string"}}, "required": ["proof"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "inspect_goal", "description": "Show the goal state after an optional tactic prefix.", "parameters": {"type": "object", "properties": {"tactic": {"type": "string"}}, "additionalProperties": False}}},
    {"type": "function", "function": {"name": "search_declaration", "description": "Check whether a declaration name exists in the current environment.", "parameters": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "sketch_proof", "description": "Elaborate a proof skeleton whose holes are deliberate. `sorry` and `admit` are allowed and are what this tool reports; an error is still a failure. The accepted skeleton is kept, so work continues from it rather than starting again. A sketch is never a submission and is never verified.", "parameters": {"type": "object", "properties": {"proof": {"type": "string"}}, "required": ["proof"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "submit_proof", "description": "Submit the final proof for a strict, hole-free check.", "parameters": {"type": "object", "properties": {"proof": {"type": "string"}}, "required": ["proof"], "additionalProperties": False}}},
]


class Runtime(Protocol):
    model: str
    def ask(self, text: str) -> str: ...


def _write_json(path: Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _audited(result: LeanToolResult, lean: LeanTools) -> tuple[ToolResult, audit.Verdict | None, dict[str, Any] | None]:
    """A kernel-accepted proof is not yet a verified one.

    Lean's exit code says the file elaborated. It says nothing about what the
    proof rests on, and a batch run has nobody to approve an assumption, so
    anything beyond the standard axioms is refused rather than recorded and
    shipped. The third element is what the record should say when the proof is
    refused: an audit that could not run is a different fact from one that ran
    and found something, and both differ from never having audited anything.
    """
    def refused(why: str) -> LeanToolResult:
        """The audit's verdict over Lean's own answer, rather than instead of it.

        Rebuilt from the result rather than replaced by a bare `ToolResult`:
        `source_sha256` is the one field in the record that ties a submission
        to the file Lean was really given, and dropping it left an honest
        artifact -- a candidate the audit refused, kept as the development in
        hand -- with nothing `hardy accept --recorded` could check it against.
        Lean's diagnostics survive for the same reason: what Lean said is not
        untrue because the audit went on to refuse what it rests on.
        """
        return replace(result, ok=False, output=why)

    name = lean.target_name
    if name is None:
        why = "an anonymous `example` cannot be audited; state the claim as a named theorem or lemma"
        return refused(why), None, audit.unestablished(why)
    # The whole report, not the tail a model is shown: an audit graded on a
    # truncated report would refuse a proof for a line that was merely cut off.
    reports = audit.parse(result.report, (name,))
    if reports is None:
        why = f"the axiom audit for `{name}` could not be established; remove any #print axioms from the proof, Hardy adds its own"
        return refused(why), None, audit.unestablished(why)
    verdict = audit.classify(reports, ())
    if verdict.status != "clean":
        why = f"Lean accepted the proof but the axiom audit refused it: {audit.describe(verdict)}"
        return refused(why), verdict, verdict.as_dict()
    return result, verdict, None


def identify_toolchain(lean: LeanTools) -> dict[str, Any]:
    """The Lean and Mathlib this run's checks answer for, or why it is not known.

    `lean_command` names a program and `lean_project` a directory; neither is
    the identity of what elaborated the proof, and two projects on different
    Mathlib revisions answer to both identically. So the identity is asked of
    the project and the compiler -- the same question the staged path asks --
    and a run that cannot answer it records the reason. Never a guess, and
    never silent: `{"unrecorded": ...}` is a finding the acceptance audit can
    refuse, where a missing key is one it would have to assume.
    """
    command = tuple(lean.lean_command)
    # Only `lake env lean` necessarily resolves imports through the project's
    # manifest; a bare `lean` or a wrapper may import from another Mathlib
    # entirely, and pairing its version with this manifest's revision would
    # attribute the proof to a library it may not have used. The same test
    # `hardy latency` applies (`latency.manifest_binds`), for the same reason.
    if not manifest_binds(command):
        return {
            "unrecorded": (
                f"lean_command {' '.join(command)!r} is not `lake env lean`, so the Mathlib "
                "it imports cannot be read from the project manifest"
            )
        }
    try:
        identity = environment_identity(
            lean.project, lean_command=command, timeout_seconds=lean.timeout
        )
    except (ValueError, OSError, KeyError, StopIteration, json.JSONDecodeError) as error:
        return {"unrecorded": str(error) or type(error).__name__}
    return identity.model_dump(mode="json")


def describe_toolchain(toolchain: dict[str, Any] | None) -> str:
    """The toolchain block of `writeup.md`, in words a reader can quote."""
    if not toolchain:
        return "Not recorded."
    if "unrecorded" in toolchain:
        return f"Not identified: {toolchain['unrecorded']}"
    return (
        f"- Lean: {toolchain.get('lean_version')} (commit {toolchain.get('lean_commit')})\n"
        f"- Mathlib: {toolchain.get('mathlib_revision')}\n"
        f"- Lake manifest SHA-256: {toolchain.get('lake_manifest_sha256')}"
    )


#: The heading a kept sketch is written under. Exported because the audit has
#: to look for exactly what the writer wrote.
SKETCH_HEADING = "## Sketch (not a proof)"


def longest_run(text: str, character: str) -> int:
    """The longest unbroken run of `character` in `text`, or 0."""
    longest = run = 0
    for item in text:
        run = run + 1 if item == character else 0
        longest = max(longest, run)
    return longest


def sketch_section(sketch: dict[str, Any]) -> str:
    """The `writeup.md` section a kept sketch is reported in.

    One function, used by the writer and required verbatim by the audit. Split
    between the two, the human-facing copy could say "0 holes" over a record
    that says one -- and the writeup is the artifact a reader actually opens,
    so that is where a partial result would most usefully conceal its remaining
    work.

    Named a sketch in the heading and again in the sentence under it. A partial
    development in a file called `writeup.md` is exactly the thing a hurried
    reader mistakes for a result, so the two words that stop them are not left
    to the section title alone.
    """
    holes = sketch["holes"]
    if holes:
        where = ", ".join(f"{item['keyword']} at line {item['line']}" for item in holes)
        body = (
            f"The run left an elaborating skeleton with {len(holes)} hole(s) in it "
            f"({where}). Lean accepted its structure and nothing else: a hole closes any "
            "goal, so this is not evidence for the claim and is not verified."
        )
    elif sketch.get("submitted"):
        # A hole-free body that was submitted, that Lean accepted, and that the
        # axiom audit then refused -- the only way a submission becomes the
        # retained candidate. The sentence below it would be false twice over:
        # it *was* submitted, and the axiom report did run. Saying "nothing has
        # audited what it rests on" over a body refused for what it rests on
        # would name the wrong remaining work, which is the whole failure this
        # section exists not to commit.
        body = (
            "The run left a complete candidate: Lean elaborated it with no hole in its "
            "own proof body, and the run submitted it. The axiom report refused what it "
            + "rests on -- the grade above names the axioms -- so this is not verified "
            "and is not a result."
        )
    else:
        # A hole-free body `sketch_proof` accepted, on a run that ended before
        # it was submitted. The first sentence is false about it -- there is no
        # hole, and saying one closes the goal would be a reason that does not
        # apply to the artifact underneath it. What *is* true is narrower and
        # is the whole of why it is not a result: nothing audited what it rests
        # on, because `submit_proof` is the only thing that runs that audit and
        # this was never submitted.
        body = (
            "The run left a complete candidate: Lean elaborated it with no hole in its "
            "own proof body, and the run ended before it was submitted. "
            + "Nothing has audited what it rests on -- only `submit_proof` runs the "
            "axiom report -- so this is not verified and is not a result."
        )
    # A fence longer than any run of backticks the proof contains. Three
    # backticks inside a Lean block comment are legal Lean, and with a fixed
    # fence they closed this block early -- after which the rest of a
    # model-written proof is rendered as ordinary writeup prose, free to forge
    # a heading or a grade under Hardy's own name. The recorded proof and the
    # generated section still agreed byte for byte, so the audit saw nothing
    # wrong; what was wrong was the rendering, and the fence is where that is
    # fixed.
    fence = "`" * max(3, longest_run(sketch["proof"], "`") + 1)
    return f"\n{SKETCH_HEADING}\n\n{body}\n\n{fence}lean\n{sketch['proof']}\n{fence}\n"


def _limits(runtime: Any, max_turns: int, wall_seconds: float, elapsed: float, context_window: int, compacted: bool) -> dict[str, Any]:
    """The bounds a run declared, and who actually applied each of them.

    Asked of the runtime rather than stated here. A trajectory that names a
    limit without naming its keeper is the honesty problem issue #23 is about:
    under an SDK-driven loop the turn bound is the SDK's to apply, and writing
    "hardy" beside it would claim a guarantee the harness cannot make. A
    backend that keeps both says so, and one that keeps neither could say that
    too.
    """
    enforcement = getattr(runtime, "enforcement", None)
    if not isinstance(enforcement, dict):
        enforcement = {"turns": "provider sdk", "wall_clock": "hardy"}
    limits = {
        "max_turns": max_turns,
        "wall_seconds": wall_seconds,
        "turns_enforced_by": enforcement.get("turns", "provider sdk"),
        "wall_clock_enforced_by": enforcement.get("wall_clock", "hardy"),
        "elapsed_seconds": elapsed,
        # The window the run was planned against, whether or not this backend
        # let Hardy do the planning. A bound that shaped the experiment and
        # went unrecorded is the same omission `turns_enforced_by` exists to
        # close, one field along.
        "context_window": context_window,
        "compacted_by": "hardy" if compacted else "nobody: the SDK owns the loop",
    }
    if limits["turns_enforced_by"] != "hardy":
        limits["note"] = "the SDK owns the loop; see issue #23"
    return limits


def run(request: Request, make_runtime: Callable[..., Runtime], lean: LeanTools, output_dir: Path, *, max_turns: int = 8, wall_seconds: float = 300, toolchain: dict[str, Any] | None = None, closers: Sequence[str] | None = None, context_window: int = compaction.CONTEXT_WINDOW) -> RunResult:
    """One unattended attempt at `request`, and everything it is recorded by.

    `closers` is the cheap Lean ladder from issue #23, tried before the model
    is asked anything. None means nobody asked for it and none runs, which is
    the default because a run whose result came from a tactic ladder and a run
    whose result came from a model are not the same experiment. Whichever it
    was is written into `trajectory.json` either way.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    # Before the first turn, so a run the wall clock cuts short still says
    # what it ran against; and asked rather than trusted from the caller when
    # nobody said.
    toolchain = identify_toolchain(lean) if toolchain is None else toolchain
    events: list[dict[str, Any]] = []
    found: dict[str, Any] = {"result": None, "proof": None, "verdict": None}
    # A submission Lean accepted and the audit then refused. Kept so the terminal
    # reason can say what happened instead of "nothing was submitted", and so the
    # verdict that refused it survives into the record.
    refused: dict[str, Any] = {"axioms": False, "record": None}
    # The last skeleton Lean accepted with its holes still in it (#52). Kept so
    # a run that never closes every hole still leaves the partial development
    # behind rather than only the transcript of having attempted one. It is
    # never a result: nothing here reaches `found`, and `submit_proof` is the
    # only door a verdict comes through.
    sketched: dict[str, Any] = {"proof": None, "holes": [], "submitted": False}
    # Cancelling the exchange does not stop a Lean check already running on a
    # worker thread, and that thread is waited on during shutdown — so late work
    # can land before the timeout is even caught. The deadline itself decides
    # what counts, not a flag set after the fact.
    closed = threading.Event()
    deadline: dict[str, float] = {}
    # What the provider says the exchange cost, folded by the same ledger the
    # interactive session uses -- so a batch record and a `/status` line cannot
    # come to disagree about what "unreported" means, or about whether a cache
    # read is a token that was spent. Held in a dict because `observe` is called
    # from the SDK's own thread: `Usage` is frozen, so rebinding the entry
    # publishes a whole new total rather than mutating a shared counter.
    spend: dict[str, Usage] = {"total": Usage()}

    def observe(event: dict[str, Any]) -> None:
        """Keep the event, and bill the run for it if it carries a report."""
        events.append(event)
        if event.get("type") == "result":
            spend["total"] = spend["total"].record(event)

    # One tool call at a time. `claude_runtime._wrap` hands each call to
    # `asyncio.to_thread`, so a response asking for several runs them on
    # several threads at once -- and every branch of `_dispatch` decides
    # something about the run (which skeleton is retained, whether a submission
    # was kept, whether either landed after the deadline) and then appends the
    # event that says so. Those two steps are one fact. Interleaved, the
    # artifacts can retain one call's sketch while the events say the last
    # accepted one was another's, and `hardy accept --recorded` refuses an
    # honest run for a disagreement the run never made.
    #
    # Serialised whole rather than around the bookkeeping alone, because the
    # trajectory is a linear record of what Hardy did and a linear record of
    # overlapping work is not one: two Lean checks whose events straddle each
    # other cannot be read back as the sequence they are written as.
    one_at_a_time = threading.Lock()

    def _retain(name: str, proof: str, result: LeanToolResult) -> None:
        """Keep the newest development Lean accepted, if the budget bought it.

        The same clock a submission is judged against. A skeleton that began
        inside the deadline and elaborated after it is work the run's budget
        did not buy, and keeping it would put a partial artifact produced
        outside the recorded bound into a `wall_clock_limit` run's writeup.

        Overwritten rather than accumulated: this is the development's current
        state, and the transcript already holds every earlier one. The holes
        are recomputed from the body rather than read off the result, so the
        two doors this comes through record the same thing about it.
        """
        if not result.ok:
            return
        # A `check_proof` *replaces* a retained candidate; it never creates
        # one. Retaining on every accepted check would give a `## Sketch (not
        # a proof)` section to every run that ever asked Lean about a body with
        # a `sorry` in it, which is a much wider change than the one this is
        # for: a run is in sketch mode because it called `sketch_proof`, and
        # what this fixes is that mode going stale.
        if name != "sketch_proof" and sketched["proof"] is None:
            return
        if closed.is_set() or time.monotonic() > deadline.get("at", float("inf")):
            events.append({"type": "discarded", "name": name, "why": "completed after the wall-clock budget expired"})
            return
        sketched["proof"] = proof
        sketched["holes"] = [item.model_dump(mode="json") for item in lean.holes(proof)]
        # Which door it came through, because the writeup says different true
        # things about the two. A submitted candidate has been through the
        # axiom report and a checked one has not, and a section telling a
        # reader that nothing audited what a refused proof rests on would name
        # the wrong remaining work.
        sketched["submitted"] = name == "submit_proof"

    def dispatch(name: str, arguments: dict[str, Any]) -> ToolResult:
        """Hardy runs every proof check, whoever decided to ask for one.

        Serialised: see `one_at_a_time` above. The budget check inside is read
        after the wait rather than before it, so a call that queued while the
        clock ran out is refused for the reason that is true when it runs.
        """
        with one_at_a_time:
            return _dispatch(name, arguments)

    def _dispatch(name: str, arguments: dict[str, Any]) -> ToolResult:
        if closed.is_set():
            return ToolResult(False, "the run's budget expired before this tool call was made")
        # The run is over, and the calls still queued behind the one that ended
        # it do not get to change what it decided. Declining the next provider
        # turn does not reach these: one response can ask for two submissions,
        # and the second is dispatched from the same batch as the first -- so
        # the later one replaced the proof the artifacts were already going to
        # carry, with the audit and the writeup built from whichever arrived
        # last. Refused here rather than in the loop, because the runner is
        # what knows a run has its result, and this way the rule holds on the
        # backends whose SDK owns the loop as well.
        if found["result"] is not None:
            why = "the run's proof was accepted before this tool call was made"
            # Recorded rather than silently refused. A call the model made and
            # Hardy would not run is a fact about the run, and a trajectory
            # that simply lacks the second submission cannot be told from one
            # where the model never asked for it. Its own type, so nothing that
            # counts submissions or grades attempts reads a refusal Lean never
            # saw as one of them.
            events.append({"type": "refused_tool", "name": name, "arguments": arguments, "why": why})
            return ToolResult(False, why)
        # Bound before the try, because the event below is written on the way
        # out of both the body and the `except`: a `submit_proof` whose
        # arguments would not parse never reaches the assignment inside, and
        # reading it there would raise a `NameError` from the error path of the
        # error handler.
        submitted = False
        try:
            if name == "check_proof":
                proof = str(arguments["proof"])
                result = lean.check_proof(proof)
                # A development Lean accepted is a development Lean accepted,
                # whichever door it came through. A model that sketched with
                # holes, closed them, and ran out of turns before submitting
                # used to leave the artifacts pointing at the earlier skeleton
                # -- publishing the old holes as the run's remaining work while
                # the trajectory two events above proved they were closed. The
                # newest one Lean accepted is the development's current state,
                # and that is what the retained candidate means.
                _retain(name, proof, result)
            elif name == "inspect_goal":
                result = lean.inspect_goal(str(arguments.get("tactic", "")))
            elif name == "search_declaration":
                result = lean.search_declaration(str(arguments["name"]))
            elif name == "sketch_proof":
                proof = str(arguments["proof"])
                result = lean.sketch_proof(proof)
                _retain(name, proof, result)
            elif name == "submit_proof":
                proof = str(arguments["proof"])
                result = lean.check_proof(proof, final=True)
                verdict = None
                record = None
                # Whether Lean accepted it, before the audit had its say. The
                # audit turns an accepted proof into a refused one, and without
                # this the late branches below could no longer tell that a
                # submission had arrived at all.
                submitted = result.ok
                # Lean's own answer, kept because the audit is about to
                # overwrite `result` with its own. The two are different
                # judgements and `ok` alone conflates them: a body Lean refused
                # and a body Lean accepted and the audit refused are recorded
                # identically without this, and neither the retention below nor
                # `hardy accept --recorded` can tell them apart.
                accepted = result
                if result.ok:
                    result, verdict, record = _audited(result, lean)
                # Judged against the clock rather than a flag: a check that was
                # still running when the budget expired cannot count, and one
                # that finished before it can. Asked before either outcome is
                # kept, not just the good one -- recording a late refusal while
                # discarding a late acceptance would grade a run that ran out of
                # time as one that rested on a bad axiom.
                late = closed.is_set() or time.monotonic() > deadline.get("at", float("inf"))
                if late:
                    if submitted:
                        events.append({"type": "discarded", "name": name, "why": "completed after the wall-clock budget expired"})
                elif result.ok:
                    found["result"], found["proof"], found["verdict"] = result, proof, verdict
                elif record is not None:
                    refused["axioms"], refused["record"] = True, record
                    # A development Lean accepted, so it replaces the retained
                    # one. A run that sketched with holes, closed them, and
                    # then had the finished proof refused for an unapproved
                    # axiom otherwise published the older skeleton as the
                    # development in hand: the holes were the run's remaining
                    # work two events ago and the axiom is its remaining work
                    # now, and the artifacts named the wrong one.
                    #
                    # Here rather than beside the check, so the retention and
                    # the submission agree about what happened. Retained before
                    # the audit ran, a candidate whose audit pushed the run past
                    # the deadline was discarded as a submission and kept as a
                    # sketch, and the writeup then reported an axiom refusal the
                    # trajectory recorded as a timeout.
                    _retain(name, proof, accepted)
            else:
                result = ToolResult(False, f"unknown tool: {name}")
        except (KeyError, TypeError, ValueError) as error:
            result = ToolResult(False, f"invalid tool arguments: {error}")
        event: dict[str, Any] = {"type": "tool", "name": name, "arguments": arguments, "result": result.as_dict()}
        if name == "submit_proof":
            # Recorded because `ok` no longer answers it. The audit turns an
            # accepted proof into a refused one, so a submission Lean accepted
            # and a submission Lean rejected reach the trajectory with the same
            # `ok: false` -- and the retained candidate above is produced by
            # exactly one of them. Without this the audit cannot re-derive
            # which, and an honest artifact fails for a skeleton it can see but
            # cannot tie to the Lean run that made it.
            event["lean_accepted"] = submitted
        events.append(event)
        return result

    system = BATCH_SYSTEM_PROMPT
    task = batch_task_prompt(request.informal_claim, request.declaration, tuple(request.imports))
    start = time.monotonic()
    deadline["at"] = start + wall_seconds
    reason = "completed"
    ladder = dict(closer_ladder.DISABLED)
    if closers:
        # Through `dispatch`, not around it. A tactic's proof goes in by the
        # same door a model's does, so the axiom audit, the deadline and the
        # trajectory all apply to it unchanged -- the ladder is a decision
        # about whose turn it is, never a second route to a verdict.
        def submit(proof: str) -> tuple[bool, str]:
            outcome = dispatch("submit_proof", {"proof": proof})
            # Whether the run *kept* the submission, not whether Lean
            # eventually accepted it. A check that began inside the deadline
            # and finished outside it is discarded by `dispatch` and leaves
            # `found` unset -- and reporting that tactic in `closed_by` would
            # name a closer for a run that terminates with no verified proof.
            return found["result"] is not None, outcome.output

        outcome = closer_ladder.close(
            submit,
            closers,
            keep_going=lambda: not closed.is_set() and time.monotonic() < deadline["at"],
        )
        # What the ladder cost, in the same seconds the model would have spent.
        # Recorded because a run that spent four minutes elaborating tactics
        # and then reported a model turn limit is not readable without it.
        ladder = {**outcome.as_dict(), "seconds": round(time.monotonic() - start, 3)}
        events.append({"type": "closers", **ladder})
    # The ladder spends the run's clock, not a clock of its own. Left to the
    # declared figure, a run whose closers used four of five minutes would then
    # hand the model a fresh five -- so the command could take the ladder's
    # time plus the whole budget again, and the wall clock in the trajectory
    # would bound neither half.
    # What the ladder took off the clock, and nothing else. Subtracting the
    # measured elapsed time instead would shave a few microseconds of setup off
    # every run that asked for no ladder at all -- a difference that means
    # nothing and would make the declared bound and the applied one differ for
    # no reason.
    remaining = wall_seconds - float(ladder["seconds"])
    def compact(messages: list[compaction.Message]) -> list[compaction.Message] | None:
        """Batch's own compaction, assembled the way the interactive one is.

        Same rule, same cut, a narrower set of facts: an unattended run has no
        naming registry and no approved assumptions, but it has the claim it
        was given, the skeleton it is holding, and every failed attempt in
        Lean's own words -- which is the heading that looked like it needed a
        model. Wired here because a batch aimed at a smaller gateway, or given
        a raised turn limit, otherwise kept appending messages until the
        endpoint refused the request: the window was a setting the interactive
        surface honoured and this one did not.

        Offered to the runtime rather than assumed: a backend whose SDK owns
        the loop has no place to put it, and `limits.compacted_by` says which
        of the two happened.
        """
        overhead = compaction.overhead(system, TOOLS)
        outcome = compaction.plan(
            messages,
            context_window=context_window,
            reserve_tokens=compaction.RESERVE_TOKENS,
            keep_tokens=compaction.RECENT_TOKENS,
            overhead_tokens=overhead,
            output_tokens=int(getattr(runtime, "output_limit", None) or 0),
        )
        if not outcome.needed:
            # A request the window has no room for, with nothing above the
            # tail that may legally be cut. There is no compaction to perform,
            # and that is not the same fact as a conversation that fits: left
            # to `needed` alone, the record showed nothing at all where an
            # oversized request was about to go out. It still goes --
            # `estimate_tokens` bounds from above, so over the estimate is not
            # necessarily over the endpoint's own count -- and if the provider
            # refuses it, this is the entry that says why.
            if outcome.overflow:
                events.append({
                    "type": "overflow",
                    "estimated_tokens": {"before": outcome.before, "available": outcome.available},
                    "context_window": context_window,
                    "why": "the request is over the window and no legal cut is above the kept tail",
                })
            return None
        facts = compaction.Facts(
            goal=request.informal_claim,
            # The frozen statement, verbatim. The cut discards the task message
            # that carried it, and a model left with the prose alone writes
            # candidates that cannot type-check against the declaration it was
            # told not to change.
            declaration=request.declaration,
            assumptions=[],
            proved=[found["proof"]] if found["proof"] else [],
            open_declarations=[
                f"{item.get('keyword')} at line {item.get('line')} of the retained skeleton"
                for item in sketched["holes"]
            ],
            # And the skeleton itself, which on this surface lives nowhere else:
            # a batch run writes no workspace file for a partial development, so
            # a cut that dropped the `sketch_proof` message would leave the
            # model unable to continue from the development the record says
            # Hardy is holding.
            development=str(sketched["proof"] or ""),
            names=[],
            attempts=compaction.failed_attempts(events),
            next_steps=[],
            modules=list(request.imports),
        )
        summarised = compaction.summarize(facts)
        settled = compaction.plan(
            messages,
            context_window=context_window,
            reserve_tokens=compaction.RESERVE_TOKENS,
            keep_tokens=compaction.RECENT_TOKENS,
            summary_tokens=compaction.estimate_tokens([compaction.Message("user", text=summarised.render())]),
            overhead_tokens=overhead,
            output_tokens=int(getattr(runtime, "output_limit", None) or 0),
        )
        if not settled.needed:
            return None
        events.append({
            "type": "compaction",
            "summarized_messages": settled.cut,
            "kept_messages": len(messages) - settled.cut,
            "estimated_tokens": {
                "before": settled.before,
                "after": settled.after,
                "available": settled.available,
                "fits": settled.fits,
            },
            "context_window": context_window,
            "text": summarised.render(),
        })
        return compaction.compacted(messages, settled.cut, summarised)

    runtime = make_runtime(system_prompt=system, specs=TOOLS, dispatch=dispatch, cwd=output_dir, observe=observe,
                           max_turns=max_turns, wall_seconds=max(remaining, 0.0))
    # Offered rather than passed in, and only to a runtime that says it can
    # take one -- the same rule `MathematicsSession._build` follows, and for
    # the same reason: handing a compactor to a backend that would drop it
    # would leave the record claiming a compaction Hardy never got to make.
    attach = getattr(runtime, "attach_compactor", None)
    compacted = attach is not None
    if attach is not None:
        attach(compact)

    def gate(messages: Sequence[compaction.Message]) -> str | None:
        """Whether there is anything left for a provider turn to do.

        A batch run ends at its first kept submission: `writeup.md` is built
        from the artifacts rather than from the model's closing remarks, so
        every turn after the one that submitted buys nothing and is billed for
        anyway. Worse than wasted, it is not inert -- the tools stay live, and
        a second accepted submission would replace the proof the artifact was
        already going to carry.

        Offered rather than assumed, like the compactor above: a backend whose
        SDK owns the loop has no point at which to ask, and a run there simply
        continues as it did before.
        """
        if found["result"] is None:
            return None
        return "a submission was accepted and audited; the run has its result and needs no further turn"

    gated = getattr(runtime, "attach_gate", None)
    if gated is not None:
        gated(gate)
    # Whether a provider was asked anything at all. A ladder that closed the
    # statement means Hardy declined to spend a turn, which is a fact about the
    # run and not an absence of one -- and it is what keeps the ledger below
    # from billing an exchange that never happened.
    closed_by_ladder = bool(found["result"] and ladder["closed_by"])
    asked = not closed_by_ladder and remaining > 0
    if closed_by_ladder:
        # Named by stage, because there are now two kinds of decline and only
        # this one belongs to the ladder. `hardy accept --recorded` reads the
        # closers block against the declines beside it, and a mid-exchange
        # decline -- the run has its proof and needs no further turn -- was
        # being counted as evidence that a ladder ran, which refused an
        # otherwise verified record.
        events.append({
            "type": "declined_turn",
            "stage": "closers",
            "why": f"closed by `{ladder['closed_by']}` before a model turn was spent",
        })
    elif not asked:
        # Out of time before the model was asked anything. Reported as the
        # limit it is, not as a model that submitted nothing.
        #
        # And blaming whatever actually spent it. `--wall-seconds 0` with no
        # ladder reached this branch too, and the record then said the closers
        # had used the whole budget beside a `closers` block saying they were
        # disabled -- a false sentence, and one `hardy accept --recorded` reads
        # as evidence that the provider was deliberately unasked.
        closed.set()
        reason = "wall_clock_limit"
        detail = (
            "the closers used the whole wall-clock budget; no model turn was spent"
            if ladder["enabled"]
            else "the wall-clock budget was gone before a model turn could be spent"
        )
        events.append({"type": "limit", "limit": "wall_seconds", "detail": detail})
    try:
        if asked:
            runtime.ask(task)
    except TurnLimitReached as error:
        # The bound the caller asked for, reached as asked. Recording it as a
        # provider failure would misreport an expected partial result.
        reason = "turn_limit"
        events.append({"type": "limit", "limit": "max_turns", "detail": str(error)})
    except TimeoutError as error:
        # Running out of time is not a provider fault, and the terminal reason is
        # what an experiment is read by.
        closed.set()
        reason = "wall_clock_limit"
        events.append({"type": "error", "error": f"{type(error).__name__}: {error}"})
    except Exception as error:
        reason = "runtime_error"
        events.append({"type": "error", "error": f"{type(error).__name__}: {error}"})
    closed.set()
    elapsed = time.monotonic() - start
    final, proof = found["result"], found["proof"]
    # A proof accepted inside the budget is verified even if the exchange then
    # ran out of time; one accepted outside it was never recorded above.
    if final:
        reason = "verified"
    elif reason == "completed":
        # A proof that elaborated and was then refused is not "no proof submitted".
        reason = "axioms_rejected" if refused["axioms"] else "no_proof_submitted"
    # The SDK ran the loop, so its own count is the only honest one; counting
    # tool calls here would be a different number wearing the same name. It
    # arrives with the SDK's final result, which a run the wall clock cut short
    # never receives -- so the count stays unset, and `None` says that. It used
    # to be flattened to 0, which reads as a measurement: a real 5-second run
    # recorded `"turns": 0` beside a trajectory holding the tool call the model
    # had already made.
    # A run where nobody was asked has a count, and it is zero. `None` means
    # "nobody said", which is the honest answer only when a provider was asked
    # and did not report -- and a never-built loop reports nothing whether it
    # ran or not, so reading the runtime here turned a measurement Hardy made
    # into an unknown. The ledger already recorded it as zero exchanges; this
    # is the same fact in the field a turn-based comparison reads.
    turns = getattr(runtime, "turns", None) if asked else 0

    # Hardy sent one exchange. A provider that never reported on it -- which is
    # what a run the wall clock cut short looks like, since the report rides on
    # the SDK's final result -- did not thereby make it free. So the exchange is
    # counted with nothing stated about it, rather than left out of the ledger
    # and rendered as a run that spent nothing.
    # A run that never asked a provider anything spent nothing, and says so.
    # A run that asked and got no report is a different thing: it is counted
    # with everything about it unstated, because a provider may well have
    # billed for what it did before the wall clock cut the exchange short.
    spent = spend["total"] if spend["total"].turns else (Usage().record({}) if asked else Usage())

    # What the audit decided: the verdict that verified the run, or failing that
    # the record of what refused it -- which distinguishes an audit that ran and
    # found something from one that could not be established. "not audited" is
    # reserved for a run where no submission ever reached the audit at all.
    verdict = found["verdict"]
    formal = "kernel verified" if final else "not formalized"
    informal = "not assessed"
    axioms = verdict.as_dict() if final and verdict is not None else refused["record"] or {"status": "not audited"}
    # Only when nothing was verified. A verified run's sketch is a step on the
    # way to the proof beside it, and recording both would invite a reader to
    # weigh them against each other; an unverified run's sketch is the only
    # thing it has to show.
    sketch = None if final else (dict(sketched) if sketched["proof"] else None)
    result = RunResult(reason, formal, informal, proof if final else None, final.output if final else "No hole-free proof was accepted.", axioms, turns, spent.summary(), [WARNING], toolchain, sketch)
    if final and proof:
        (output_dir / "proof.lean").write_text(lean.source(proof, audit=True), encoding="utf-8")
    # The grade and what it rests on, together. "kernel verified" beside a
    # silent axiom section is the claim this gate exists to stop being made.
    stands_on = (
        ", ".join(verdict.reports[0].axioms) or "none"
        if final and verdict is not None and verdict.reports
        else audit.summarise(axioms)
    )
    writeup = f"# Hardy proof result\n\n## Claim\n\n{request.informal_claim}\n\n## Exact Lean statement\n\n```lean\n{request.declaration}\n```\n\n## Grades\n\n- Formalization: **{formal}**\n- Informal completeness: **{informal}**\n- Audited axioms: {stands_on}\n\n## Toolchain\n\n{describe_toolchain(toolchain)}\n\n## Limits\n\n{WARNING}\n"
    if not final:
        writeup += f"\nNo completed artifact was produced. Terminal reason: `{reason}`.\n"
    if sketch is not None:
        writeup += sketch_section(sketch)
    (output_dir / "writeup.md").write_text(writeup, encoding="utf-8")
    _write_json(output_dir / "trajectory.json", {"schema_version": 1, **provenance(runtime), "lean_command": list(lean.lean_command), "lean_project": str(lean.project) if lean.project else None, "toolchain": toolchain, "request": {"declaration": request.declaration, "informal_claim": request.informal_claim, "imports": list(request.imports)}, "limits": _limits(runtime, max_turns, wall_seconds, elapsed, context_window, compacted), "usage": spent.summary(), "sketch": sketch, "closers": ladder, "events": events, "terminal_reason": reason})
    _write_json(output_dir / "result.json", result.as_dict())
    return result
