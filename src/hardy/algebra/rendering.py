"""Bounded descriptions of what a cell leaves behind, for the kernel's replies.

Three things the driver says about values a cell produced, none of which may
cost the kernel its life: reply fields cut to one shared byte budget (`clip`,
`clip_jointly`), a repr that is never built larger than that budget
(`bounded_repr`), and a fingerprint of the whole namespace (`state_digest`)
that recovery and export compare across processes. Every value here comes from
untrusted code, so every step is bounded and every failure is an answer.

This runs inside the kernel child beside `driver.py`, and like it imports
nothing from Hardy: only the standard library.
"""

from __future__ import annotations

import hashlib
import itertools
import re
import sys


def clip(text: str, limit: int) -> tuple[str, bool]:
    """Truncate here, at the source, rather than in the parent's pipe reader.

    A reply is length-prefixed, so a parent that stopped reading at a byte cap
    could never assemble the frame it was promised — it would wait out the cell
    timeout and lose the kernel over an answer that was merely large. Clipping
    before serialising keeps every envelope parseable.

    `limit` is a count of bytes, because that is what the parent reserves
    retention for and what `cas_output_bytes` promises. Counting characters
    instead let a cell of astral-plane text carry four times the budget past a
    reader sized for one. The cut lands on a UTF-8 boundary: a truncated
    trailing character is dropped rather than emitted as a replacement.

    Encoded with `backslashreplace`, not plain `utf-8`. A lone UTF-16
    surrogate (U+D800-U+DFFF) is not valid UTF-8 and cannot be encoded
    strictly at all -- a `Symbol` with a badly decoded name, or an exception
    message built from bad bytes, raised `UnicodeEncodeError` right here, with
    nothing catching it, and took the whole kernel down (issue #195). The
    escaped text is returned even when it already fit: a caller that trusted
    this function's word that a string is safe to encode must actually get
    one back, not the original that still carries the surrogate, which would
    then fail exactly the same way one step later, at the reply serialiser.
    """
    encoded = text.encode("utf-8", "backslashreplace")
    if len(encoded) <= limit:
        return encoded.decode("utf-8"), False
    return encoded[:limit].decode("utf-8", errors="ignore"), True


def clip_jointly(fields: dict[str, str], limit: int) -> tuple[dict[str, str], bool]:
    """Fit stdout, stderr and value_repr into one shared byte budget.

    One budget rather than three, because the parent reserves retention for one
    and the frame carries all three. A per-field cap of `limit` each meant a
    cell writing that much to every field built a payload three times the size
    the reader was sized for — a legal cell whose frame could never assemble,
    which reads as a timeout and costs the whole session's state.

    The share is max-min fair: smallest field first, each allowed an equal cut
    of what is left, and whatever a small field does not use passes to the
    others. So a lone large value still gets nearly the whole budget, and three
    large ones each get a third instead of one starving the rest.

    Sized with `backslashreplace`, like `clip` itself: a lone surrogate in any
    field raised `UnicodeEncodeError` right here, sorting fields before a
    single one of them had been clipped -- so the crash was not confined to
    the final serialisation step, and catching only the last one would have
    left this one standing.
    """
    truncated = False
    clipped: dict[str, str] = {}
    remaining = limit
    ordered = sorted(
        fields.items(), key=lambda item: len(item[1].encode("utf-8", "backslashreplace"))
    )
    for index, (name, text) in enumerate(ordered):
        share = remaining // (len(ordered) - index)
        clipped[name], cut = clip(text, share)
        truncated = truncated or cut
        remaining -= len(clipped[name].encode("utf-8", "backslashreplace"))
    return clipped, truncated


# Containers whose length is known without walking them, and whose repr is at
# least one byte per element -- so their size can be bounded from below without
# rendering them, which is what lets an over-large one be diverted before its
# repr is built.
_SIZED = (list, tuple, set, frozenset, dict)
# How deep the size estimate walks before it stops. A bound is needed at all
# because a container can hold itself, and the estimate must not become the
# unbounded traversal it exists to prevent. Past it the answer is "too large",
# never "small": a cutoff that returned zero read seven nested singleton lists
# around a multi-gigabyte string as nothing to worry about and handed them to
# plain `repr`, which is the allocation this whole path exists to avoid.
_ESTIMATE_DEPTH = 20
_BRACKETS = {
    list: ("[", "]"),
    tuple: ("(", ")"),
    set: ("{", "}"),
    frozenset: ("frozenset({", "})"),
    dict: ("{", "}"),
}


