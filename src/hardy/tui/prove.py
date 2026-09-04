"""The staged `prove` workflow, run from inside a live session (#85).

Exploration and staged proving are the same activity at different levels of
commitment, and making the second one a separate program dropped the
conversational context that motivated the claim exactly when it became useful.
What kept them apart was mechanical rather than principled: the staged path
asks its questions through `input()` and `print()`, and a blocking read of the
terminal from inside a running application takes the keyboard out from under it.

So this is a `Terminal` that asks through the `Ui` port instead. It subclasses
`ConsoleTerminal` deliberately and overrides almost nothing: every sentence the
staged workflow says -- the unsandboxed-execution warning above all -- has to be
the same sentence whichever surface a user reached it through, and two copies of
that text is one edit away from being two different warnings.

The approval question is the exception, and it is one because the terminal has a
real selector: three rows a user arrows through beat a word they have to spell
correctly, and `ConsoleTerminal`'s re-ask loop exists only because a typed line
can be wrong.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from ..cli import ConsoleTerminal
from .ports import BlockingUi, Choice

APPROVALS = (
    Choice("approve", "Approve", "the statement says what I meant"),
    Choice("revise", "Revise", "describe the interpretation change I need"),
    Choice("cancel", "Cancel", "stop the run here"),
)


class UiTerminal(ConsoleTerminal):
    """`ConsoleTerminal`, speaking through a `Ui` rather than through stdio.

    Takes the BLOCKING face of the `Ui` (`Ui.from_thread`), because the workflow
    runs on a worker thread: it is synchronous from end to end and would freeze
    the event loop that has to read the answers if it ran on it.
    """

    def __init__(self, ui: BlockingUi, abandoned: Callable[[], bool] | None = None) -> None:
        self._ui = ui
        # Set only around the revision prompt. See `revision_text`.
        self._abandoning_cancels = False
        # Whether the run has already been told to stop. The terminal cannot
        # see the workflow otherwise, and there is one window where it has to:
        # see `revision_text`.
        self._abandoned = abandoned or (lambda: False)
        super().__init__(input_fn=self._read, output=self._write)

    def watch(self, workflow: Any) -> None:
        """Learn which run this terminal is asking questions on behalf of."""
        cancelled = getattr(workflow, "_cancelled", None)
        if cancelled is not None:
            self._abandoned = cancelled.is_set

    def _write(self, text: str) -> None:
        # Split rather than passed whole: the staged wording opens sections with
        # "\n", and a `Ui` writes lines.
        for line in str(text).split("\n"):
            self._ui.write(line)

    def _read(self, prompt: str) -> str:
        # "" rather than None, because every caller of this in the staged
        # workflow calls `.strip()` on it. An Esc at the acknowledgement prompt
        # therefore reads as "not I UNDERSTAND", which is the refusal it means.
        answer = self._ui.ask_line(prompt)
        if answer is None and self._abandoning_cancels:
            raise KeyboardInterrupt
        return answer or ""

    def revision_text(self) -> str:
        """The one prompt where an abandoned read is not an empty answer.

        An empty revision is a revision: the workflow loops and opens another
        billable formalization turn with nothing new to say. On the console
        this prompt is a bare `input()`, so Ctrl+C in it raises and the run
        finalizes as a cancellation; swallowing Esc into "" here made the two
        surfaces disagree about what abandoning the prompt means.

        The wording stays in `ConsoleTerminal`. Every sentence the workflow
        says has to be the same sentence on both surfaces, so this flags the
        read rather than asking the question a second time.
        """
        # Before posting it, as well as after it returns. Esc can land between
        # the workflow's approval check and this prompt attaching: the shell's
        # stopper sets the run's flag and consumes the key, and then this opened
        # a blocking read anyway -- so a user who had already walked away was
        # asked for a second input, and the workflow could not observe its own
        # flag until they gave one.
        if self._abandoned():
            raise KeyboardInterrupt
        self._abandoning_cancels = True
        try:
            answer = super().revision_text()
        finally:
            self._abandoning_cancels = False
        # And again on the way out. The check above and the posting of the
        # prompt are two operations, so an Esc can still land between them: the
        # outer shell consumes it, and the prompt opens on a run that is
        # already abandoned. What must not follow is the run acting on the
        # answer -- a revision restarts the loop and opens another billable
        # formalization turn. The residual cost is that the user is asked a
        # question they had already walked away from and has to dismiss it;
        # closing that would need the stopper to cancel a prompt already posted
        # to the loop, which is machinery this does not have.
        if self._abandoned():
            raise KeyboardInterrupt
        return answer

    def choose_approval(self) -> str:
        picked = self._ui.choose(
            "The formalization above",
            APPROVALS,
            subtitle="Approving freezes this exact Lean statement as the claim.",
        )
        # An abandoned selector cancels the RUN, which is not the same as
        # picking the "Cancel" row. That row is a judgement -- the user read
        # the formalization and refused it, recorded as USER_REJECTION -- and
        # Esc is not: it is walking away from the question, recorded as
        # USER_CANCELLATION. Returning "cancel" for both put a judgement in the
        # manifest that nobody made.
        #
        # Raised rather than signalled, because the selector runs its own
        # nested application: it consumes the key itself, so the shell's stop
        # binding never fires and the workflow's cancellation flag is never
        # set. `KeyboardInterrupt` is the path the workflow already has for
        # exactly this, and on the console this prompt is an `input()` where
        # Ctrl+C raises it anyway.
        if picked is None:
            raise KeyboardInterrupt
        return picked.value


def problem_slug(claim: str) -> str:
    """The run directory's name, from the claim. Shared with `hardy prove`."""
    return re.sub(r"[^a-z0-9]+", "-", claim.lower()).strip("-")[:48] or "theorem"


def run(
    config: Any,
    claim: str,
    terminal: Any,
    *,
    backend: str = "claude",
    ready: Any = None,
) -> Any:
    """One staged run, on this session's live configuration. Blocking, by design.

    `config` is `State.config` and not what the process launched with: `/model`
    moves the former and not the latter, and a `/prove` that ran on the model
    the user has already moved off would be the whole reason this exists,
    inverted.

    `ready` is handed the workflow the moment it exists, before anything is
    run. That is how Esc reaches it: this whole function runs on a worker, and
    cancelling the caller's `await` cannot raise inside a worker -- so the
    caller needs the object itself to call `cancel()` on. Published before the
    first stage rather than returned at the end, because the run is exactly
    what there is to cancel.
    """
    from ..cli import build_prove_workflow
    from ..workflow import ProveRequest

    workflow = build_prove_workflow(config, config.config_path, backend=backend)
    # The terminal asks the workflow whether the run is still wanted. Attached
    # here rather than at construction because the workflow does not exist
    # until now, and the terminal is built by the caller.
    attach = getattr(terminal, "watch", None)
    if attach is not None:
        attach(workflow)
    if ready is not None:
        ready(workflow)
    return workflow.run(
        ProveRequest(
            text=claim, model=str(config.model), problem_slug=problem_slug(claim)
        ),
        terminal,
    )
