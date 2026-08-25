"""Registering a problem's Lean with the host Lake project, or refusing to.

Registration is for the user's own toolchain and editor. Hardy's resolution
never depends on it, so declining always costs nothing -- which is what makes
refusing a collision the right answer rather than a hard case.
"""

from __future__ import annotations

import tomllib
from pathlib import Path, PurePosixPath

from .workspace import module_name


class RegistrationRefused(Exception):
    """A registration that would make the host build ambiguous."""


def exposed_modules(lean_root: Path) -> set[str]:
    """Every module name a source directory would put into a build."""
    if not lean_root.is_dir():
        return set()
    return {
        module_name(PurePosixPath(path.relative_to(lean_root).as_posix()))
        for path in lean_root.rglob("*.lean")
    }


def registered_libraries(lakefile: Path) -> dict[str, str]:
    """The `lean_lib` entries a host lakefile already declares, name to srcDir."""
    if not lakefile.is_file():
        return {}
    try:
        loaded = tomllib.loads(lakefile.read_text(encoding="utf-8-sig"))
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
    return f'\n[[lean_lib]]\nname = "{slug}"\nsrcDir = "{source}"\n'