def _lower_bound(value: object, limit: int, depth: int = 0) -> int:
    """A cheap lower bound on `len(repr(value))`, capped at `limit + 1`.

    Cheap because it never renders anything and stops the moment the budget is
    already exceeded. A lower bound because anything it cannot measure counts
    as zero: the point is to *prove* a value too large before building its
    repr, never to guess that one is small enough to matter.

    An item count alone was not enough. A one-element list holding a
    multi-gigabyte string has one item and a multi-gigabyte repr, so deciding
    by `len(value)` sent it down the plain `repr` path and built the whole
    thing -- with `MemoryError` caught only after the allocation had already
    been attempted, by which point the OS may have taken the kernel instead.
    """
    if isinstance(value, (str, bytes, bytearray)):
        return min(len(value), limit + 1)
    if depth >= _ESTIMATE_DEPTH:
        # Deeper than this is not measured, and unmeasured is not small.
        return limit + 1
    if type(value) not in _SIZED:  # noqa: E721 -- exact type only
        # Genuinely unknown: an object's repr cannot be sized without running
        # it. Zero, so a value with a perfectly ordinary repr still takes the
        # exact path -- this is the residual the docstring names, not a cutoff.
        return 0
    # The separators alone, which is why a container of more than `limit`
    # elements is over budget before a single one of them is looked at.
    total = len(value)
    if total > limit:
        return limit + 1
    for key, entry in _members(value):
        total += _lower_bound(key, limit, depth + 1)
        if entry is not None:
            total += _lower_bound(entry, limit, depth + 1)
        if total > limit:
            return limit + 1
    return total


def _members(value):
    """A container's contents as (item, None) pairs, or a dict's as (key, value)."""
    if isinstance(value, dict):
        return value.items()
    return ((item, None) for item in value)


def bounded_repr(value: object, limit: int) -> tuple[str, bool]:
    """`repr(value)`, without building a gigantic one to then throw away.

    Exact whenever the answer would have fitted: the fast path is plain
    `repr`, so an ordinary value's repr is byte-identical to what an
    interpreter would print. Only a value `_lower_bound` can *prove* too large
    is rendered head-first instead, and it says so in the text.

    What this does not bound is a `__repr__` a cell wrote itself. There is no
    way to ask an object how long its repr will be without running it, and the
    honest answer is that the process, not this function, is the bound there;
    `MemoryError` is caught and reported as the cell failing rather than
    allowed to take the kernel down.

    The second element of the pair is whether the text is a truncation. It is
    not only for the reader: `state_digest` refuses to fingerprint a namespace
    any part of which it could only see a prefix of, because two values sharing
    a prefix would otherwise fingerprint alike.
    """
    try:
        if _lower_bound(value, limit) <= limit:
            return repr(value), False
        if isinstance(value, (str, bytes, bytearray)):
            head, _ = clip(repr(value[:limit]), limit)
            return f"{head} … <{type(value).__name__} of length {len(value)}>", True
        if type(value) in _SIZED:  # noqa: E721 -- exact type only
            return _head_of_container(value, limit), True
        return repr(value), False
    except MemoryError:
        return f"<{type(value).__name__}: its repr did not fit in memory>", True
    except BaseException:  # a cell's own __repr__ is allowed to be broken
        return f"<{type(value).__name__}: repr() raised {sys.exc_info()[0].__name__}>", True


def _head_of_container(value, limit: int) -> str:
    """Render the leading elements of an over-long container and say so."""
    opening, closing = _BRACKETS[type(value)]
    items = value.items() if isinstance(value, dict) else value
    pieces: list[str] = []
    size = 0
    for item in itertools.islice(items, limit):
        if isinstance(value, dict):
            key, entry = item
            piece = f"{bounded_repr(key, limit)[0]}: {bounded_repr(entry, limit)[0]}"
        else:
            piece = bounded_repr(item, limit)[0]
        piece, _ = clip(piece, limit)
        pieces.append(piece)
        size += len(piece) + 2
        if size >= limit:
            break
    body, _ = clip(", ".join(pieces), limit)
    return f"{opening}{body}, … <{len(value)} items>{closing}"


# CPython's default repr -- ` at 0xADDR>` -- says the type and the address and
# nothing else. The address is not state, but neither is anything else in that
# string: `Box()` with `payload = 1` and `Box()` with `payload = 2` render
# identically, so a namespace holding one cannot be told from a namespace
# holding the other. Normalising the address away made those two agree and
# called the rebuild faithful, which is the failure the digest exists to catch.
#
# So an opaque repr is not normalised, it is *refused*: the namespace has no
# fingerprint, the parent reads that as "not compared", and the rebuild is
# reported unverified. The cost is real and is the honest one -- a session that
# binds a plain instance or a `def`-defined function cannot have its state
# checked across a restart, and now says so instead of claiming otherwise.
_OPAQUE = re.compile(r" at 0x[0-9a-fA-F]+>")
def _record(digest, name: object, kind: bytes, payload: bytes) -> None:
    """Add one entry, tagged by what it is.

    The tag is what keeps the kinds apart. Writing a deletion as the bare
    bytes `deleted` was indistinguishable from a name whose value *rendered*
    as `deleted` -- `Symbol("deleted")` does -- so a namespace missing a name
    hashed exactly like one holding that symbol under it. Every entry carries
    its kind, and no kind's bytes can be produced by another.
    """
    _open(digest, name, kind)
    digest.update(payload)
    digest.update(b"\0")


