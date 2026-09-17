"""Read-only view of a problem's saved checkpoints.

Checkpoints live under `<root>/.hardy/checkpoints/<slug>/` -- outside the
problem directory `workspace.confine()` guards, the same reason `runs.py` is
its own module rather than a function in `workspace.py`. Nothing here reads a
byte itself: `hardy.workflows.checkpoints.list_checkpoints` already does the
whole job -- find every whole checkpoint, skip a partial one mid-write, refuse
a symlinked entry -- so this module's entire job is reshaping its
`Checkpoint` dataclasses into JSON, exactly the way `record.py` and `runs.py`
reshape `pydantic`/dataclass models rather than recomputing what they already
proved.

Restoring is destructive and is not served from here at all: the design's
confirm card names `/checkpoint restore <id>`, and every field that command
needs -- the `id` alone, the same way `web/src/pages/Jobs.jsx`'s own confirm
cards build `/jobs pause ${selected.id}` from a row's `id` and nothing else
the server has to spell out -- is already in the row below. There is no
mutation route here, and none should be added.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from hardy.workflows.checkpoints import list_checkpoints
from hardy.workflows.layout import Layout


def checkpoints(paths: Layout) -> dict[str, Any]:
    """Every whole checkpoint of `paths.slug`, oldest first -- `list_checkpoints`'s own order.

    A fresh project, or one nobody has ever checkpointed, has no
    `.hardy/checkpoints/<slug>/` directory at all: an honest empty list,
    matching `ledger_list`'s and `runs()`'s own fresh-project case, not an
    error. `label` is carried alongside the raw fields rather than left for
    the browser to reconstruct: it is a `Checkpoint` property
    (`workflows/checkpoints.py:62-64`), not a stored field, and building it
    twice -- once here, once in the client -- is exactly the drift `family`
    and `edge_style` are kept server-side to avoid elsewhere in this package.
    """
    return {
        "checkpoints": [{**asdict(checkpoint), "label": checkpoint.label} for checkpoint in list_checkpoints(paths)],
    }
