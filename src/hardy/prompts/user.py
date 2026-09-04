"""The user's own slash commands: markdown files in `.hardy/prompts/`.

A filename becomes a command, its frontmatter describes it, and its body is the
text that gets sent. The repeatable asks in mathematics are stereotyped --
"formalize this and list every interpretation choice", "audit the workspace",
"restate this in the style of the existing writeup" -- and they are worth
keeping next to the project rather than retyping.

Deliberately outside `_prompt_set_payload`, so nothing here moves
`PROMPT_SET_SHA256`. That hash identifies the *instructions Hardy sends*, and a
user template is not one of them: it is input, typed by the person at the
prompt, and expanding it is a convenience over typing the same paragraph again.
Folding it in would also make a staged run's hash depend on a file staged runs
never read.

Two rules make a shared record readable, and both are the point:

- The expansion is what goes to the model and what goes into
  `transcript.jsonl` -- never the `/name`. A record that says `/audit` refers
  to a file its reader does not have, which is the same as saying nothing.
- A placeholder with no argument is a refusal, not an empty string. Hardy's
  own templates render under `StrictUndefined` for the same reason: a prompt
  that quietly lost half its sentence still looks entirely ordinary.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from pathlib import Path

#: Where a project keeps them, under `.hardy/`.
DIRECTORY = "prompts"
SUFFIX = ".md"
#: The largest template Hardy will read. A prompt template is a paragraph or
#: two; anything past this is either not one or is not meant to be sent, and
#: reading it whole at startup is how a session stops opening.
LIMIT = 64 * 1024

#: What a command may be called. The same shape the built-in names have, so a
#: template is typed and completed exactly like `/status` -- and so a filename
#: can never introduce a name the dispatcher lowercases into something else.
NAME = re.compile(r"[a-z0-9][a-z0-9_-]*\Z")

#: `$@`, `$1`, `$12`, and `$$` for a literal dollar. Nothing else is touched,
#: which is what lets a template body carry LaTeX: `$x + y$` has no digit and
#: no `@` after its dollars, so it survives verbatim.
#:
#: Positional indices start at 1, so `$0` is not one. Matching it made
#: `Show $0 < x$` -- ordinary inline mathematics -- a template that could never
#: expand, because the argument it asked for does not exist at any index.
PLACEHOLDER = re.compile(r"\$(\$|@|[1-9][0-9]*)")

FRONTMATTER = "---"
#: `description: ...`. Hyphens accepted in keys because Pi spells one
#: `argument-hint` and a user copying a template across should not have to know
#: that Hardy stores it under an underscore.
SETTING = re.compile(r"([A-Za-z][A-Za-z0-9_-]*)\s*:\s*(.*)\Z")


class TemplateError(ValueError):
    """A template that cannot be read, named, or expanded with what was typed."""


@dataclass(frozen=True)
class Template:
    """One `.hardy/prompts/<name>.md`, parsed."""

    name: str
    body: str
    description: str = ""
    argument_hint: str = ""
    #: Where it came from, so `/help` can say which file to edit.
    path: Path | None = None

    @property
    def summary(self) -> str:
        return self.description or f"your own prompt ({self.name}.md)"


def _unquoted(word: str) -> str:
    """One token with the quotes non-POSIX `shlex` deliberately leaves on."""
    if len(word) >= 2 and word[0] == word[-1] and word[0] in {'"', "'"}:
        return word[1:-1]
    return word


def tokenize(argument: str) -> list[str]:
    """The positional arguments in what was typed after the command.

    Non-POSIX `shlex`, then unquoted by hand, for the reason `/import` gives:
    POSIX rules read every backslash as an escape, and a mathematician's
    argument is full of them -- `\\forall`, `\\mathbb{Z}`, a Windows path.
    Quoting still groups, so `"the alternating group"` is one argument.

    An unbalanced quote is not an error here. Apostrophes are ordinary in
    mathematical English -- "Sylow's theorem" -- and refusing to run a command
    over one would be a worse answer than splitting on whitespace, which is
    what a user typing prose meant anyway.
    """
    try:
        return [_unquoted(word) for word in shlex.split(argument, posix=False)]
    except ValueError:
        return argument.split()


def parse(name: str, text: str, *, path: Path | None = None) -> Template:
    """One template file, as `load` reads it.

    Frontmatter is optional and is a flat `key: value` block between two `---`
    lines. Deliberately not YAML: a dependency is not worth two keys, and the
    keys Hardy reads (`description`, `argument-hint`) are strings.
    """
    if not NAME.match(name):
        raise TemplateError(
            f"{name!r} is not a usable command name: use lower-case letters, "
            "digits, hyphens and underscores, starting with a letter or digit"
        )
    settings, body = _frontmatter(text)
    if not body.strip():
        raise TemplateError(f"{name!r} has no body: there would be nothing to send")
    return Template(
        name=name,
        body=body.strip(),
        description=settings.get("description", ""),
        argument_hint=settings.get("argument_hint", ""),
        path=path,
    )


def _frontmatter(text: str) -> tuple[dict[str, str], str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != FRONTMATTER:
        return {}, text
    for index in range(1, len(lines)):
        if lines[index].strip() == FRONTMATTER:
            settings: dict[str, str] = {}
            for line in lines[1:index]:
                found = SETTING.match(line.strip())
                if found is not None:
                    key = found.group(1).replace("-", "_").lower()
                    settings[key] = found.group(2).strip().strip("'\"")
            return settings, "\n".join(lines[index + 1 :])
    # An opening marker with no close is a body that starts with a rule, not a
    # frontmatter block Hardy silently ate the rest of the file for.
    return {}, text


def expand(template: Template, argument: str) -> str:
    """The text this command actually sends, or a refusal naming what is missing."""
    words = tokenize(argument)
    rest = argument.strip()
    missing: list[str] = []

    def substitute(found: re.Match[str]) -> str:
        token = found.group(1)
        if token == "$":
            return "$"
        if token == "@":
            # Empty is missing, exactly as `$1` with no first word is. The
            # contract is that a placeholder with nothing to fill it refuses;
            # `/formalize` with no argument was sending and recording
            # "Formalize ." instead, which is the malformed prompt the rule
            # exists to prevent.
            if not rest:
                missing.append("$@")
            return rest
        index = int(token)
        if index < 1 or index > len(words):
            missing.append(f"${token}")
            return ""
        return words[index - 1]

    expanded = PLACEHOLDER.sub(substitute, template.body)
    if missing:
        hint = f" Usage: /{template.name} {template.argument_hint}".rstrip()
        raise TemplateError(
            f"/{template.name} needs {', '.join(dict.fromkeys(missing))}, "
            f"and {len(words)} argument(s) were given.{hint if template.argument_hint else ''}"
        )
    if not expanded.strip():
        raise TemplateError(f"/{template.name} expanded to nothing; there is no message to send")
    return expanded.strip()


def directory(root: Path) -> Path:
    """Where `root`'s templates live. One place, named once."""
    from ..layout import HARDY_DIR

    return root / HARDY_DIR / DIRECTORY


