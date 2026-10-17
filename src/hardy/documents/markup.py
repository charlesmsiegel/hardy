"""The HTML an export page is assembled from, escaped on the way in.

Two kinds of text reach the page, and they are escaped differently. Free text
-- the conversation, prose, record fields -- passes through `redact` first, so
a credential matching a known shape never reaches the file. Audited source --
Lean the kernel graded, the declarations an approval rests on -- is escaped
and nothing else: `verbatim` says why no rule is safe to run over it.
"""

from __future__ import annotations

import html
from collections.abc import Iterable, Sequence

from hardy.documents.redaction import redact


def escape(value: object) -> str:
    """Free text, redacted and then HTML-escaped."""
    return html.escape(redact(str(value)), quote=True)


def verbatim(value: object) -> str:
    """Escape an audited artifact. NOTHING is rewritten -- see below.

    An earlier version of this kept the token-shape rules on, reasoning that
    `sk-...` or `Bearer ...` could not occur in valid Lean by accident. It can:
    `theorem t : "Bearer abc123" = "Bearer abc123" := rfl` is a proposition
    about a string literal, and rewriting it exports a DIFFERENT proposition
    from the one the kernel checked. There is no shape a formal artifact
    cannot contain, so there is no rule that is safe to run over one.

    What guards the reader instead is the page's own header, which says the
    redaction is a filter and not a proof, and the fact that this text is the
    workspace's own audited tree rather than anything Hardy pasted into it.
    """
    return html.escape(str(value), quote=True)


def block(text: str) -> str:
    """Free text as a `<pre>`, redacted."""
    return f"<pre>{escape(text)}</pre>"


def source_block(text: str) -> str:
    """A `<pre>` for audited code, kept byte-for-byte. See `redact`."""
    return f"<pre>{verbatim(text)}</pre>"


def rows(pairs: Iterable[tuple[str, object]]) -> str:
    """Label and value pairs as a definition list; nothing at all when there are none."""
    body = "".join(
        f"<dt>{escape(key)}</dt><dd>{escape(value)}</dd>" for key, value in pairs
    )
    return f"<dl>{body}</dl>" if body else ""


def listing(items: Sequence[object], empty: str) -> str:
    """Items as a bulleted list, or the sentence `empty` says when there are none."""
    if not items:
        return f"<p>{escape(empty)}</p>"
    return "<ul>" + "".join(f"<li>{escape(item)}</li>" for item in items) + "</ul>"
