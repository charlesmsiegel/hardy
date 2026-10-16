r"""Pure TeX syntax over a writeup tree, without compilation.

Which `\if...` names are conditionals, which files TeX reads and which it
never reaches, and where the document begins. The line-by-line scan this rests
on -- comments, verbatim, macro bodies, false branches -- is `scanner`'s, and
the names other packages read it through are re-exported here.
"""
from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable, Collection, Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from hardy.documents.scanner import (
    _MACRO_DEF,
    Conditionals,
    _conditional_pattern,
    _executed_line,
    _false_regions,
    _MacroState,
    _Purpose,
    _Reading,
    _skip_balanced,
    _skip_command,
    _tex_lines,
    typeset,
)
from hardy.documents.scanner import CONDITIONALS as CONDITIONALS
from hardy.documents.scanner import INLINE_VERBATIM as INLINE_VERBATIM
from hardy.documents.scanner import NO_CONDITIONALS as NO_CONDITIONALS
from hardy.documents.scanner import VERBATIM_ENVIRONMENT as VERBATIM_ENVIRONMENT
from hardy.documents.scanner import opens_conditional as opens_conditional
from hardy.documents.scanner import uncommented as uncommented
from hardy.documents.scanner import unfinished_definition as unfinished_definition

# Fragments are `\input` from one document, and that document is what a
# compiler is ever pointed at.
ROOT_DOCUMENT = "writeup.tex"
# How many times one check may run the compiler. A document with any `\ref` in
# it needs two passes to resolve one -- LaTeX writes the numbers into the
# `.aux` on the way through and reads them on the pass after -- and a
# `thebibliography` cited from the text needs the same. The third is for the
# case where resolving a reference moved a page number and moved a reference
# with it; TeX distributions converge in three in practice, and a document
# that has not converged by then is reported on the last pass rather than
# looped over. Each pass is bounded by the same `timeout` as before, so the
# worst case here is three times what one compile could take.
MAX_PASSES = 3
#: How much of `writeup.log` is read. Generous for the log of a real document
#: and small enough that one looping `\typeout` cannot take the process with
#: it -- the compiler's own output is already bounded by the subprocess guard,
#: and this is the file that was not.
MAX_LOG_BYTES = 8 * 1024 * 1024
#: How large an auxiliary file may be and still be read. Unlike the log, which
#: is bounded and TRUNCATED, an oversized `.aux` refuses the compile: the log
#: is prose about the run and its tail is the useful part, while the `.aux` is
#: the record of what the document cited, and reading part of one would be
#: vouching for citations without having seen them all -- which is the
#: direction this whole check exists to rule out. Generous: the auxiliary file
#: of a book-length document with thousands of labels is a few megabytes, and
#: nothing but a `\@auxout` written in a loop reaches this.
MAX_AUX_BYTES = 16 * 1024 * 1024
BEGIN_DOCUMENT = re.compile(r"\\begin\{document\}")


def stamped(source: str, stamp: str | None) -> str:
    r"""`source` with a provenance banner after `\begin{document}`.

    Applied to the scratch copy `check` compiles, never to the file that is
    saved: the source stays the author's, and the banner cannot be edited out
    of the document a reader opens.

    Before `\maketitle` rather than after, which puts it on page one above the
    title. That is where a provenance banner belongs, and burying it is how the
    graded run's own warning went unread -- Hardy said "Still missing labels for
    registered names" under 4.8 KB of pdfTeX font paths, and the PDF went out
    anyway.

    A document with no `\begin{document}` is returned untouched. It is a
    fragment being probed, or a file too broken to compile, and neither is worth
    failing a compile over: breaking the build to enforce a banner inverts the
    priority.
    """
    if not stamp:
        return source
    found = executed_begin_document(source)
    if found is None:
        return source
    banner = (
        "\n\\begingroup\\footnotesize\\noindent\n"
        f"{stamp}\n"
        "\\par\\endgroup\\medskip\\hrule\\medskip\n"
    )
    return source[: found.end()] + banner + source[found.end() :]
#: What a compile of the real document produces, by the exact names Hardy
#: publishes and reads. Never copied into the scratch tree: a checked-in or
#: left-behind `tex/writeup.pdf` made `pdf.exists()` true for a compile that
#: wrote no document at all, so the refusal added for that case passed and the
#: OLD file was published as the new source's -- the very outcome the check
#: exists to prevent, with the evidence supplied by the tree being checked.
#: Matched by whole path rather than by suffix, because `\includegraphics` of
#: a `.pdf` figure is an ordinary thing a writeup does.
OUTPUTS = frozenset({"writeup.pdf", "writeup.log"})