def _open(digest, name: object, kind: bytes) -> None:
    """Begin an entry whose payload is fed in piece by piece.

    `_walk` streams rather than returning what it built. The node budget
    bounds how many objects it visits and not how much they render to, so a
    single container of a few thousand distinct near-cap strings accumulated
    the whole fingerprint in memory before hashing any of it -- 1.5 GiB under
    a 256 KiB cap, in a kernel a session's whole state depends on staying
    alive. Fed to the hash as it is produced, nothing but one leaf's repr is
    held at a time."""
    digest.update(b"\0")
    digest.update(kind)
    digest.update(b"\0")
    digest.update(repr(name).encode("utf-8", errors="backslashreplace"))
    digest.update(b"\0")


def _emit(emit, budget: list[int], text: str) -> bool:
    """Feed one token to the hash, or refuse because the payload is too large.

    A node budget bounds how many objects the walk visits; it does not bound
    what they render to, and every leaf may render to `limit` bytes. So the
    total is bounded here as well, and exceeding it refuses the digest for the
    same reason exceeding any other bound does: the alternative is a
    fingerprint of part of a namespace presented as one of the whole.

    `emit is None` is the numbering-only walk, which produces no payload and
    so cannot exhaust this.
    """
    if emit is None:
        return True
    payload = text.encode("utf-8", errors="backslashreplace")
    budget[1] -= len(payload)
    if budget[1] < 0:
        return False
    emit(payload)
    return True


def _walk(
    value: object,
    seen: dict[int, int],
    limit: int,
    budget: list[int],
    emit=None,
    depth: int = 0,
) -> bool:
    """Fingerprint the object *graph* rooted at `value`, or refuse.

    Hashing a rendering was never going to be enough, and each round of it
    failed one level deeper: two names for one object, then a name bound to
    something nested inside another, then two members of the same container
    sharing one object. `[x, x]` and `[[], []]` render identically whatever
    is done to the rendering, and `a[0].append(1); a[1]` tells them apart.

    So the structure is walked rather than printed. Every object is numbered
    on first sight and emitted as `@n` on every later one, so a reference is
    part of the fingerprint wherever it occurs and at whatever depth. Only a
    leaf -- something that is not a container Hardy can look inside -- falls
    back to its repr, and the same two refusals apply there as before: a
    prefix and a default repr are not fingerprints, and either gives up the
    whole digest.

    Bounded in nodes and in payload bytes, because a container can hold itself
    and this must not become the unbounded traversal `_lower_bound` exists to
    prevent. Exceeding either refuses rather than truncating: a partial walk
    would be a fingerprint of part of the graph presented as one of the whole.

    `emit=None` numbers the graph without describing it, for a name whose
    value is not itself hashed -- the preamble's -- but whose position other
    names may refer to. Numbering it is what lets a name bound to a preamble
    object hash as a reference to it rather than by a repr that is usually
    `<function symbols at 0x...>` and would refuse the digest outright.
    """
    marker = seen.get(id(value))
    if marker is not None:
        return _emit(emit, budget, f"@{marker}")
    if budget[0] <= 0 or depth >= _ESTIMATE_DEPTH:
        return False
    seen[id(value)] = len(seen)
    budget[0] -= 1
    if type(value) in _SIZED:  # noqa: E721 -- exact type only
        if not _emit(emit, budget, type(value).__name__ + "("):
            return False
        for key, entry in _members(value):
            for part in ((key,) if entry is None else (key, entry)):
                if not _walk(part, seen, limit, budget, emit, depth + 1) and emit is not None:
                    return False
                if not _emit(emit, budget, ","):
                    return False
        return _emit(emit, budget, ")")
    if emit is None:
        return True
    rendered, truncated = bounded_repr(value, limit)
    if truncated or _OPAQUE.search(rendered):
        return False
    return _emit(emit, budget, type(value).__name__ + ":" + rendered)


