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

    def __init__(self, ui: BlockingUi) -> None:
        self._ui = ui
        super().__init__(input_fn=self._read, output=self._write)

    def _write(self, text: str) -> None:
        # Split rather than passed whole: the staged wording opens sections with
        # "\n", and a `Ui` writes lines.
        for line in str(text).split("\n"):
            self._ui.write(line)

    def _read(self, prompt: str) -> str:
        # "" rather than None, because every caller of this in the staged
        # workflow calls `.strip()` on it. An Esc at the acknowledgement prompt
        # therefore reads as "not I UNDERSTAND", which is the refusal it means.
        return self._ui.ask_line(prompt) or ""

    def choose_approval(self) -> str:
        picked = self._ui.choose(
            "The formalization above",
            APPROVALS,
            subtitle="Approving freezes this exact Lean statement as the claim.",
        )
        # Cancelling the selector cancels the run. There is no third state here:
        # the workflow is waiting on one of three words, and treating an
        # abandoned prompt as "approve" would freeze a claim nobody read.
        return picked.value if picked is not None else "cancel"


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
    if ready is not None:
        ready(workflow)
    return workflow.run(
        ProveRequest(
            text=claim, model=str(config.model), problem_slug=problem_slug(claim)
        ),
        terminal,
    )