#: Files a LaTeX run writes for its own next pass. None of them is an input,
#: and every one of them is a way for the last compile to speak for this one.
#: `.aux` was the first found: a committed one had its `\citation` records
#: read as this document's. A `.toc` is the same shape with a worse ending --
#: under `\nofiles` LaTeX reads the stale file, does not rewrite it, and puts
#: last time's section titles and page numbers in a PDF that exits zero and is
#: then published and stamped as current.
ARTIFACTS = frozenset({".aux", ".toc", ".out", ".lof", ".lot", ".nav", ".snm", ".vrb"})

BODY = "\\begin{document}"
INCLUSION = re.compile(r"\\(?:input|include|subfile)\s*\{([^}]*)\}")


def compiles_document(sources: Mapping[str, str], path: str) -> bool:
    r"""Whether saving `path` compiles the writeup itself rather than a probe.

    The whole tree, because inclusion is transitive: `writeup.tex` includes
    `a.tex` and `a.tex` includes `b.tex`, and asking only whether the root's
    own text names `b.tex` called a file that is genuinely in the document a
    probe -- which is the same mistake `LatexTools.check` was making, and the
    same walk fixes both.
    """
    return path == ROOT_DOCUMENT or path in reached_fragments(sources)


def _normalise_include(found: str) -> str:
    r"""A captured `\input{...}` argument, in the spelling `by_stem` is keyed by.

    Tectonic accepts either path separator and resolves `.` as the including
    file's own directory, so `\input{./lemma1}` and `\input{lemma1}` name the
    same file -- and did, in the PDF, while an un-normalised lookup here
    called the first one an orphan nothing includes.
    """
    posix = PurePosixPath(found.strip().replace("\\", "/"))
    parts = [part for part in posix.parts if part != "."]
    return str(PurePosixPath(*parts)) if parts else ""


#: `\newif\ifdraft` declares `\ifdraft` as a conditional of the document's own.
#: The declaration names the conditional without opening it.
_NEWIF = re.compile(r"\\newif\s*\{?\s*\\(if[a-zA-Z]+)(?![a-zA-Z])")
#: An `\if...` name bound some other way than `\newif`. `\def\ifdraft{}` makes
#: a macro, which TeX never counts while it skips; `\let\ifdraft\iftrue` makes
#: a conditional it does, and what a `\let` binds is not read here.
_DEFINES = re.compile(
    r"\\(?:[gxe]?def|(?:re)?newcommand\*?|providecommand\*?|DeclareRobustCommand\*?)"
    r"\s*\{?\s*\\(if[a-zA-Z]+)(?![a-zA-Z])"
)
#: The `\let`-like commands: each binds a control word to the meaning of a
#: token, which may be a conditional TeX counts while it skips -- directly, or
#: through a chain (`\let\a\iftrue \let\mycond\a`) no text scan can follow.
#: Each names what it captures the same way, which `_binding` reads: the name
#: bound as a control `word` or as the text of a `\csname` (`built`), and what
#: it is bound to as a control word (`target_word`, backslash included), as
#: `\csname` text (`target_built`), or as a single `character`.
_CS = r"\\(?:[a-zA-Z@]+|.)"


def _csname(group: str) -> str:
    return rf"\\csname(?![a-zA-Z])(?P<{group}>[^{{}}]*?)\\endcsname"