def load(root: Path, *, reserved: frozenset[str] | set[str] = frozenset()) -> tuple[list[Template], list[str]]:
    """Every readable template under `root`, and one sentence per one that is not.

    Never raises. A project's own directory is untrusted input like any other
    file a clone brings with it, and a bad template is a line at startup rather
    than a session that will not open -- exactly how an unreadable input history
    or a refused `.gitignore` is handled elsewhere.

    A name that collides with a built-in command is refused rather than
    shadowing it. Shadowing `/exit` or `/status` would let a checked-in file
    change what Hardy's own commands do, and a user who cannot leave the
    session has no way to find out why.

    Nothing here is read through a link, and nothing that is not an ordinary
    file is read at all -- the same rule `layout` enforces on every other path
    inside a project, and it has to hold here for two reasons. A checkout can
    ship `.hardy/prompts/notes.md -> ~/.ssh/id_rsa`, and the body of a template
    is *sent*: the link would turn `/notes` into a command that mails a host
    file to the provider. And a link to a device or a fifo -- `/dev/zero` is
    the easy one -- would hang or exhaust memory during startup, before the
    session exists to report it.
    """
    where = directory(root)
    problems: list[str] = []
    try:
        # Every component from `root` down, not only the leaf. `.hardy` itself
        # can be the link: a checkout shipping `.hardy -> ~/somewhere` with an
        # ordinary `prompts/*.md` beneath it passes a check on `prompts` alone
        # and loads host files as prompts to send. Above `root` is the user's
        # own filesystem and none of Hardy's business.
        walked = root
        for name in where.relative_to(root).parts:
            walked = walked / name
            if walked.is_symlink():
                return [], [
                    f"{walked} is a symlink; refusing to read prompt templates through it."
                ]
        if not where.is_dir():
            return [], problems
        files = sorted(where.iterdir(), key=lambda item: item.name)
    except OSError as error:
        return [], [f"Could not read {where}: {error}"]

    found: dict[str, Template] = {}
    for item in files:
        if item.suffix != SUFFIX:
            continue
        name = item.stem.lower()
        try:
            if item.is_symlink():
                problems.append(
                    f"{item.name} is a symlink, so it was not loaded. A template's "
                    "body is sent to the model; Hardy reads one only where it lies."
                )
                continue
            # A regular file and nothing else: a directory named `x.md`, a
            # fifo, or a device would each get past a bare existence test, and
            # two of those never finish being read.
            if not item.is_file():
                continue
            if item.stat().st_size > LIMIT:
                problems.append(
                    f"{item.name} is larger than {LIMIT} bytes, so it was not loaded. "
                    "A prompt template is prose."
                )
                continue
            text = item.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as error:
            problems.append(f"Could not read {item.name}: {error}")
            continue
        if name in reserved:
            problems.append(
                f"{item.name} would shadow the built-in /{name}, so it was not loaded. "
                "Rename the file."
            )
            continue
        if name in found:
            problems.append(f"{item.name} names /{name}, which {found[name].path} already does.")
            continue
        try:
            found[name] = parse(name, text, path=item)
        except TemplateError as error:
            problems.append(f"{item.name}: {error}")
    return list(found.values()), problems
