"""Read-only views for the browser, each derived from the workspace's own artifacts.

Split by what a view reads, not by which tab shows it: `session` reads the live
session, `workspace` reads files under the problem directory, and `record` reads
the project ledger. A view that needs two of those belongs to the one it reads
first.

Every function in every submodule is pure: given a session (for the
conversational panels) or a problem directory (for the artifact panels), it
returns a JSON-serializable value and nothing else -- no HTTP, no caching, no
mutation. A later task wires each one behind a GET endpoint; this package owns
only the shape of the answer.
"""

from hardy.app.web.panels.record import STATEMENT_LIMIT, graph
from hardy.app.web.panels.session import jobs, summary, transcript, tree
from hardy.app.web.panels.workspace import (
    CAS_SKIPPED,
    CELL_TEXT_LIMIT,
    CELLS_LIMIT,
    TEXT_LIMIT,
    cas_cells,
    confine,
    file_text,
    files,
    pdf_bytes,
    sources,
)

__all__ = [
    "CAS_SKIPPED", "CELLS_LIMIT", "CELL_TEXT_LIMIT", "STATEMENT_LIMIT", "TEXT_LIMIT",
    "cas_cells", "confine", "file_text", "files", "graph", "jobs", "pdf_bytes",
    "sources", "summary", "transcript", "tree",
]
