"""Registering a problem's Lean with the host Lake project, or refusing to.

Registration is for the user's own toolchain and editor. Hardy's resolution
never depends on it, so declining always costs nothing -- which is what makes
refusing a collision the right answer rather than a hard case.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from .layout import LayoutError, files_under, resolve_named_child
from .workspace import module_name

#: What Hardy's own template puts in a fresh `lean/`, and so the root to
#: declare when a problem has no sources yet. An empty `roots` array is a
#: library that builds nothing, which is the shape of the bug this replaces.
DEFAULT_ROOT = "Main"


class RegistrationRefused(Exception):
    """A registration that would make the host build ambiguous."""


def host_lakefile(lakefile: Path) -> Path:
    """`lakefile`, proven to be the host project's own file rather than a link.

    `is_file()` follows a symlink, `tomllib` reads through one, and the append
    that registers a library opens it `"a"` and follows it too -- so
    `<root>/lakefile.toml -> ../other/lakefile.toml` had Hardy parse another
    project's build definition and then write a `lean_lib` stanza into it,
    naming a `srcDir` that does not exist over there. The same identity rule
    every other project path is held to settles it: the host lakefile must BE
    the root's own child of that name.
    """
    try:
        return resolve_named_child(lakefile, lakefile.parent.resolve())
    except LayoutError as error:
        raise RegistrationRefused(str(error)) from None


def append_stanza(lakefile: Path, stanza: str) -> None:
    """Add `stanza` to the host lakefile, re-proving it at the moment of the write.

    Proven again here rather than trusted from the parse: the read happened
    before a human was asked whether to register at all, and a file can be
    replaced by a link in between.
    """
    with host_lakefile(lakefile).open("a", encoding="utf-8") as handle:
        handle.write(stanza)


def exposed_modules(lean_root: Path) -> set[str]:
    """Every module name a source directory would put into a build.

    Through `files_under`, which refuses a symlink anywhere in the tree, so the
    collision check is asked about the modules that are really there. A linked
    source counted here would answer for a file the host build cannot reach.
    """
    if not lean_root.is_dir():
        return set()
    try:
        return {module_name(relative) for relative in files_under(lean_root, ".lean")}
    except LayoutError as error:
        # A refusal, not a traceback. Registration is a convenience for the
        # user's own editor and declining costs nothing, so a source tree that
        # cannot be enumerated honestly is reported the way every other
        # registration problem is.
        raise RegistrationRefused(str(error)) from None


def registered_libraries(lakefile: Path) -> dict[str, str]:
    """The `lean_lib` entries a host lakefile already declares, name to srcDir."""
    if not lakefile.is_file():
        return {}
    proven = host_lakefile(lakefile)
    try:
        loaded = tomllib.loads(proven.read_text(encoding="utf-8-sig"))
    except tomllib.TOMLDecodeError as error:
        raise RegistrationRefused(f"{lakefile} could not be read: {error}") from None
    libraries = loaded.get("lean_lib") or []
    if isinstance(libraries, dict):
        libraries = [libraries]
    return {
        str(entry["name"]): str(entry.get("srcDir", ""))
        for entry in libraries
        if isinstance(entry, dict) and entry.get("name")
    }


def register(lakefile: Path, root: Path, slug: str) -> str:
    """The lakefile stanza that registers `slug`, or a refusal with a reason.

    Refused on two collisions, and a distinct Lake target only settles the
    first of them. A `lean_lib` name is a target name; it does not rename the
    modules underneath it, so two problems both holding the documented default
    `lean/Main.lean` still put two modules named `Main` into one build.

    `roots` is written out, and that is what makes the stanza do anything at
    all. Lake defaults a library's roots to `#[name]` -- one module named after
    the library -- so `name = "sylow"` with `srcDir = "sylow/lean"` sends Lake
    looking for `sylow/lean/Sylow.lean`, while Hardy creates `lean/Main.lean`
    holding module `Main`. `lake build` then found nothing, having been told it
    would now see the problem's modules. Naming every module the tree exposes
    is the form that covers a split workspace too: a root builds itself and
    what it imports, so a second file no module imports would otherwise never
    be built either.
    """
    existing = registered_libraries(lakefile)
    source = f"{slug}/lean"
    if slug in existing and existing[slug] != source:
        raise RegistrationRefused(
            f"{lakefile.name} already defines a library named {slug!r} for {existing[slug]!r}"
        )
    mine = exposed_modules(root / slug / "lean")
    for name, directory in sorted(existing.items()):
        if name == slug:
            continue
        clashing = sorted(mine & exposed_modules(root / directory))
        if clashing:
            raise RegistrationRefused(
                f"{slug!r} and the registered library {name!r} both expose "
                f"{clashing[0]!r}; rename the file in one of them, or decline "
                "registration -- Hardy's own resolution does not need it"
            )
    roots = ", ".join(f'"{module}"' for module in sorted(mine) or [DEFAULT_ROOT])
    return f'\n[[lean_lib]]\nname = "{slug}"\nsrcDir = "{source}"\nroots = [{roots}]\n'
