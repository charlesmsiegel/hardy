"""Rendering of recorded batch results; never launches a run."""
from __future__ import annotations

from typing import Any


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