_LET_BINDING = re.compile(
    rf"\\let(?![a-zA-Z])\s*(?:{_csname('built')}|\\(?P<word>[a-zA-Z@]+)|\\.)"
    rf"\s*=?\s?(?:{_csname('target_built')}|(?P<target_word>{_CS})|(?P<character>.))?",
    re.DOTALL,
)
_FUTURELET = re.compile(r"\\futurelet(?![a-zA-Z])\s*\\(?P<word>[a-zA-Z@]+)")
_CSLET = re.compile(
    rf"\\cslet(?![a-zA-Z])\s*\{{(?P<built>[^{{}}]*)\}}\s*(?:(?P<target_word>{_CS})|(?P<character>.))?",
    re.DOTALL,
)
_CSLETCS = re.compile(r"\\csletcs(?![a-zA-Z])\s*\{(?P<built>[^{}]*)\}\s*\{(?P<target_built>[^{}]*)\}")
_LETCS = re.compile(r"\\letcs(?![a-zA-Z])\s*\\(?P<word>[a-zA-Z@]+)\s*\{(?P<target_built>[^{}]*)\}")
_LET_LIKE = (_LET_BINDING, _FUTURELET, _CSLET, _CSLETCS, _LETCS)
_NEWIF_CSNAME = re.compile(rf"\\newif\s*{_csname('built')}")
#: The other commands that copy a control word's meaning, as `\let` does, with
#: the kinds of their arguments: `N` a control word (braced or not), `c` the
#: text of a `\csname`. `letltxmacro`'s `\LetLtxMacro`, the kernel's
#: `\NewCommandCopy` family, and expl3's `\cs_set_eq` family; expl3's
#: `\cs_undefine` binds its argument to `\undefined`.
_COPY_COMMANDS = re.compile(
    r"\\(LetLtxMacro|NewCommandCopy|RenewCommandCopy|DeclareCommandCopy"
    r"|cs_(?:g?set|new)_eq:(?:NN|Nc|cN|cc)|cs_g?undefine:[Nc])(?![a-zA-Z_:@])"
)
_EXPL_NAME = re.compile(r"\\([a-zA-Z@_:]+|.)")
#: Every control word a `\def`-like command defines, whatever it is called.
_ANY_DEFINES = re.compile(
    r"\\(?:[gxe]?def|(?:re)?newcommand\*?|providecommand\*?|DeclareRobustCommand\*?)"
    r"\s*\{?\s*\\([a-zA-Z@]+)"
)
#: Targets that are never a conditional -- while nothing in the tree rebinds
#: them. A `\let` to one of these, or to a character, binds a name TeX never
#: counts while it skips.
_HARMLESS_TARGETS = frozenset({"relax", "undefined", "empty", "@empty", "par", "space", "bgroup", "egroup"})
#: `ifthen`'s `\newboolean{draft}` and `\provideboolean{draft}` declare `\ifdraft`.
_BOOLEAN = re.compile(r"\\(?:new|provide)boolean\s*\{\s*([a-zA-Z]+)\s*\}")


@dataclass(frozen=True)
class _Binding:
    """One `\\let`-like binding: the name bound (`None` when it cannot be read),
    what it is bound to (a control word's name, `""` for a character, `None`
    when unknown), and where the bound control word is written, if it is."""

    name: str | None
    target: str | None
    at: int | None


def _csname_text(text: str) -> str | None:
    r"""A `\csname ... \endcsname` name read literally, or `None` when it holds
    a control sequence and so could be anything."""
    return None if "\\" in text else text.strip()


def _let_bindings(text: str) -> list[_Binding]:
    bindings = (_binding(match) for pattern in _LET_LIKE for match in pattern.finditer(text))
    found = [binding for binding in bindings if binding is not None]
    found.extend(_copy_binding(text, match) for match in _COPY_COMMANDS.finditer(text))
    return found


def _binding(match: re.Match[str]) -> _Binding | None:
    r"""The binding one `_LET_LIKE` match makes, or None for a control symbol
    (`\let\&...`), which is never written as a word."""
    groups = match.groupdict()
    if groups.get("word") is not None:
        name, at = groups["word"], match.start("word") - 1
    elif groups.get("built") is not None:
        name, at = _csname_text(groups["built"]), None
    else:
        return None
    return _Binding(name, _bound_to(groups), at)


def _bound_to(groups: Mapping[str, str | None]) -> str | None:
    """What a `_LET_LIKE` match binds its name to, from its named groups."""
    if groups.get("target_word") is not None:
        return groups["target_word"][1:]
    if groups.get("target_built") is not None:
        return _csname_text(groups["target_built"])
    character = groups.get("character")
    return "" if character is not None and not character.isspace() else None


def _copy_binding(text: str, match: re.Match[str]) -> _Binding:
    command = match.group(1)
    kinds = command.rsplit(":", 1)[1] if ":" in command else "NN"
    arguments: list[tuple[str | None, int | None]] = []
    position = match.end()
    for kind in kinds:
        value, at, position = _copy_argument(text, position, kind)
        arguments.append((value, at))
    name, at = arguments[0]
    bound_to = arguments[1][0] if len(arguments) > 1 else "undefined"
    return _Binding(name, bound_to, at)


