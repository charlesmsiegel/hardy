"""Human approval adapters shared by CLI and interactive terminal.

A terminal renders workflow decisions and collects explicit approval; it
does not construct the workflows or interpret proof evidence itself.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from hardy.app.tui.ports import Choice
from hardy.workflows.batch import WARNING


def confirm_assumption(ui: Any) -> Callable[[dict[str, Any]], bool]:
    """The axiom gate, reached from an SDK tool thread.

    `MathematicsSession` calls this synchronously from inside a tool call
    (`chat.py`'s `_tool`, itself dispatched from whichever thread the SDK ran
    the tool on), so it must not touch the terminal application directly --
    it goes through `ui.from_thread`, which marshals the prompt onto the
    event loop and blocks this thread for the answer. A decline still
    hard-gates the assumption: `picked is None` (Esc, or the prompt could not
    be shown at all) is treated exactly like an explicit "No", never as
    approval. Every non-approval path returns `False`, including an
    unexpected exception from `blocking` itself -- a bug in the prompting
    path must not be able to fail this gate open.
    """

    def confirm(proposal: dict[str, Any]) -> bool:
        blocking = ui.from_thread
        try:
            # The goal first, and the absence of one shown rather than hidden.
            # Nobody can judge whether an assumption is too strong without the
            # assignment in front of them, and Hardy does not judge it for them:
            # the session that approved `no_simple_nonabelian_composite_orders`
            # -- the assignment itself, for 28 of the orders -- spent 170
            # seconds on a well-argued paragraph with nothing beside it.
            goal = proposal.get("goal") or ""
            blocking.write("Goal, as you stated it:", style="normal")
            blocking.write(f"  {goal}" if goal else "  not set -- /goal sets one")
            blocking.write("Hardy wants to introduce an assumption:", style="warning")
            blocking.write(f"  Informal: {proposal['informal_statement']}")
            # `keyword` rather than a literal `axiom`: an assumed definition
            # is written as `opaque`, and this is the single line a person
            # reads before deciding. Defaulted for a caller that predates the
            # field -- `request_assumption` produces only axioms.
            keyword = proposal.get("keyword") or "axiom"
            blocking.write(
                f"  Lean: {keyword} {proposal['formal_name']} : {proposal['lean_statement']}"
            )
            identity = proposal.get("declaration_identity") or {}
            if identity.get("lean_reported_type"):
                blocking.write(f"  Lean reported: {identity['lean_reported_type']}")
            blocking.write(f"  Source: {proposal['source']}")
            blocking.write(f"  Reason: {proposal['reason']}")
            blocking.write(f"  Checked: {proposal.get('checked', 'not checked')}")
            # A name refused or declined earlier this session is shown beside
            # the new statement, so a weakening between the two is seen rather
            # than approved sight unseen.
            if proposal.get("previous"):
                blocking.write(f"  Previously requested as: {proposal['previous']}", style="warning")
            # What has been searched since the last request -- proof the
            # `reason` given is not free text alone, since the search-first
            # gate that required it is otherwise invisible at the prompt.
            if proposal.get("searched"):
                blocking.write(f"  Searched since the last request: {', '.join(proposal['searched'])}")
            picked = blocking.choose(
                f"Approve the assumption {proposal['formal_name']}?",
                [Choice("no", "No, decline it"), Choice("yes", "Yes, approve it")],
                current=0,
            )
        except Exception:  # noqa: BLE001 - every non-approval path is a decline, never a crash
            return False
        return picked is not None and picked.value == "yes"

    return confirm


class ConsoleTerminal:
    """The staged workflow's conversation with the person running it."""

    def __init__(
        self,
        *,
        input_fn: Callable[[str], str] = input,
        output: Callable[[str], Any] = print,
    ) -> None:
        self._input = input_fn
        self._output = output

    def acknowledge_unsafe_execution(self) -> bool:
        # A typed acknowledgement rather than a keystroke: running unsandboxed
        # generated code is worth one deliberate sentence.
        # Every kind of generated code this run can execute is named. A staged
        # run has no chat banner, so this sentence is the only place a user
        # learns that computer algebra cells run here too.
        self._output(
            f"WARNING: {WARNING} LaTeX and computer algebra cells are also "
            "executed without isolation."
        )
        return self._input("Type I UNDERSTAND to continue: ").strip() == "I UNDERSTAND"

    def show_formalization(self, proposal: Any, elaboration: Any) -> None:
        self._output("\nProposed interpretation")
        self._output(proposal.restatement)
        for label, values in (
            ("Domains", proposal.domains),
            ("Quantifiers", proposal.quantifiers),
            ("Assumptions", proposal.assumptions),
            ("Interpretation choices", proposal.interpretation_choices),
        ):
            self._output(f"{label}: {', '.join(values) if values else 'none'}")
        binders = f" {proposal.binders.strip()}" if proposal.binders.strip() else ""
        self._output(f"theorem {proposal.theorem_name}{binders} : {proposal.proposition}")
        # A statement that elaborates has been type-checked, not proved. Saying
        # so here is the whole point of showing it.
        self._output(
            "statement elaborates; this is not proof evidence"
            if elaboration.success
            else "statement does not elaborate"
        )

    def choose_approval(self) -> str:
        while True:
            choice = self._input("Choose approve, revise, or cancel: ").strip().lower()
            if choice in {"approve", "revise", "cancel"}:
                return choice
            self._output("Please enter approve, revise, or cancel.")

    def revision_text(self) -> str:
        return self._input("Describe the required interpretation change: ").strip()

    def show_faithfulness(self, verdict: Any) -> None:
        """Say what the independent reader said, agreement included.

        Printed on a pass as well as a halt: a gate whose only visible output
        is a refusal leaves a user unable to tell a run that was checked from
        one where the check never ran.
        """
        self._output(
            f"\nIndependent faithfulness review by {verdict.reviewer_model}: "
            f"{verdict.outcome.value}"
        )
        if verdict.review is None:
            self._output(verdict.detail)
        else:
            for divergence in verdict.review.divergences:
                self._output(f"  divergence: {divergence}")
            if verdict.review.notes:
                self._output(f"  notes: {verdict.review.notes}")
        # An unreachable reader halts the run exactly as a refusal does, so it
        # gets the same sentence: the user is owed the reason the run ended,
        # not only the reason the reader gave when there was one.
        if verdict.review is None:
            # Nothing was read, so there is nothing to restate. Telling the
            # user to reword a claim no reader ever saw sends them to fix
            # something that was never the problem.
            self._output(
                "The run stops here. No reader assessed the translation; "
                "try again, or set faithfulness_model to a reachable model."
            )
        elif not verdict.agreed:
            self._output(
                "The run stops here. Restate the claim, or ask for a "
                "formalization that says what you meant."
            )

    def show_result(self, manifest: Any) -> None:
        self._output("\nHardy result")
        self._output(f"Run ID: {manifest.run_id}")
        self._output(f"Phase: {manifest.phase.value}")
        self._output(f"Formal: {manifest.grades.formal.value}")
        review = manifest.grades.faithfulness_review
        checked = (
            f" ({review.outcome.value} by {review.reviewer_model})"
            if review is not None
            else " (no independent review)"
        )
        self._output(f"Faithfulness: {manifest.grades.faithfulness.value}{checked}")
        self._output(f"Informal: {manifest.grades.informal.value}")
        self._output(f"Document: {manifest.grades.document.value}")
        for gap in manifest.grades.known_gaps:
            self._output(f"Known gap: {gap}")
        if manifest.terminal_reason is not None:
            self._output(f"Terminal reason: {manifest.terminal_reason.value}")
