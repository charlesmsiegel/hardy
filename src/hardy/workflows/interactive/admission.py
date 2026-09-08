"""Assumption admission, search evidence, and quarantine decisions.

Search belongs to one request and is spent even when a later gate refuses it.
A paper disagreement quarantines the name; an unavailable reader mints nothing.
Approval is recorded before the gated module save and rolled back on refusal.
"""
from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from ... import assume as assume_module
from ... import refute
from ...arxiv import ArxivError
from ...bibliography import BibliographyError
from ...layout import LayoutError
from ...models import ToolResult
from ...storage import LockTimeout
from ...workspace import ANY_NAME, COMMAND, module_name, safe_relative, unreadable_assumptions

@dataclass(frozen=True)
class AdmissionOperations:
    shape: Callable[[str, str], str | None]
    probe: Callable[[str], tuple[str | None, str]]
    vacuity: Callable[[str], str]
    refutation: Callable[[str], refute.Verdict]
    faithfulness: Callable[..., tuple[bool, bool, tuple[str, ...]]]
    confirm: Callable[[dict[str, Any]], bool]
    goal: Callable[[], str]
    event: Callable[[dict[str, Any]], int]
    assumptions: Callable[[], list[dict[str, Any]]]
    admit: Callable[[dict[str, Any], dict[str, str]], bool]
    revoke: Callable[[dict[str, Any], dict[str, str]], None]
    locate: Callable[[str, str], None]
    quarantine: Callable[[dict[str, Any]], None]
    persist: Callable[[], None]
    paper_statements: Callable[[str], Any]
    cite: Callable[[Any], Any]
    write_module: Callable[..., str | ToolResult]