def _fingerprint(namespace: dict, baseline: dict, limit: int) -> str:
    """One pass: hash the namespace, or return "" because it cannot be hashed.

    `baseline` is the namespace as the preamble left it. A name still bound to
    the very object the preamble bound is skipped, which keeps the digest to
    what the session did rather than to a thousand SymPy exports, and which is
    stable across processes for exactly the names that were never touched.
    Rebinding a preamble name brings it back in; mutating one of those objects
    in place does not, and that is the hole this leaves.
    """
    digest = hashlib.sha256()
    # Which object each name is bound to, numbered by first appearance. A repr
    # describes a value and says nothing about the object graph: `a = []; b = a`
    # and `a = []; b = []` render identically, so a replay that rebuilt the
    # wrong one of those fingerprinted the same -- and a later `a.append(1); b`
    # then sees different state. A second name for an object already seen
    # hashes that reference instead of its repr, so sharing is part of the
    # fingerprint. The walk holds every value, so the ids cannot be reused
    # under it, and the order is the deterministic one below.
    seen: dict[int, int] = {}
    # What the walk may spend: objects to number, then bytes to describe them
    # in. Both are generous against anything a session would plausibly hold
    # and both are finite, because a namespace can hold a graph no traversal
    # would finish and a handful of objects that render to more than a machine
    # has. Neither is a cap on what is *reported* -- only the hex digest
    # leaves here -- so exceeding one costs the digest rather than truncating
    # it, and the cell is reported unverified.
    budget = [max(limit, 1024), max(limit, 1024) * 256]
    # `repr`, not the name itself: `globals()` accepts a non-string key, and
    # `sorted` over mixed types raises -- which used to lose the whole digest
    # for the namespace rather than describe it.
    for name in sorted(namespace, key=repr):
        value = namespace[name]
        # Numbered even when it is not described. A name whose value is not
        # hashed is still a *position* in the object graph, and leaving it out
        # of `seen` let the graph be rebuilt differently for free.
        #
        # `_` is not one of them. It was skipped while it held the value
        # `value_repr` had already recorded, and that justification cost three
        # findings before it was abandoned: skipping it unconditionally hid a
        # cell that assigned `_` itself, remembering the object across cells
        # hid mutations to it, and clearing it every cell still left the
        # sharing *inside* the displayed value unrecorded -- `[x, x]` and
        # `[[], []]` have the same `value_repr` and different futures. A repr
        # was never a fingerprint, which is the whole argument for walking the
        # graph; a skip resting on one could not survive it. `_` is a name in
        # the namespace and is hashed like any other.
        if name in baseline and baseline[name] is value:
            _walk(value, seen, limit, budget)
            continue
        _open(digest, name, b"value")
        if not _walk(value, seen, limit, budget, digest.update):
            # A prefix, a default repr, and a graph too large or too deep to
            # walk are all the same answer: Hardy could not see what is there.
            # Two values agreeing for the first `limit` bytes hash alike, and
            # two instances of the same class render identically whatever they
            # hold -- either would let a rebuild holding the wrong one be
            # called faithful, which is the exact failure the digest exists to
            # prevent. No digest at all is the honest answer: the parent reads
            # an empty one as "not compared" and says so.
            return ""
        digest.update(b"\0")
    # A name the preamble bound and a cell removed contributes nothing to the
    # loop above, so a namespace missing it fingerprinted exactly like one
    # still holding it: `del symbols; 1 / 0` followed by an accepted `pass`
    # rebuilt with `symbols` quietly back and was called faithful. Absence is
    # as much a change as a new value, and is hashed as one.
    for name in sorted((key for key in baseline if key not in namespace), key=repr):
        _record(digest, name, b"deleted", b"")
    return digest.hexdigest()


def state_digest(namespace: dict, baseline: dict, limit: int) -> str:
    """A fingerprint of everything a cell has put in the namespace.

    Recovery replays the accepted cells and compares what they printed. That
    is not the same as comparing what they *did*: `import random; x =
    random.random()` prints nothing at all, so a replay that rebuilt a
    different `x` reproduced three empty fields and was called faithful, and
    every cell after it was standing on a value nobody had checked. The digest
    is what makes that comparable -- it is recorded per cell and compared on
    replay, so an unobservable change is observable after all.

    Taken twice, and withheld unless the two agree. Fingerprinting runs
    `repr`, and a `__repr__` is a cell's own code: one that assigns
    `globals()["a"]` mutates a name that has already been hashed, so the digest
    describes a namespace that no longer exists by the time it is finished --
    and if what it assigns differs run to run, the recorded digest and the
    replay's agree while the two namespaces do not. That is the exact failure
    the digest exists to catch, arriving through the digest itself.

    A second pass is what detects it, because there is no asking an object
    whether its repr has side effects. A pass over an unchanged namespace
    hashes it identically; any mutation that a fingerprint could have seen
    moves the second answer off the first, and no digest is the honest result.
    One that a fingerprint could *not* have seen is the limit named in
    `docs/design/computer-algebra.md` either way. This also reports a namespace being changed under
    Hardy by a thread a cell started as unfingerprintable, which it is.

    The cost is one extra walk per cell, bounded by the same node and payload
    budgets as the first.
    """
    first = _fingerprint(namespace, baseline, limit)
    if not first:
        return ""
    return first if first == _fingerprint(namespace, baseline, limit) else ""
