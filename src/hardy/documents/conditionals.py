r"""Which `\if...` names of a writeup are conditionals, read in the order TeX meets them.

Only matters inside a skipped branch, where TeX counts a conditional's `\fi`
as its own: a scan that counts a name TeX does not, or misses one it does,
ends the branch somewhere else. So every way a name can become a conditional
is read -- `\newif`, `\newboolean`, the `\let` family, copy commands, a
`\csname`-built declaration -- and a name Hardy cannot place before its first
use is called ambiguous rather than guessed at. The answer is a
`scanner.Conditionals`, which the scan consumes.
"""
from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable, Collection, Iterable, Mapping
from dataclasses import dataclass, field

from hardy.documents.inclusion import INCLUSION, ROOT_DOCUMENT, may_reach, resolver
from hardy.documents.scanner import (
    CONDITIONALS,
    Conditionals,
    conditional_pattern,
    macro_bodies,
    typeset,
)

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
    trace = _Trace(macro_bodies(text), _binding_sites(text), openers)
    for found in conditional_pattern(openers).finditer(text):
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


def _reading_keys(
    texts: Mapping[str, str], events: Mapping[str, _Events], root: str | None
) -> dict[str, tuple[int, ...]]:
    """Where each file starts in reading order: the offsets of the `\\input`s
    leading to it from `root`, compared as tuples. Only files input exactly
    once, through inputs that certainly run, have one."""
    if root is None or root not in texts:
        return {}
    resolve = resolver(texts)
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