def _copy_argument(text: str, position: int, kind: str) -> tuple[str | None, int | None, int]:
    r"""One argument of a copy command at `position`: the control word's name
    (`N`) or the `\csname` text (`c`) -- `None` when it cannot be read --
    where a control word is written, and where the argument ends."""
    while position < len(text) and text[position] in _TEX_SPACE:
        position += 1
    if position >= len(text):
        return None, None, position
    if text[position] == "{":
        close = text.find("}", position)
        if close < 0:
            return None, None, len(text)
        inner = text[position + 1 : close]
        stripped = inner.strip()
        if kind == "c":
            return _csname_text(inner), None, close + 1
        word = _EXPL_NAME.fullmatch(stripped)
        at = position + 1 + inner.index(stripped) if word else None
        return (word.group(1) if word else None), at, close + 1
    word = _EXPL_NAME.match(text, position)
    if word is not None:
        return (word.group(1) if kind == "N" else None), position, word.end()
    # A lone character: bound to a character, which is never a conditional.
    return ("" if kind == "N" else text[position]), None, position + 1


_LETTERS = re.compile(r"[a-zA-Z]+")
_TEX_SPACE = frozenset(" \t\r\n")


def read_conditionals(
    sources: Mapping[str, str], root: str | None = ROOT_DOCUMENT, *, every_file: bool = False
) -> Conditionals:
    r"""Which `\if...` names of `sources` are conditionals, in reading order.

    A `\newif` collected from the whole tree before anything is read took a
    name as a conditional before TeX had declared it: `\def\ifdraft{}`, then
    `\iffalse \ifdraft \fi` and live text, then `\newif\ifdraft` -- TeX meets
    `\ifdraft` as a macro while skipping, the first `\fi` closes the false
    branch, and the scan that nested it hid the live text after it.

    Reading order across files is the order `root` inputs them in, through
    `\input`s that certainly run (outside any conditional, macro body and
    verbatim block) of files input exactly once. Without a root, or for a
    file reached no such way, only order within one file is known, and a
    declaration elsewhere cannot be placed before a use -- the name is
    ambiguous.

    Only files TeX may read count: a `\def\ifdraft{}` in a `.tex` nothing
    inputs never runs, and counting it made the root's correctly declared
    `\ifdraft` ambiguous and blocked a valid report. Which files TeX reads
    depends on the conditionals being read here, so the set is taken without
    them (`may_reach`): every file another file mentions by name, however it
    is loaded, or every file at all when one loads a name built from a macro.
    That can only include more files, which keeps a binding that might run
    counted. With no `root` among the
    sources, or `every_file` (a caller that scans every file as though it were
    read), every file counts.
    """
    texts = _texts_read(sources, root, every_file=every_file)
    defined = _captured(_DEFINES, texts.values())
    newif = _captured(_NEWIF, texts.values()) | _captured(_BOOLEAN, texts.values(), prefix="if")
    built, opaque_declaration = _csname_declarations(texts.values())
    bindings = [binding for text in texts.values() for binding in _let_bindings(text)]
    let, opaque_binding = _let_conditionals(bindings, _captured(_ANY_DEFINES, texts.values()))
    rebound = let | built | (defined & (newif | CONDITIONALS))
    # Counted as openers for "does this run": everything that might be one.
    openers = CONDITIONALS | newif | let | built
    events = {path: _conditional_events(text, openers) for path, text in texts.items()}
    keys = _reading_keys(texts, events, root)
    declared = {name for name in newif - rebound if _declared_before_use(name, events, keys)}
    ambiguous = rebound | (newif - rebound - declared)
    return Conditionals(frozenset(declared), frozenset(ambiguous), opaque_declaration or opaque_binding)


def _texts_read(sources: Mapping[str, str], root: str | None, *, every_file: bool) -> dict[str, str]:
    """The `typeset` text of every file of `sources` TeX may read from `root`."""
    texts = {path: typeset(text) for path, text in sources.items()}
    if every_file or root is None or root not in texts:
        return texts
    read = may_reach(texts, root)
    return {path: text for path, text in texts.items() if path in read}


def _captured(pattern: re.Pattern[str], texts: Iterable[str], *, prefix: str = "") -> set[str]:
    """Every name `pattern` captures in `texts`, after `prefix`."""
    return {prefix + found.group(1) for text in texts for found in pattern.finditer(text)}


def _csname_declarations(texts: Iterable[str]) -> tuple[set[str], bool]:
    r"""The names `\newif\csname ...\endcsname` declares literally, and whether
    one declares a name Hardy cannot read -- which may be any `\if...`."""
    names = [_csname_text(found.group("built")) for text in texts for found in _NEWIF_CSNAME.finditer(text)]
    built = {name for name in names if name is not None and _LETTERS.fullmatch(name)}
    return built, any(_opaque(name) for name in names)