class AssumptionAdmission:
    def __init__(self):
        self._inspected_since_request = False
        self._searched_since_request: list[str] = []
        self._inspect_attempts_since_request = 0
        self._rejected: dict[str, list[str]] = {}

    def attempted_inspection(self) -> None:
        self._inspect_attempts_since_request += 1

    def rejected(self, name: str, statement: str) -> None:
        self._rejected.setdefault(name, []).append(statement)

    def _consume_search_evidence(self) -> None:
        """Spend the search evidence gathered for the request just handled.

        Shared by every exit out of `_request_assumption` once the
        search-first gate has passed, because the evidence a request
        consults belongs to *that* request, not to whichever one asks next.
        A request the search gate itself refused never reaches here -- it
        looked at nothing, so there is nothing to spend -- but a request the
        gate let through and `_assumption_shape` or `_assumption_probe` then
        refused has: a human's `inspect_declarations` call was already
        looked at to decide the refusal, and letting it sit unconsumed let a
        next request under a different `formal_name` walk through the
        search gate on evidence that was never about it.
        """
        self._inspected_since_request = False
        self._searched_since_request = []
        self._inspect_attempts_since_request = 0


    def _note_inspected(self, names: list[str], output: str) -> None:
        """Remember what a completed inspection asked, and what it found."""
        resolved: set[str] = set()
        try:
            payload = json.loads(output[output.index("{"):])
            resolved = {item["name"] for item in payload.get("resolved", [])}
        except (ValueError, KeyError, TypeError):
            # A hint line with no JSON after it, or JSON in an unexpected
            # shape -- either way, nothing was resolved as far as this can
            # tell, and every name below is recorded as not found.
            pass
        for name in names:
            self._searched_since_request.append(f"{name} {'✓' if name in resolved else '✗'}")
        self._inspected_since_request = True


    def _request_assumption(self, proposal: dict[str, str], *, search_available: bool, operations: AdmissionOperations) -> ToolResult:
        if search_available and self._inspect_attempts_since_request == 0:
            # Three axioms were approved on a failing run with the reason
            # "Mathlib does not expose this" and nothing had been searched
            # for. When search is available, a request is refused until
            # `inspect_declarations` has actually been *tried* since the last
            # request -- the reason given in `reason` is free text and proves
            # nothing on its own. Gated on attempts, not completions: a
            # machine whose Lean cannot finish an inspection still tried, and
            # refusing it forever with a message claiming nothing was even
            # attempted is the failure this fix exists to close. Below, once
            # an attempt has been made, the request goes through even if none
            # of them finished -- `searched` tells the human that state.
            return ToolResult(
                False,
                "no `inspect_declarations` has been run since the last assumption "
                "request. Look for the result before assuming it: pass several "
                "candidate spellings and let Lean say which exist.",
            )
        # Both gates below run before `confirm`. Nobody should be asked to
        # approve a statement Hardy has not read, and nobody should be asked
        # at all about one that could never be declared or that Lean proves
        # itself. Everything from here on is wrapped in `try`/`finally`: a
        # request that gets this far has passed the search gate, so evidence
        # was spent looking at it -- whether `_assumption_shape` or
        # `_assumption_probe` goes on to refuse it, or a human declines it,
        # or it is approved, the search that justified even asking is gone
        # either way, and the next request -- even under a different
        # `formal_name` -- owes a fresh one. Only the search gate itself,
        # above, returns without spending anything: it refused before any of
        # this request's evidence was looked at.
        try:
            refusal = operations.shape(proposal["formal_name"], proposal["lean_statement"])
            if refusal is not None:
                return ToolResult(False, refusal)
            # Built once and reused: the text elaborated, the text approved, and
            # the text the model is told to write are one string, which is the
            # whole point.
            declaration = f"axiom {proposal['formal_name']} : {proposal['lean_statement'].strip()}"
            refusal, caveat = operations.probe(declaration)
            if refusal is not None:
                return ToolResult(False, refusal)
            # Carried to the prompt rather than swallowed: a human approving an
            # unchecked statement is owed the word "unchecked", and one whose
            # hypotheses turn out to be doing no work is owed that too. Only run
            # when the first probe actually elaborated: a caveat already means
            # Lean was unreachable or unreadable, and a second full Lean run
            # would spend up to PROBE_SECONDS on an answer `or caveat` discards.
            warning = operations.vacuity(proposal["lean_statement"]) if not caveat else ""
            # The elaboration sentence always leads: it is the one fact that is
            # true of every request that reaches this line, so a vacuity warning
            # or a strip-refused note is appended to it rather than displacing
            # it -- finding #5 of the second brutal review, where a stripper
            # refusal used to replace the only sentence saying Lean had read the
            # statement at all.
            elaborated = "Lean elaborated this statement and could not prove it."
            proposal["checked"] = caveat or (f"{elaborated} {warning}" if warning else elaborated)
            proposal["goal"] = operations.goal()
            if self._inspect_attempts_since_request and not self._inspected_since_request:
                # Every attempt since the last request was stopped before it
                # could report anything -- `_searched_since_request` is empty for
                # the honest reason that nothing to put in it ever finished, not
                # because nothing was tried. Say which is true, in the human's
                # own count, rather than leave the list looking untouched.
                proposal["searched"] = [
                    f"{self._inspect_attempts_since_request} inspection(s) attempted "
                    "since the last request, none finished"
                ]
            else:
                searched = list(self._searched_since_request)
                if len(searched) > 20:
                    # A session that inspects in large batches across many
                    # requests can pile up a `searched` list a human is never
                    # going to read in full. Show the count and the most recent
                    # 20 -- what was just asked, not the whole session's
                    # history -- rather than let the field grow without bound.
                    searched = [f"{len(searched)} names inspected; last 20:"] + searched[-20:]
                proposal["searched"] = searched
            # A name refused or declined earlier this session gets its last
            # statement shown beside the new one: `sylow_unique_normal` lost a
            # conjunct between a refused request and an approved one, unseen,
            # because nothing put the two statements side by side.
            earlier = self._rejected.get(proposal["formal_name"])
            if earlier:
                proposal["previous"] = earlier[-1]
            # `checked`, `searched` and `previous` reach `confirm` but never
            # `record` below, so without this nothing durable ever says what
            # evidence the human was actually shown when they approved (or
            # refused) an axiom -- a nit from the second brutal review.
            operations.event({
                "type": "assumption_prompt",
                "formal_name": proposal["formal_name"],
                "checked": proposal["checked"],
                "searched": proposal["searched"],
                "previous": proposal.get("previous", ""),
            })
            if not operations.confirm(proposal):
                return ToolResult(False, "The user declined this assumption. Do not use it.")
            # `checked`, `goal`, `searched` and `previous` describe this one
            # request, not the assumption, and have no business in the durable
            # record.
            record = {key: value for key, value in proposal.items() if key not in {"checked", "goal", "searched", "previous"}}
            record["status"] = "user-approved"
            # Except the goal, kept under a name that says what it is. `goal`
            # is a singleton that `/goal` overwrites, so an export rendered
            # after the goal moved showed the NEW goal above an axiom approved
            # for the old one -- an approval attributed to a question it was
            # never asked about. Hardy sets `proposal["goal"]` from its own
            # state rather than taking the model's word for it, so this is the
            # workspace's goal at the moment the user said yes.
            # Written unconditionally, empty string included. Dropping the key
            # when no goal was set made "the user approved this with no goal in
            # front of them" indistinguishable from "this record predates the
            # field" -- and the renderers say different things about those. The
            # key's presence is the evidence that the question was asked.
            record["goal_at_approval"] = str(proposal.get("goal") or "").strip()
            # When a human said yes, in UTC. Additive, so `schema_version`
            # stays 2 for the reason `goal` gives: a record written before
            # this existed simply lacks the key, and every reader of it says
            # "date not recorded" rather than inventing one. An export meant
            # to leave the machine has to be able to say who approved what and
            # when (#105), and the transcript's own `assumption_prompt` event
            # is not enough on its own -- it records the asking, not the answer.
            record["approved_at"] = datetime.now(UTC).isoformat()
            mapping = {"formal_name": record["formal_name"], "latex_name": record["latex_name"], "description": record["informal_statement"]}
            if operations.admit(record, mapping):
                operations.persist()
            return ToolResult(
                True,
                f"User approved. Declare exactly `{declaration}`, disclose source "
                f"`{proposal['source']}`, and state it in the writeup's \\appendix -- both "
                f"that Lean line, verbatim, and \\label{{{proposal['latex_name']}}} on the "
                "prose statement of what was assumed. Nothing resting on it can be "
                "reported until the appendix carries both.",
            )
        finally:
            self._consume_search_evidence()


    def _assume_statement(self, request: dict[str, str], *, search_available: bool, operations: AdmissionOperations) -> ToolResult:
        """Mint one paper statement as an axiom, or say why not.

        The order of the gates is the design. Everything Hardy can establish
        by itself runs before a human is asked anything: that the paper really
        makes this statement, that the Lean parses as an axiom at all, that
        Lean does not prove it outright (a theorem, not an assumption), that
        Lean does not prove its *negation* (false, and would make everything
        provable), and that an independent reader accepts the Lean as saying
        what the paper says. Only then is anyone asked to approve it.

        A reader that disputes the translation quarantines it rather than
        warning about it: the name is recorded, visible, and refused by the
        save gate. An unfaithfully formalised assumption is worse than no
        assumption, because it lets Hardy prove what the paper never claimed
        while naming the paper as its source.
        """
        try:
            record, reading = operations.paper_statements(request["paper_id"])
        except (ArxivError, assume_module.AssumeError) as error:
            return ToolResult(False, str(error))
        statements = reading.statements
        wanted = assume_module.find(statements, request["statement"])
        if wanted is None:
            # A truncated reading is said so rather than reported as the
            # paper's silence: the inventory stopping is Hardy's bound, and
            # "the paper makes no statement called that" is a claim about the
            # paper that Hardy has not established.
            cut = (
                f" The reading stopped at the first {assume_module.MAX_STATEMENTS} statements, "
                "so this may be one it did not reach."
                if reading.truncated
                else ""
            )
            return ToolResult(
                False,
                f"{record.arxiv_id} makes no statement called {request['statement']!r}.{cut} "
                f"list_statements names them: {[item.ref for item in statements][:20]}",
            )
        kind = request.get("kind") or "statement"
        if kind not in ("statement", "constant"):
            return ToolResult(
                False, f"kind must be 'statement' or 'constant', not {kind!r}"
            )
        # One component, refused here rather than at the save. The module is
        # regenerated from the record with only the leaf of the recorded name,
        # so a dotted name passed every gate, spent a human approval, and then
        # failed the save under a Lean name nobody proposed -- advising
        # `request_assumption`, which cannot write `Papers/` either. The
        # namespace is Hardy's to choose; the caller names the axiom in it.
        short = request["formal_name"].strip()
        # One component: `ANY_NAME` admits the guillemet escape, and `«a.b»`
        # carries a dot through it. The module is regenerated with
        # `rsplit(".", 1)[-1]`, so the human approved `Papers.<key>.«a.b»`
        # and the file was written with `axiom b»`.
        if "." in short or not re.fullmatch(ANY_NAME, short):
            return ToolResult(
                False,
                f"formal_name must be a single Lean identifier, not {short!r}: the "
                "namespace is the paper's cite key and Hardy writes it. Pass the axiom's "
                "own name with no dots in it.",
            )
        if search_available and self._inspect_attempts_since_request == 0:
            # The same gate `request_assumption` lives by, for the same
            # reason: a paper stating a result is not evidence that Mathlib
            # lacks it, and an axiom for something already formalised is a
            # widening of the trust base bought for nothing.
            return ToolResult(
                False,
                "no `inspect_declarations` has been run since the last assumption request. "
                "Look for the result in Mathlib before assuming it from the paper.",
            )
        # Spent from here on, whatever the answer, for the reason
        # `_request_assumption` spends it: an inspection was evidence about
        # *this* request, and leaving it standing on a refusal let the next
        # request under a different name walk through the gate on evidence
        # that was never about it. The `finally` sat around the mint alone, so
        # the three refusals between here and there returned it intact.
        try:
            try:
                entry, _ = operations.cite(record)
            except (BibliographyError, LockTimeout, OSError, LayoutError) as error:
                return ToolResult(
                    False, f"the paper could not be recorded in the bibliography: {error}"
                )
            try:
                namespace = assume_module.namespace_for(entry.key)
            except assume_module.AssumeError as error:
                return ToolResult(False, str(error))
            qualified = f"{namespace}.{short}"
            if any(item["formal_name"] == qualified for item in operations.assumptions()):
                # Refused here rather than at the save, and before the human is
                # asked. The module is regenerated from the record, so a second
                # mint under a recorded name renders that axiom twice and the save
                # is refused -- after an approval was spent, with a message
                # blaming the approval flow for a rendering fault. Nothing revises
                # a minted assumption in place (`Papers/` is closed to `save_lean`
                # and `delete_file`), so a different name is the only honest move.
                return ToolResult(
                    False,
                    f"`{qualified}` is already an approved assumption from this paper. An "
                    "assumption cannot be restated under a name that is already minted: ask "
                    "for the corrected statement under a new formal_name.",
                )
            return self._mint(request, record, entry, wanted, namespace, qualified, kind, operations=operations)
        finally:
            self._consume_search_evidence()


    def _mint(
        self,
        request: dict[str, str],
        record: Any,
        entry: Any,
        wanted: Any,
        namespace: str,
        qualified: str,
        kind: str,
        *, operations: AdmissionOperations,
    ) -> ToolResult:
        statement = request["lean_statement"].strip()
        # The shape gate runs for both kinds. What it asks -- is this a type
        # rather than a declaration, is it one line -- is as true of a
        # constant's type as of a proposition, and skipping it let a
        # `lean_statement` carrying its own `axiom` reach the human and the
        # generated file, with only the final elaboration behind it.
        refusal = operations.shape(request["formal_name"], statement)
        if refusal is not None:
            return ToolResult(False, refusal)
        # The *probes* are what a constant has nothing to say to: they ask
        # whether Lean proves a proposition, and a type is not one. Its own
        # risk is put to the human instead, and recorded as added trust.
        if kind == "statement":
            probe, caveat = operations.probe(f"axiom {qualified} : {statement}")
            if probe is not None:
                return ToolResult(False, probe)
            verdict = operations.refutation(statement)
            if verdict.refuted:
                return ToolResult(False, refute.describe(verdict, statement))
            # Both caveats travel, not the first of them. A human approving
            # an axiom is owed every fact about what was and was not checked,
            # and reporting only the elaboration's silence would leave them
            # believing the counterexample search had come back clean.
            checked = " ".join(
                part
                for part in (
                    caveat
                    or "Lean elaborated this statement and could not prove it.",
                    (
                        f"The counterexample search was not conclusive: {verdict.caveat}."
                        if verdict.caveat
                        else "No counterexample was found by the cheap refutation probes."
                    ),
                )
                if part
            )
        else:
            checked = (
                "An opaque constant is not elaborated as a proposition, so nothing was "
                "proved or refuted about it. It asserts that something with this type "
                "exists, which is trust beyond assuming a statement."
            )
        reached, agreed, divergences = operations.faithfulness(request, record, wanted, qualified)
        if not reached:
            # Refused, and nothing recorded against the name. Hardy did not
            # establish anything about this translation, so the record must
            # not read as though it did.
            operations.event(
                {
                    "type": "assumption_review_unavailable",
                    "formal_name": qualified,
                    "reason": list(divergences),
                }
            )
            return ToolResult(
                False,
                "the independent reader that has to accept this Lean before it may be "
                f"assumed could not be reached: {list(divergences)}. Nothing was minted and "
                "nothing is recorded against this name -- try again.",
            )
        if not agreed:
            self._quarantine(request, record, entry, wanted, qualified, kind, divergences, operations=operations)
            return ToolResult(
                False,
                "an independent reader would not accept this Lean as saying what the paper "
                f"says, so it is quarantined rather than assumed: {list(divergences)}. It is "
                "recorded in the workspace and cannot be declared. Restate it and try again.",
            )
        proposal = {
            "formal_name": qualified,
            "lean_statement": statement,
            "latex_name": request.get("latex_name") or f"assumption:{request['formal_name']}",
            "informal_statement": request["informal_statement"],
            "source": f"arXiv:{record.arxiv_id} ({wanted.ref})",
            "reason": request["reason"],
            "checked": checked,
            "goal": operations.goal(),
            "paper_text": wanted.text,
            "paper_title": record.title,
            "cite_key": entry.key,
            "kind": kind,
            # The keyword the file will actually carry, so the one line a
            # person reads before deciding is the declaration Hardy writes.
            # It printed `axiom` for what it mints as `opaque`, which is the
            # weaker of the two and not what was being approved.
            "keyword": "opaque" if kind == "constant" else "axiom",
        }
        operations.event(
            {
                "type": "assumption_prompt",
                "formal_name": qualified,
                "checked": checked,
                "paper": record.arxiv_id,
                "statement": wanted.ref,
            }
        )
        if not operations.confirm(proposal):
            return ToolResult(False, "The user declined this assumption. Do not use it.")
        minted = assume_module.Minted(
            formal_name=request["formal_name"].strip(),
            lean_statement=statement,
            informal_statement=request["informal_statement"],
            kind=kind,
            ref=wanted.ref,
            heading=wanted.heading,
            paper_text=wanted.text,
        )
        durable = {
            "formal_name": qualified,
            "lean_statement": statement,
            "latex_name": proposal["latex_name"],
            "informal_statement": request["informal_statement"],
            "source": proposal["source"],
            "reason": request["reason"],
            "status": "user-approved",
            "kind": kind,
            "paper": {
                "arxiv_id": record.arxiv_id,
                "cite_key": entry.key,
                "ref": wanted.ref,
                "heading": wanted.heading,
                # The paper's own sentence, kept because the module is
                # regenerated from this record rather than from the object
                # minted here. Without it every generated docstring named the
                # statement and quoted nothing, which is the one thing a
                # reader checking an assumption needs.
                "text": wanted.text,
                "module": "",
            },
            # Both written for the reason `_request_assumption` writes them:
            # every renderer of an approval reads these, and their absence is
            # a claim of its own. Without them the export said "this approval
            # predates the field" of one seconds old, and that the goal shown
            # may not be the one it was given for -- when the proposal above
            # had just shown that very goal.
            "goal_at_approval": str(proposal.get("goal") or "").strip(),
            "approved_at": datetime.now(UTC).isoformat(),
        }
        mapping = {
            "formal_name": qualified,
            "latex_name": proposal["latex_name"],
            "description": request["informal_statement"],
        }
        fresh = operations.admit(durable, mapping)
        if fresh:
            # Recorded BEFORE the module is written, because the module is
            # saved through the ordinary path and that path refuses an axiom
            # no human approved -- which this one now has. Rolled back below
            # if the save fails, so a refused write never leaves an approval
            # standing for an axiom that is not in the tree.
            operations.persist()
        written = operations.write_module(record, entry, minted, minted_already=fresh)
        if isinstance(written, ToolResult):
            if fresh:
                operations.revoke(durable, mapping)
                operations.persist()
            return written
        if fresh:
            operations.locate(qualified, written)
            operations.persist()
        return ToolResult(
            True,
            f"User approved. `{qualified}` is declared in {written} and cited as "
            f"\\cite{{{entry.key}}}. Import it with `import {module_name(safe_relative(written))}`"
            f", disclose it in the writeup's \\appendix under \\label{{{proposal['latex_name']}}} "
            "with the exact Lean line, and remember that anything resting on it is verified "
            "only modulo this paper.",
        )


    def _quarantine(
        self,
        request: dict[str, str],
        record: Any,
        entry: Any,
        wanted: Any,
        qualified: str,
        kind: str,
        divergences: tuple[str, ...],
        *, operations: AdmissionOperations,
    ) -> None:
        """Record a refused translation where it stays visible and unusable.

        Not a warning beside an admitted axiom: the entry below is what
        `_final_gates` reads to refuse the name outright. The difference
        between a rule and a suggestion is that the rule refuses.
        """
        operations.quarantine(
            {
                "formal_name": qualified,
                "lean_statement": request["lean_statement"].strip(),
                "informal_statement": request["informal_statement"],
                "kind": kind,
                "divergences": list(divergences),
                "paper": {
                    "arxiv_id": record.arxiv_id,
                    "cite_key": entry.key,
                    "ref": wanted.ref,
                },
            }
        )
        operations.persist()
        operations.event({"type": "assumption_quarantined", "formal_name": qualified,
                      "divergences": list(divergences)})


    def _assumption_shape(self, formal_name: str, lean_statement: str) -> str | None:
        """Why this could never be declared, or None.

        `request_assumption` used to accept anything and wrap it in
        `axiom NAME : ...`, so it could approve text `save_lean` would refuse
        forever. That is not hypothetical: a session was told to declare
        `axiom cyclic_of_prime_order : axiom cyclic_of_prime_order (G : Type*)
        ... : ...` -- a double header nothing can parse -- and spent ten turns
        discovering there was no spelling that satisfied both ends. Matching the
        approval required binders the parser refuses; satisfying the parser
        produced a statement that no longer matched the approval.

        So both ends now ask the same code about the same string. `COMMAND` is
        what recognises a line opening a declaration, and an axiom's statement
        is a type, never a command. `unreadable_assumptions` is what `save_lean`
        itself calls.

        A statement is also one line. `True\\naxiom extra : False` is two
        declarations and `ASSUMPTION` reads both happily, so without this the
        request round-trips and an approval granted for the first carries the
        second. Approved statements are stored whitespace-collapsed anyway, so
        refusing a newline costs nothing a caller needed.

        Not sufficient, and not meant to be. A binder-only statement --
        `(G : Type*) : True` -- matches neither check, because
        `axiom f : (G : Type*) : True` parses by taking everything after the
        first colon. It is not valid Lean and only elaboration can say so, which
        is what `_assumption_probe` is for. `opaque`, and any declaration
        keyword `COMMAND` does not list, land there too.
        """
        statement = lean_statement.strip()
        if "\n" in statement or "\r" in statement:
            return (
                "a statement is one line and one type. More than one line can carry a "
                "second declaration, which an approval of the first would not cover. "
                "Collapse it to one line."
            )
        if COMMAND.match(statement):
            return (
                f"a statement may not itself be a declaration, and `{statement[:60]}` "
                f"opens one. Pass only the statement -- the type after the colon -- and "
                f"Hardy writes `axiom {formal_name} :` in front of it. Binders belong "
                f"inside the statement as `forall`, not before the colon."
            )
        declaration = f"axiom {formal_name} : {statement}"
        if unreadable_assumptions(declaration):
            return (
                f"`{declaration[:80]}` cannot be read as `axiom NAME : STATEMENT`, so "
                f"save_lean could never accept it. An assumption carries no binders and "
                f"no universe parameters."
            )
        return None


