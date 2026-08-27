"""One rule for cutting an observation down to what a model can be handed.

Everything Hardy returns to a model is bounded -- Lean output keeps its tail
(`lean.LeanTools._observe`), a CAS value too large to return spills to an
artifact (`cas_tools`), a listing stopped returning file bodies
(`chat.MathematicsSession._workspace_listing`) -- and every one of those
bounds was re-derived where it was needed. Which end to keep, what to count,
and how to tell the model it is looking at a fragment are not per-call-site
questions, and answering them per call site is how `read_file` came to be the
one tool result with no bound at all.

Two independent limits, whichever is reached first. Lines, because a
thousand-line file costs a model's attention whatever its bytes say; bytes,
because one generated line can be a megabyte on its own and a line count would
not notice. Bytes rather than characters: every other budget in Hardy --
`model_observation_bytes`, `cas_output_bytes`, `ProcessSpec.max_output_bytes`
-- is a byte budget, and a limit that means something different here would be
a limit nobody can reason about across two tools.

Which end to keep is the caller's, because it is a fact about the observation
and not about the cutting. A file read wants the beginning; a failing
command's output wants the end, where the error is.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

# A file read that reaches either of these is long enough that the model
# should be choosing which part it wants. The byte figure matches the default
# `model_observation_bytes` in `domain.Limits`; the line figure is there for
# the file that is 20,000 short lines and well under the byte cap.
DEFAULT_LINE_LIMIT = 2_000
DEFAULT_BYTE_LIMIT = 32 * 1024


@dataclass(frozen=True)
class Truncation:
    """A cut observation, and enough about the cut to describe it honestly.

    Structured rather than a bare string, because a caller that is handed only
    the text has to guess at what it is missing -- and every caller guessing
    separately is the state this module exists to end. `total_lines` and
    `total_bytes` describe the whole input; `output_lines` and `output_bytes`
    describe what came back.
    """

    text: str
    # Which limit did the cutting, or None when the whole thing fit.
    truncated_by: Literal["lines", "bytes"] | None
    total_lines: int
    total_bytes: int
    output_lines: int
    output_bytes: int
    # Both 1-based and against the whole input. `next_line` is where a
    # follow-up read should start, or None when this reached the end.
    first_line: int
    next_line: int | None

    @property
    def truncated(self) -> bool:
        return self.truncated_by is not None

    @property
    def summary(self) -> str:
        """What was returned and what was left, in one line for the model.

        Deliberately says nothing about how to ask for the rest: that is the
        name of a tool and the name of its argument, which this module does
        not know. The caller appends it.

        A window that was not cut still gets a line, because a caller that
        asked for one from `start_line` is holding a fragment either way and
        has to be told it is looking at the end of the file rather than at all
        of it.
        """
        last = self.first_line + self.output_lines - 1
        window = (
            f"lines {self.first_line}-{last} of {self.total_lines} "
            f"({self.output_bytes} of {self.total_bytes} bytes)"
        )
        if not self.truncated:
            return f"{window}; to the end of the file"
        return f"{window}; truncated by {self.truncated_by}"


def truncate(
    text: str,
    *,
    keep: Literal["head", "tail"] = "head",
    line_limit: int | None = DEFAULT_LINE_LIMIT,
    byte_limit: int | None = DEFAULT_BYTE_LIMIT,
    start_line: int = 1,
) -> Truncation:
    """Cut `text` to fit both limits, on line boundaries, keeping one end.

    `start_line` skips ahead before the limits are applied, so a caller can
    page through a long file; the reported line numbers stay against the whole
    text either way. It is 1-based because that is how every editor, compiler
    and reader numbers a file, and a model asked to name a line will name that
    one.

    A partial line is never returned, with one exception that cannot be
    avoided: a single line longer than `byte_limit` is cut at a UTF-8
    character boundary rather than dropped, because returning nothing at all
    for a file that is one enormous line is not a more honest answer. That
    line cannot then be asked for whole, which `summary` reports as bytes
    dropped.
    """
    lines = text.splitlines(keepends=True)
    total_lines = len(lines)
    total_bytes = len(text.encode("utf-8"))
    begin = max(start_line - 1, 0)
    window = lines[begin:]
    truncated_by: Literal["lines", "bytes"] | None = None
    if line_limit is not None and len(window) > line_limit:
        truncated_by = "lines"
        window = window[:line_limit] if keep == "head" else window[-line_limit:]
    if byte_limit is not None:
        fitted = _fit(window, byte_limit, keep)
        if fitted != window:
            # Reported over a line cut already made: whichever limit produced
            # the smaller window is the one that actually bound this call, and
            # a caller told "lines" while bytes did the work would raise the
            # wrong limit when the answer came back short.
            truncated_by = "bytes"
        window = fitted
    kept = "".join(window)
    # Where the returned text sits in the whole. For `keep="tail"` the drop
    # was at the front, so the first line moves and nothing follows.
    dropped_before = begin + (len(lines[begin:]) - len(window) if keep == "tail" else 0)
    first_line = dropped_before + 1
    last_line = dropped_before + len(window)
    return Truncation(
        text=kept,
        truncated_by=truncated_by,
        total_lines=total_lines,
        total_bytes=total_bytes,
        output_lines=len(window),
        output_bytes=len(kept.encode("utf-8")),
        first_line=first_line if window else max(begin + 1, 1),
        next_line=last_line + 1 if last_line < total_lines else None,
    )


def _fit(lines: list[str], limit: int, keep: Literal["head", "tail"]) -> list[str]:
    """The most whole lines from one end of `lines` that fit in `limit` bytes.

    At least one line always comes back, cut on a character boundary when that
    line alone does not fit -- see `truncate` for why nothing is the wrong
    answer there.
    """
    ordered = lines if keep == "head" else list(reversed(lines))
    fitted: list[str] = []
    spent = 0
    for line in ordered:
        cost = len(line.encode("utf-8"))
        if spent + cost > limit:
            break
        fitted.append(line)
        spent += cost
    if not fitted and ordered:
        fitted = [_clip(ordered[0], limit, keep)]
    return fitted if keep == "head" else list(reversed(fitted))


def _clip(line: str, limit: int, keep: Literal["head", "tail"]) -> str:
    """One over-long line, cut to `limit` bytes without splitting a character.

    `errors="ignore"` drops the character the cut landed inside rather than
    emitting a replacement, so what comes back is text the file really
    contains -- shorter than the file, never different from it.
    """
    encoded = line.encode("utf-8")
    if len(encoded) <= limit:
        return line
    clipped = encoded[:limit] if keep == "head" else encoded[-limit:]
    return clipped.decode("utf-8", errors="ignore")