def _opaque(name: str | None) -> bool:
    r"""Whether a bound or declared name may be any conditional at all: one
    Hardy cannot read, or one spelled `\if...` that no control word a scan
    meets can be."""
    return name is None or (name.startswith("if") and not _LETTERS.fullmatch(name))


def _let_conditionals(bindings: Collection[_Binding], defined: Collection[str]) -> tuple[set[str], bool]:
    r"""The names a `\let`-like binding may make a conditional, and whether
    one binds a name Hardy cannot read, which may be any control word.

    Any name a `\let`-like command binds may be a conditional, whatever it
    is bound to -- a chain (`\let\a\iftrue \let\mycond\a`) is not
    followed. Except a literal name not spelled `\if...` bound to a
    character or to a target that is never a conditional while nothing
    rebinds it: `\let\oldx\relax`, the package-faking
    `\expandafter\let\csname ver@hyperref.sty\endcsname\relax`. A literal
    name that is not letters (`ver@hyperref.sty`) can never be written as a
    control word in a false branch, and so binds nothing a scan meets.
    """
    unsafe = {binding.name for binding in bindings if binding.name} | set(defined)
    let = {
        binding.name for binding in bindings
        if binding.name is not None and _LETTERS.fullmatch(binding.name) and _may_bind_conditional(binding, unsafe)
    }
    return let, any(_opaque(binding.name) for binding in bindings)


def _may_bind_conditional(binding: _Binding, unsafe: Collection[str]) -> bool:
    if binding.name and binding.name.startswith("if"):
        return True
    harmless = binding.target == "" or (binding.target in _HARMLESS_TARGETS and binding.target not in unsafe)
    return not harmless


def _declared_before_use(
    name: str, events: Mapping[str, _Events], keys: Mapping[str, tuple[int, ...]]
) -> bool:
    r"""Whether every `\newif` of `name` runs, and one is read before each use."""
    places = [
        (path, offset, runs)
        for path, found in events.items()
        for named, offset, runs in found.declarations
        if named == name
    ]
    uses = [(path, offset) for path, found in events.items() for named, offset in found.uses if named == name]
    if not all(runs for _, _, runs in places):
        return False
    return all(any(_read_before((path, offset), use, keys) for path, offset, _ in places) for use in uses)


def _read_before(
    first: tuple[str, int], second: tuple[str, int], keys: Mapping[str, tuple[int, ...]]
) -> bool:
    """Whether TeX certainly reads offset `first` before `second`, as (file, offset)."""
    if first[0] == second[0]:
        return first[1] < second[1]
    if first[0] in keys and second[0] in keys:
        return (*keys[first[0]], first[1]) < (*keys[second[0]], second[1])
    return False


@dataclass(frozen=True)
class _Events:
    """One file's `\\newif`s (name, offset, whether it certainly runs), its
    uses of `\\if...` names, and its `\\input`s (offset, argument, whether it
    certainly runs), all over its `typeset` text."""

    declarations: tuple[tuple[str, int, bool], ...]
    uses: tuple[tuple[str, int], ...]
    inputs: tuple[tuple[int, str, bool], ...]


def _conditional_events(text: str, openers: Collection[str]) -> _Events:
    trace = _Trace(_macro_bodies(text), _binding_sites(text), openers)
    for found in _conditional_pattern(openers).finditer(text):
        trace.meet(found)
    booleans = tuple(
        (f"if{found.group(1)}", found.start(), trace.runs(found.start())) for found in _BOOLEAN.finditer(text)
    )
    inputs = tuple((found.start(), found.group(1), trace.runs(found.start())) for found in INCLUSION.finditer(text))
    return _Events((*trace.declarations, *booleans), tuple(trace.uses), inputs)


def _binding_sites(text: str) -> set[int]:
    r"""Where each control word a `\def` or `\let` binds is written: the name
    a binding binds is not a use of it."""
    defined = {found.start(1) - 1 for found in _DEFINES.finditer(text)}
    return defined | {binding.at for binding in _let_bindings(text) if binding.at is not None}


def _inside(spans: Iterable[tuple[int, int]], index: int) -> bool:
    return any(start <= index < end for start, end in spans)


