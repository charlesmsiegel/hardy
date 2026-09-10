"""Assumption admission, search evidence, and quarantine decisions.

Search belongs to one request and is spent even when a later gate refuses it.
A paper disagreement quarantines the name; an unavailable reader mints nothing.
Approval is recorded before the gated module save and rolled back on refusal.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from hardy.formal.workspace import (
    module_name,
    safe_relative,
)
from hardy.foundation.files import LayoutError
from hardy.foundation.locking import LockTimeout
from hardy.foundation.values import ToolResult
from hardy.literature import statements as assume_module
from hardy.literature.arxiv import ArxivError
from hardy.literature.bibliography import BibliographyError
from hardy.workflows.admission import (
    AdmissionPolicy,
    AdmissionRequest,
    FaithfulnessDisposition,
    ProbeOperations,
    SearchEvidence,
    SourceEvidence,
    TrustRequestKind,
    assumption_shape,
)

_GLOBAL_REQUEST = AdmissionRequest(TrustRequestKind.GLOBAL_ASSUMPTION)
_PAPER_REQUEST = AdmissionRequest(TrustRequestKind.PAPER_STATEMENT_ASSUMPTION)


@dataclass(frozen=True)
class AdmissionOperations:
    probes: ProbeOperations
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
        self.policy = AdmissionPolicy()
        self.search = SearchEvidence()
        self._rejected: dict[str, list[str]] = {}

    def attempted_inspection(self) -> None:
        self.search.attempted_inspection()

    def rejected(self, name: str, statement: str) -> None:
        self._rejected.setdefault(name, []).append(statement)

    def _consume_search_evidence(self) -> None:
        """Only called after the search gate passes, on every subsequent exit."""
        self.search.consume()


    def _note_inspected(self, names: list[str], output: str) -> None:
        self.search.note_inspected(names, output)


    def _request_assumption(
        self, proposal: dict[str, str], *, search_available: bool, operations: AdmissionOperations,
        admission_request: AdmissionRequest = _GLOBAL_REQUEST,
    ) -> ToolResult:
        refusal = self.policy.request_refusal(admission_request)
        if refusal:
            return ToolResult(False, refusal)
        refusal = self.search.refusal(available=search_available)
        if refusal:
            return ToolResult(False, refusal)
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
            decision = self.policy.check_global(
                proposal["formal_name"], proposal["lean_statement"], operations.probes
            )
            if decision.refusal:
                return ToolResult(False, decision.refusal)
            declaration = f"axiom {proposal['formal_name']} : {proposal['lean_statement'].strip()}"
            proposal["checked"] = decision.checked
            proposal["goal"] = operations.goal()
            proposal["searched"] = self.search.description()
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


    def _assume_statement(
        self, request: dict[str, str], *, search_available: bool, operations: AdmissionOperations,
        admission_request: AdmissionRequest = _PAPER_REQUEST,
    ) -> ToolResult:
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
        refusal = self.policy.request_refusal(admission_request)
        if refusal:
            return ToolResult(False, refusal)
        try:
            record, reading = operations.paper_statements(request["paper_id"])
        except (ArxivError, assume_module.AssumeError) as error:
            return ToolResult(False, str(error))
        wanted, source_evidence, refusal = self.policy.paper_statement(record, reading, request["statement"])
        if refusal:
            return ToolResult(False, refusal)
        kind = request.get("kind") or "statement"
        short = request["formal_name"].strip()
        refusal = self.policy.paper_name_refusal(short, kind)
        if refusal:
            return ToolResult(False, refusal)
        refusal = self.search.refusal(available=search_available, paper=True)
        if refusal:
            return ToolResult(False, refusal)
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
            return self._mint(request, record, entry, wanted, namespace, qualified, kind, operations=operations, source_evidence=source_evidence)
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
        *, operations: AdmissionOperations, source_evidence: SourceEvidence | None = None,
    ) -> ToolResult:
        statement = request["lean_statement"].strip()
        decision = self.policy.check_paper(
            request["formal_name"], qualified, statement, kind, operations.probes
        )
        if decision.refusal:
            return ToolResult(False, decision.refusal)
        checked = decision.checked
        reached, agreed, divergences = operations.faithfulness(request, record, wanted, qualified)
        disposition = self.policy.faithfulness(reached=reached, agreed=agreed)
        if disposition == FaithfulnessDisposition.UNAVAILABLE:
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
        if disposition == FaithfulnessDisposition.QUARANTINE:
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
        if source_evidence is not None:
            durable["paper"]["source_artifact"] = source_evidence.artifact.model_dump(mode="json")
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
        return assumption_shape(formal_name, lean_statement)


