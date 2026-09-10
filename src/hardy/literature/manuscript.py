"""A deliberately lexical inventory of supplied manuscript source.

This module does not read files or execute TeX.  It reports only literal source
structure and keeps every location in the original Unicode string, so a caller
can check a record against the exact source it supplied.  In particular, it is
not a statement reader: macro expansion, conditionals, custom environments and
the boundary of a mathematical claim are outside this small scanner.
Nested title commands are explicitly unavailable. Malformed stored definitions
make the remaining source unavailable when no reliable body boundary exists;
comment-spliced citation identities require a contiguous span or a finding.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from typing import Mapping

from hardy.literature.statements import ALIASES, KINDS

_SECTION_LEVELS = frozenset({"section", "subsection", "subsubsection", "paragraph", "subparagraph"})
_VERBATIM_ENVIRONMENTS = frozenset(
    {"verbatim", "verbatim*", "Verbatim", "Verbatim*", "lstlisting", "minted"}
)
_MACRO_DEFINITIONS = frozenset(
    {"newcommand", "renewcommand", "providecommand", "def", "gdef", "edef", "xdef", "newenvironment", "renewenvironment"}
)


@dataclass(frozen=True)
class SourceSpan:
    """An inclusive/exclusive Unicode-codepoint slice of one supplied source."""

    path: str
    digest: str
    start: int
    end: int

    def valid_for(self, path: str, text: str) -> bool:
        """Whether this span still identifies this exact supplied source."""
        return (
            path == self.path
            and 0 <= self.start <= self.end <= len(text)
            and hashlib.sha256(text.encode("utf-8")).hexdigest() == self.digest
        )

    def matches(self, path: str, text: str) -> bool:
        """Alias for :meth:`valid_for` for readers validating a saved span."""
        return self.valid_for(path, text)


@dataclass(frozen=True)
class Source:
    """The identity of one value from the explicit source mapping."""

    path: str
    digest: str


@dataclass(frozen=True)
class Section:
    level: str
    starred: bool
    command: SourceSpan
    title: SourceSpan | None


@dataclass(frozen=True)
class Environment:
    """A lexical source block, never a claimed mathematical assertion."""

    kind: str
    name: str
    opening: SourceSpan
    closing: SourceSpan | None


@dataclass(frozen=True)
class Label:
    value: str
    command: SourceSpan
    argument: SourceSpan


@dataclass(frozen=True)
class Citation:
    command_name: str
    key: str
    command: SourceSpan
    key_span: SourceSpan


@dataclass(frozen=True)
class Finding:
    """A bounded lexical limit or malformed construct, in source order."""

    kind: str
    detail: str
    span: SourceSpan

    @property
    def message(self) -> str:
        return self.detail


@dataclass(frozen=True)
class Inventory:
    """Immutable records from the mapping passed to :func:`inventory`."""

    sources: tuple[Source, ...]
    sections: tuple[Section, ...]
    environments: tuple[Environment, ...]
    labels: tuple[Label, ...]
    citations: tuple[Citation, ...]
    unsupported: tuple[Finding, ...]

    @property
    def findings(self) -> tuple[Finding, ...]:
        """The explicit lexical limits, under the more general reader name."""
        return self.unsupported


def inventory(sources: Mapping[str, str]) -> Inventory:
    """Return a deterministic lexical inventory of an explicit source mapping.

    Source labels are identities only; they are never treated as filesystem
    paths.  Each supplied value is strictly encoded as UTF-8 before scanning,
    which both derives its digest and refuses unpaired surrogates.
    """
    ordered: list[tuple[str, str, str]] = []
    for path, text in sources.items():
        if not isinstance(path, str) or not isinstance(text, str):
            raise TypeError("manuscript sources must map str paths to str text")
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        ordered.append((path, text, digest))
    ordered.sort(key=lambda item: item[0])

    scanner_results = [_Scanner(path, text, digest).scan() for path, text, digest in ordered]
    return Inventory(
        sources=tuple(Source(path, digest) for path, _, digest in ordered),
        sections=tuple(item for result in scanner_results for item in result.sections),
        environments=tuple(item for result in scanner_results for item in result.environments),
        labels=tuple(item for result in scanner_results for item in result.labels),
        citations=tuple(item for result in scanner_results for item in result.citations),
        unsupported=tuple(item for result in scanner_results for item in result.findings),
    )


scan = inventory


@dataclass
class _Result:
    sections: list[Section]
    environments: list[Environment]
    labels: list[Label]
    citations: list[Citation]
    findings: list[Finding]


@dataclass
class _OpenEnvironment:
    name: str
    record: int | None


class _Scanner:
    def __init__(self, path: str, text: str, digest: str) -> None:
        self.path = path
        self.text = text
        self.digest = digest
        self.result = _Result([], [], [], [], [])
        self.open_environments: list[_OpenEnvironment] = []

    def scan(self) -> _Result:
        index = 0
        while index < len(self.text):
            if self.text[index] == "%":
                index = self._comment_end(index)
            elif self.text[index] == "\\":
                index = self._command(index)
            else:
                index += 1
        for open_environment in self.open_environments:
            if open_environment.record is not None:
                environment = self.result.environments[open_environment.record]
                self._finding(
                    "unclosed_environment",
                    f"\\begin{{{environment.name}}} has no matching literal end",
                    environment.opening.start,
                    environment.opening.end,
                )
        return self.result

    def _command(self, start: int) -> int:
        name, end = self._control_word(start)
        if not name:
            return end
        if end < len(self.text) and self.text[end] == "%":
            after = self._comment_end(end)
            if after < len(self.text) and self.text[after].isalpha():
                self._finding(
                    "split_control_word",
                    "a comment splits a control word; it is not treated as a literal command",
                    start,
                    after,
                )
        if name == "verb":
            return self._verb(start, end)
        if name in _MACRO_DEFINITIONS:
            return self._macro_definition(name, start, end)
        if name == "newtheorem":
            self._finding("macro_declaration", "custom theorem declarations are not interpreted", start, end)
            return end
        if name.startswith("if"):
            return self._conditional(name, start, end)
        if name == "fi":
            self._finding("stray_conditional_end", "literal \\fi has no scanned conditional opener", start, end)
            return end
        if name in {"input", "include", "subfile"}:
            self._finding("external_input", "source mappings are not followed or read", start, end)
            return end
        if name in _SECTION_LEVELS:
            return self._section(name, start, end)
        if name == "begin":
            return self._begin(start, end)
        if name == "end":
            return self._end(start, end)
        if name == "label":
            return self._label(start, end)
        if name.startswith("cite"):
            return self._citation(name, start, end)
        return end

    def _section(self, name: str, start: int, end: int) -> int:
        starred = end < len(self.text) and self.text[end] == "*"
        after_name = end + 1 if starred else end
        argument = self._group(after_name, "{", "}")
        if argument is None:
            self._finding("missing_argument", f"\\{name} has no balanced title", start, after_name)
            self.result.sections.append(Section(name, starred, self._span(start, after_name), None))
            return after_name
        opening, closing = argument
        self.result.sections.append(
            Section(name, starred, self._span(start, closing), self._span(opening + 1, closing - 1))
        )
        if "\\" in self.text[opening + 1 : closing - 1]:
            self._finding(
                "nested_title_structure",
                "commands inside section titles are not inventoried",
                opening + 1,
                closing - 1,
            )
        return closing

    def _begin(self, start: int, end: int) -> int:
        argument = self._group(end, "{", "}")
        if argument is None:
            self._finding("unbalanced_argument", "\\begin has no balanced environment name", start, end)
            return end
        opening, closing = argument
        name = self.text[opening + 1 : closing - 1].strip()
        opening_span = self._span(start, closing)
        if name in _VERBATIM_ENVIRONMENTS:
            return self._verbatim(name, start, closing)
        record: int | None = None
        kind = _environment_kind(name)
        if kind is not None:
            record = len(self.result.environments)
            self.result.environments.append(Environment(kind, name, opening_span, None))
        self.open_environments.append(_OpenEnvironment(name, record))
        return closing

    def _end(self, start: int, end: int) -> int:
        argument = self._group(end, "{", "}")
        if argument is None:
            self._finding("unbalanced_argument", "\\end has no balanced environment name", start, end)
            return end
        opening, closing = argument
        name = self.text[opening + 1 : closing - 1].strip()
        if not self.open_environments:
            self._finding("stray_environment_end", f"\\end{{{name}}} has no opener", start, closing)
            return closing
        current = self.open_environments[-1]
        if current.name != name:
            self._finding(
                "mismatched_environment",
                f"\\end{{{name}}} does not close literal \\begin{{{current.name}}}",
                start,
                closing,
            )
            return closing
        self.open_environments.pop()
        if current.record is not None:
            old = self.result.environments[current.record]
            self.result.environments[current.record] = replace(old, closing=self._span(start, closing))
        return closing

    def _label(self, start: int, end: int) -> int:
        argument = self._group(end, "{", "}")
        if argument is None:
            self._finding("unbalanced_argument", "\\label has no balanced argument", start, end)
            return end
        opening, closing = argument
        value = self.text[opening + 1 : closing - 1].strip()
        self.result.labels.append(Label(value, self._span(start, closing), self._span(opening + 1, closing - 1)))
        return closing

    def _citation(self, name: str, start: int, end: int) -> int:
        position = end + 1 if end < len(self.text) and self.text[end] == "*" else end
        while True:
            optional = self._group(position, "[", "]")
            if optional is None:
                break
            _, position = optional
        argument = self._group(position, "{", "}")
        if argument is None:
            self._finding("unbalanced_argument", f"\\{name} has no balanced key argument", start, position)
            return position
        opening, closing = argument
        command = self._span(start, closing)
        for key_start, key_end in self._citation_keys(opening + 1, closing - 1):
            self.result.citations.append(
                Citation(name, self.text[key_start:key_end], command, self._span(key_start, key_end))
            )
        return closing

    def _macro_definition(self, name: str, start: int, end: int) -> int:
        position = end + 1 if end < len(self.text) and self.text[end] == "*" else end
        if name in {"def", "gdef", "edef", "xdef"}:
            position = self._skip_space_comments(position)
            _, position = self._control_word(position) if position < len(self.text) and self.text[position] == "\\" else ("", position)
            while position < len(self.text) and self.text[position] != "{":
                position = self._comment_end(position) if self.text[position] == "%" else position + 1
            bodies = 1
        else:
            position = self._skip_space_comments(position)
            first = self._group(position, "{", "}")
            if first is not None:
                _, position = first
            elif (name in {"newcommand", "renewcommand", "providecommand"}
                  and position < len(self.text) and self.text[position] == "\\"):
                _, position = self._control_word(position)
            else:
                # No reliable body boundary remains: do not scan stored decoys.
                self._finding("macro_definition", f"\\{name} declaration is unsupported; remaining source unavailable",
                              start, len(self.text))
                return len(self.text)
            optional = self._group(position, "[", "]")
            if optional is not None:
                _, position = optional
                # The argument count may be followed by TeX's optional
                # default value.  Both precede the stored body; stopping at
                # the first bracket would scan that body as live source.
                default = self._group(position, "[", "]")
                if default is not None:
                    _, position = default
            bodies = 2 if name in {"newenvironment", "renewenvironment"} else 1
        for _ in range(bodies):
            body = self._group(position, "{", "}")
            if body is None:
                self._finding("macro_definition", f"\\{name} has an unbalanced body; remaining source unavailable",
                              start, len(self.text))
                return len(self.text)
            _, position = body
        self._finding("macro_definition", f"\\{name} body is stored, not expanded", start, position)
        return position

    def _conditional(self, name: str, start: int, end: int) -> int:
        depth = 1
        position = end
        while position < len(self.text) and depth:
            if self.text[position] == "%":
                position = self._comment_end(position)
                continue
            if self.text[position] != "\\":
                position += 1
                continue
            nested, after = self._control_word(position)
            if nested.startswith("if"):
                depth += 1
            elif nested == "fi":
                depth -= 1
            position = after
        detail = "literal conditional source is not interpreted"
        if depth:
            detail = "unterminated conditional source is not interpreted"
        self._finding("conditional", detail, start, position)
        return position

    def _verb(self, start: int, end: int) -> int:
        position = end + 1 if end < len(self.text) and self.text[end] == "*" else end
        if position >= len(self.text) or self.text[position].isspace() or self.text[position].isalpha():
            self._finding("unterminated_verb", "\\verb has no literal delimiter", start, position)
            return position
        delimiter = self.text[position]
        close = self.text.find(delimiter, position + 1)
        if close < 0:
            self._finding("unterminated_verb", "\\verb has no closing delimiter", start, len(self.text))
            return len(self.text)
        return close + 1

    def _verbatim(self, name: str, start: int, after_opening: int) -> int:
        marker = f"\\end{{{name}}}"
        close = self.text.find(marker, after_opening)
        if close < 0:
            self._finding("unclosed_verbatim", f"\\begin{{{name}}} has no literal end", start, after_opening)
            return len(self.text)
        return close + len(marker)

    def _group(self, position: int, opener: str, closer: str) -> tuple[int, int] | None:
        position = self._skip_space_comments(position)
        if position >= len(self.text) or self.text[position] != opener:
            return None
        start = position
        depth = 0
        while position < len(self.text):
            character = self.text[position]
            if character == "\\":
                _, position = self._control_word(position)
                continue
            if character == "%":
                position = self._comment_end(position)
                continue
            if character == opener:
                depth += 1
            elif character == closer:
                depth -= 1
                if depth == 0:
                    return start, position + 1
            position += 1
        return None

    def _citation_keys(self, start: int, end: int) -> list[tuple[int, int]]:
        keys: list[tuple[int, int]] = []
        left: int | None = None
        right = start
        comments: list[int] = []
        depth = 0
        position = start
        while position <= end:
            at_end = position == end
            character = "" if at_end else self.text[position]
            if character == "%":
                comments.append(position)
                position = min(self._comment_end(position), end)
                continue
            if at_end or (character == "," and depth == 0):
                if left is not None:
                    if any(left < comment < right for comment in comments):
                        self._finding("comment_spliced_citation_key",
                                      "comment-spliced key has no contiguous original source span", left, right)
                    else:
                        keys.append((left, right))
                left = None
                comments = []
                position += 1
                continue
            # Use the same token boundaries as balanced groups: escaped commas,
            # braces and percent signs are control tokens, not delimiters.
            after = position + 1
            if character == "\\":
                _, after = self._control_word(position)
            elif character == "{":
                depth += 1
            elif character == "}" and depth:
                depth -= 1
            if not character.isspace():
                if left is None:
                    left = position
                right = after
            position = after
        return keys

    def _control_word(self, start: int) -> tuple[str, int]:
        position = start + 1
        if position >= len(self.text):
            return "", position
        if self.text[position].isalpha():
            begin = position
            while position < len(self.text) and self.text[position].isalpha():
                position += 1
            return self.text[begin:position], position
        return self.text[position], position + 1

    def _skip_space_comments(self, position: int) -> int:
        while position < len(self.text):
            if self.text[position].isspace():
                position += 1
            elif self.text[position] == "%":
                position = self._comment_end(position)
            else:
                break
        return position

    def _comment_end(self, position: int) -> int:
        newline = self.text.find("\n", position + 1)
        return len(self.text) if newline < 0 else newline + 1

    def _span(self, start: int, end: int) -> SourceSpan:
        return SourceSpan(self.path, self.digest, start, end)

    def _finding(self, kind: str, detail: str, start: int, end: int) -> None:
        self.result.findings.append(Finding(kind, detail, self._span(start, end)))


def _environment_kind(name: str) -> str | None:
    if name in {"proof", "proof*"}:
        return "proof"
    if name in KINDS:
        return name
    return ALIASES.get(name)