@dataclass
class _Trace:
    """How deep in conditionals each point of one file is, read in order,
    with the `\\newif`s and `\\if...` uses met on the way."""

    bodies: list[tuple[int, int]]
    sites: set[int]
    openers: Collection[str]
    depth: int = 0
    # (offset, depth from there on), in offset order.
    depths: list[tuple[int, int]] = field(default_factory=lambda: [(0, 0)])
    declarations: list[tuple[str, int, bool]] = field(default_factory=list)
    uses: list[tuple[str, int]] = field(default_factory=list)

    def meet(self, found: re.Match[str]) -> None:
        r"""One `\if...`, `\fi` or `\newif` token, in reading order."""
        name = found.group(2)
        body = _inside(self.bodies, found.start())
        if found.group(1) is not None:
            self.declarations.append((name, found.start(), self.depth == 0 and not body))
            return
        if found.start(2) - 1 in self.sites:
            return
        if name != "fi" and name not in CONDITIONALS:
            self.uses.append((name, found.start()))
        if body:
            # A stored body runs where it is expanded, not here.
            return
        if name == "fi":
            self.depth = max(self.depth - 1, 0)
        elif name in self.openers:
            self.depth += 1
        self.depths.append((found.end(), self.depth))

    def runs(self, index: int) -> bool:
        """Whether text at `index` certainly runs: outside every conditional and macro body."""
        level = 0
        for at, value in self.depths:
            if at > index:
                break
            level = value
        return level == 0 and not _inside(self.bodies, index)


def _resolver(paths: Collection[str]) -> Callable[[str], str | None]:
    r"""What an `\input{...}` argument names among `paths`, with or without
    `.tex`, with either separator and a leading `./`, as TeX reads it."""
    by_stem: dict[str, str] = {}
    for path in paths:
        normal = path.replace("\\", "/")
        by_stem[normal] = path
        if normal.endswith(".tex"):
            by_stem[normal[: -len(".tex")]] = path

    def resolve(found: str) -> str | None:
        key = _normalise_include(found)
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


def _reading_keys(
    texts: Mapping[str, str], events: Mapping[str, _Events], root: str | None
) -> dict[str, tuple[int, ...]]:
    """Where each file starts in reading order: the offsets of the `\\input`s
    leading to it from `root`, compared as tuples. Only files input exactly
    once, through inputs that certainly run, have one."""
    if root is None or root not in texts:
        return {}
    resolve = _resolver(texts)
    once = _input_once(events, resolve)
    keys: dict[str, tuple[int, ...]] = {root: ()}
    frontier = [root]
    while frontier:
        current = frontier.pop()
        for offset, argument, runs in events[current].inputs:
            target = resolve(argument)
            if runs and target in once and target not in keys:
                keys[target] = (*keys[current], offset)
                frontier.append(target)
    return keys


def _input_once(events: Mapping[str, _Events], resolve: Callable[[str], str | None]) -> set[str]:
    """The files the tree inputs exactly once, counting every `\\input`."""
    counts = Counter(resolve(argument) for found in events.values() for _, argument, _ in found.inputs)
    return {target for target, count in counts.items() if target is not None and count == 1}


def _drop_iffalse(text: str, conditionals: Conditionals = NO_CONDITIONALS) -> str:
    r"""`text` with every `\iffalse ... \fi` region removed.

    Only a real conditional opens a nested region (`Conditionals.opens`):
    `\iff` in a false branch has no `\fi`, and counting it left the branch
    open to the end of the file. A name `conditionals` calls ambiguous is read
    both ways, and text either reading skips is dropped: an `\input` there is
    credited to neither, and `uncertain_conditionals` reports the name so the
    check refuses rather than guesses.

    Depth-tracked rather than matched to the nearest `\fi`: a conditional
    written inside the false branch -- `\ifx\a\b ... \fi` guarding something
    else entirely -- carries its own `\fi`, which closes IT, not the
    `\iffalse` wrapping it. Stopping at the first `\fi` seen would close the
    outer conditional early and leave everything after the inner one,
    `\input` included, looking executed when it is still inside dead code.
    """
    out = []
    index = 0
    for start, end in sorted(_either_false_regions(text, conditionals)):
        if end <= index:
            continue
        out.append(text[index:max(start, index)])
        index = end
    out.append(text[index:])
    return "".join(out)


def _either_false_regions(text: str, conditionals: Conditionals) -> list[tuple[int, int]]:
    """The false branches of `text` under both readings of an ambiguous name."""
    return _false_regions(text, conditionals, nest=True) + _false_regions(text, conditionals, nest=False)


