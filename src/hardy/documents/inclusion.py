r"""Which files a writeup tree loads, read from its text.

One root document is ever compiled, and every other file is a fragment it
reaches by `\input`, `\include` or `\subfile` (`INCLUSION`), named the way
TeX resolves the name (`resolver`). Which of those inputs actually run is
`syntax`'s question; this module answers the two that need no conditional:
what an argument names, and every file TeX might read at all (`may_reach`).
"""
from __future__ import annotations

import re
from collections.abc import Callable, Collection, Mapping
from pathlib import PurePosixPath

# Fragments are `\input` from one document, and that document is what a
# compiler is ever pointed at.
ROOT_DOCUMENT = "writeup.tex"
INCLUSION = re.compile(r"\\(?:input|include|subfile)\s*\{([^}]*)\}")


def normalise_include(found: str) -> str:
    r"""A captured `\input{...}` argument, in the spelling `by_stem` is keyed by.

    Tectonic accepts either path separator and resolves `.` as the including
    file's own directory, so `\input{./lemma1}` and `\input{lemma1}` name the
    same file -- and did, in the PDF, while an un-normalised lookup here
    called the first one an orphan nothing includes.
    """
    posix = PurePosixPath(found.strip().replace("\\", "/"))
    parts = [part for part in posix.parts if part != "."]
    return str(PurePosixPath(*parts)) if parts else ""



def resolver(paths: Collection[str]) -> Callable[[str], str | None]:
    r"""What an `\input{...}` argument names among `paths`, with or without
    `.tex`, with either separator and a leading `./`, as TeX reads it."""
    by_stem: dict[str, str] = {}
    for path in paths:
        normal = path.replace("\\", "/")
        by_stem[normal] = path
        if normal.endswith(".tex"):
            by_stem[normal[: -len(".tex")]] = path

    def resolve(found: str) -> str | None:
        key = normalise_include(found)
        target = by_stem.get(key)
        if target is None and key.endswith(".tex"):
            target = by_stem.get(key[: -len(".tex")])
        elif target is None:
            target = by_stem.get(f"{key}.tex")
        return target

    return resolve


#: A load whose file name is built from a macro, or a loader followed straight
#: by a control sequence (`\input\name`): the name is not in the text, so any
#: file may be the one read.
_BUILT_LOAD = re.compile(
    r"\\(?:@*input|include|InputIfFileExists|import|subimport|subfile|inputfrom|subinputfrom"
    r"|includefrom|subincludefrom)(?![a-zA-Z])\s*(?:\\|(?:\{[^{}]*\}\s*)?\{[^{}]*\\"
    # TeX's `^^` notation spells a character by its code, `\input ^^64efs` a
    # name that is then written nowhere.
    r"|[^\n]*?\^\^)"
)


def may_reach(texts: Mapping[str, str], root: str) -> set[str]:
    r"""Every file TeX might read when it runs `root`, over-approximated.

    Which files TeX reads depends on the conditionals this module reads, so
    the set is taken without them -- and without trying to know every way a
    file is loaded: `\input defs`, `\InputIfFileExists{defs}{}{}`,
    `\import{./}{defs}` and `\def\d{defs}\input{\d}` all read `defs.tex`, and
    following `\input{...}` alone dropped it and the `\newif` in it. So a file
    is left out only when nothing in any other file's `typeset` text mentions
    it -- its path, its path without `.tex`, or its base name either way --
    and no file loads a name built from a macro, which could be any file.
    `texts` are already `typeset`: a mention in a comment or a verbatim block
    loads nothing.
    """
    if any(_BUILT_LOAD.search(text) for text in texts.values()):
        return set(texts)
    reached = {root}
    for path in texts:
        if path == root:
            continue
        normal = path.replace("\\", "/")
        base = normal.rsplit("/", 1)[-1]
        names = {normal, normal.removesuffix(".tex"), base, base.removesuffix(".tex")} - {""}
        if any(name in text for other, text in texts.items() if other != path for name in names):
            reached.add(path)
    return reached