def _uncertain_in(text: str, conditionals: Conditionals) -> list[str]:
    r"""The ambiguous names `text` uses inside a false branch, in order."""
    found: list[str] = []
    regions = _either_false_regions(text, conditionals)
    if conditionals.opaque and regions:
        # A conditional whose `\csname`-built name Hardy cannot read may be
        # any control word in any false branch.
        found.append("csname")
    for start, end in regions:
        for match in conditionals.pattern.finditer(text, start, end):
            name = match.group(2)
            if match.group(1) is None and name in conditionals.ambiguous and name not in found:
                found.append(name)
    return found


def uncertain_conditionals(sources: Mapping[str, str]) -> list[tuple[str, str]]:
    r"""Every (file, name) where a false branch holds an `\if...` name Hardy
    cannot place, so where the branch ends is not known.

    The finding the writeup scan owes instead of a guess: nested, the name
    hides whatever follows its `\fi`; not nested, it credits whatever the
    branch still hides. `_drop_iffalse` drops what either reading skips, and
    the compile check refuses the writeup over these.
    """
    conditionals = read_conditionals(sources)
    texts = {path: typeset(text) for path, text in sources.items()}
    read = may_reach(texts, ROOT_DOCUMENT) if ROOT_DOCUMENT in texts else set(texts)
    return [
        (path, name)
        for path in sorted(read)
        for name in _uncertain_in(texts[path], conditionals)
    ]


def _macro_bodies(text: str) -> list[tuple[int, int]]:
    r"""The span of every macro-definition body in `text`, as `(start, end)`
    over its braces -- `\newcommand`, `\renewcommand`, `\providecommand`,
    `\def`, and both bodies of `\newenvironment`/`\renewenvironment`.

    One walk, two questions. What is inside a macro body is not executed until
    the macro is expanded, and nothing here expands macros -- so the body is
    dropped for the reachability walk, and it is stopped from opening a
    verbatim region for the bibliography scan. Those want different treatment
    of the same span, which is why this returns the spans rather than the
    edited text.

    The name argument (`{\foo}`, or a bare `\foo` for `\def`) and any
    `[n]`/`[default]` brackets are not part of the body; only the balanced
    `{...}` that follows is.
    """
    spans: list[tuple[int, int]] = []
    index = 0
    while (keyword := _MACRO_DEF.search(text, index)) is not None:
        # `\newenvironment{name}{begin}{end}` has TWO bodies, and the second
        # is as unexecuted as the first: an `\input` written into either runs
        # when the environment is used, not when it is defined. Recognising
        # only `\newcommand` and friends left both of them looking live, which
        # is the dangerous direction -- a fragment nothing reaches judged part
        # of the document is compiled by running the unchanged root, which
        # never reads it.
        bodies, end = _bodies(text, _body_start(text, keyword.end()), 2 if keyword.group("env") else 1)
        spans.extend(bodies)
        index = max(end, keyword.end())
    return spans


def _skip_spaces(text: str, index: int) -> int:
    while index < len(text) and text[index].isspace():
        index += 1
    return index


def _body_start(text: str, index: int) -> int:
    r"""Past the name and the `[...]` arguments of the definition whose keyword
    ends at `index`: where its first body's brace is, or the end of `text`."""
    index = _skip_spaces(text, index)
    if index < len(text) and text[index] == "{":
        index = _skip_balanced(text, index, "{", "}")
    elif index < len(text) and text[index] == "\\":
        index = _skip_command(text, index)
    index = _skip_spaces(text, index)
    while index < len(text) and text[index] == "[":
        index = _skip_spaces(text, _skip_balanced(text, index, "[", "]"))
    # `\def`'s parameter text (`#1#2`, delimiters) is neither a name nor a
    # bracket, so it is skipped over rather than parsed.
    brace = text.find("{", index)
    return len(text) if brace < 0 else brace


def _bodies(text: str, index: int, count: int) -> tuple[list[tuple[int, int]], int]:
    """Up to `count` balanced brace groups from `index`, and where the last ends."""
    found: list[tuple[int, int]] = []
    for _ in range(count):
        index = _skip_spaces(text, index)
        if index >= len(text) or text[index] != "{":
            break
        end = _skip_balanced(text, index, "{", "}")
        found.append((index, end))
        index = end
    return found, index


def _drop_macro_bodies(text: str) -> str:
    r"""`text` with the body of every macro definition removed.

    An `\input` written only inside a macro's own body runs when the macro is
    *expanded*, and nothing here expands macros -- reading it as reached the
    moment it is merely defined is the same mistake as reading one inside
    `\iffalse ... \fi` as reached because the branch text is merely present.
    """
    kept = []
    last = 0
    for opening, closing in _macro_bodies(text):
        kept.append(text[last:opening])
        last = closing
    kept.append(text[last:])
    return "".join(kept)


def _executed(source: str, conditionals: Conditionals = NO_CONDITIONALS) -> str:
    r"""`source` with everything TeX would never actually run removed.

    `uncommented` drops what a human comment hides from TeX; this drops
    what TeX itself never reaches. An `\input` inside a `verbatim` block, or
    inside `\iffalse ... \fi`, or
    inside a `\newcommand`/`\renewcommand`/`\providecommand`/`\def` body, is
    text that sits in the file but is never executed unless a branch is
    taken or a macro is expanded -- and nothing here does either. Reading it
    as reached is the same mistake `uncommented` already exists to avoid,
    one layer further in: not "did a human hide this with `%`" but "would
    TeX itself ever get here".

    Used by `unreached_fragments`, which asks that question. The walk
    keeps `uncommented`: whether a save *compiles* is a different question,
    answered by what the writeup's `\input` chain names, not by which of
    those inputs would run.
    """
    return _drop_macro_bodies(_drop_iffalse(typeset(source, conditionals=conditionals), conditionals))


def unreached_fragments(sources: Mapping[str, str]) -> list[str]:
    r"""Writeup files no `\input` chain from the root reaches.

    A fragment nothing includes is in no PDF, whatever it says. A session once
    wrote itself a status report that way and nobody could have read it.
    Follows the same commands `reached_fragments` does, through `_executed` rather
    than plain `uncommented`: an `\input` written inside `\iffalse ... \fi`
    or inside a macro definition's body is text TeX itself never runs, and
    counting it as reached would clear a fragment that is not, in fact, in
    the PDF -- the same failure this function exists to catch, from a
    different kind of dead text. Accepts a path with or without `.tex`,
    with either separator, and with a leading `./`, as TeX does.

    Returns `[]` when there is no root document yet, rather than every
    fragment: the prompt itself prescribes saving a fragment before
    `writeup.tex` mentions it, so a missing root is the normal state of a
    mid-build workspace, not a workspace where everything is an orphan.
    Nothing can be judged unreached from a root that does not exist.
    """
    if ROOT_DOCUMENT not in sources:
        return []
    reached = reached_fragments(sources)
    return sorted(path for path in sources if path not in reached)


def reached_fragments(sources: Mapping[str, str]) -> set[str]:
    r"""Every writeup file an `\input` chain from the root does reach.

    The walk itself, so that the two questions asked of it -- which files are
    orphans, and whether THIS file is part of the real document -- are
    answered by one traversal rather than by two rules that can disagree.
    They did: asking only whether the root's own text names a fragment made
    `b.tex`, included by `a.tex` which the root includes, look like a file
    nothing reaches, so saving it was compiled through a probe root and
    exempted from the reference checks that the real document owes.
    """
    if ROOT_DOCUMENT not in sources:
        return set()
    resolve = _resolver(sources)
    reached = {ROOT_DOCUMENT}
    frontier = [ROOT_DOCUMENT]
    conditionals = read_conditionals(sources)
    while frontier:
        current = frontier.pop()
        for found in INCLUSION.findall(_executed(sources[current], conditionals)):
            target = resolve(found)
            if target is not None and target not in reached:
                reached.add(target)
                frontier.append(target)
    return reached


def executed_begin_document(source: str) -> re.Match[str] | None:
    """Locate a live document opener without changing source offsets."""
    conditionals = read_conditionals({ROOT_DOCUMENT: source})
    text = "".join(_located_lines(source, _Reading(_Purpose.LOCATE, conditionals)))
    chars = list(text)
    for start, end in _either_false_regions(text, conditionals):
        chars[start:end] = " " * (end - start)
    return BEGIN_DOCUMENT.search("".join(chars))


def _located_lines(source: str, reading: _Reading) -> Iterator[str]:
    """Each line of `source` as TeX runs it, verbatim content blanked, offsets kept."""
    state = _MacroState()
    closing: str | None = None
    for raw, ending in _tex_lines(source):
        line = raw
        prefix = ""
        while line:
            if closing is not None:
                end = line.find(closing)
                if end < 0:
                    prefix += " " * len(line)
                    break
                end += len(closing)
                prefix += " " * end
                line = line[end:]
                closing = None
            text, environment, rest, _ = _executed_line(line, state, reading)
            prefix += text + " " * (len(line) - len(text) - len(rest))
            if environment is None:
                break
            closing = f"\\end{{{environment}}}"
            line = rest
        yield prefix + ending
