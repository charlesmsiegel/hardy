"""Registering a problem with a host Lake project, and refusing to when it collides."""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

import pytest

from hardy import lakefile


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_a_problem_is_registered_as_its_own_library(tmp_path: Path):
    host = write(tmp_path / "lakefile.toml", 'name = "host"\n')
    write(tmp_path / "sylow" / "lean" / "Sylow.lean", "import Mathlib\n")

    added = lakefile.register(host, tmp_path, "sylow")

    assert "[[lean_lib]]" in added
    assert 'name = "sylow"' in added
    assert "sylow/lean" in added


def test_registering_twice_is_refused_rather_than_duplicated(tmp_path: Path):
    host = write(
        tmp_path / "lakefile.toml",
        'name = "host"\n\n[[lean_lib]]\nname = "sylow"\nsrcDir = "other/lean"\n',
    )
    write(tmp_path / "sylow" / "lean" / "Sylow.lean", "import Mathlib\n")

    with pytest.raises(lakefile.RegistrationRefused, match="already defines"):
        lakefile.register(host, tmp_path, "sylow")


def test_a_duplicate_module_name_is_refused_and_names_the_holder(tmp_path: Path):
    """A distinct Lake target does not rename the modules under it.

    Two problems both holding the documented default `lean/Main.lean` expose
    two modules named `Main` to one build, whatever their targets are called.
    """
    host = write(
        tmp_path / "lakefile.toml",
        'name = "host"\n\n[[lean_lib]]\nname = "galois"\nsrcDir = "galois/lean"\n',
    )
    write(tmp_path / "galois" / "lean" / "Main.lean", "import Mathlib\n")
    write(tmp_path / "sylow" / "lean" / "Main.lean", "import Mathlib\n")

    with pytest.raises(lakefile.RegistrationRefused) as refusal:
        lakefile.register(host, tmp_path, "sylow")

    assert "Main" in str(refusal.value)
    assert "galois" in str(refusal.value)


def test_distinct_module_names_register_cleanly(tmp_path: Path):
    host = write(
        tmp_path / "lakefile.toml",
        'name = "host"\n\n[[lean_lib]]\nname = "galois"\nsrcDir = "galois/lean"\n',
    )
    write(tmp_path / "galois" / "lean" / "Galois.lean", "import Mathlib\n")
    write(tmp_path / "sylow" / "lean" / "Sylow.lean", "import Mathlib\n")

    added = lakefile.register(host, tmp_path, "sylow")
    assert 'name = "sylow"' in added


def test_modules_are_named_by_their_path(tmp_path: Path):
    write(tmp_path / "lean" / "Group" / "Sylow.lean", "import Mathlib\n")
    write(tmp_path / "lean" / "Main.lean", "import Mathlib\n")
    assert lakefile.exposed_modules(tmp_path / "lean") == {"Group.Sylow", "Main"}


needs_symlinks = pytest.mark.skipif(
    os.name == "nt", reason="symlink_to needs Developer Mode on Windows"
)


def test_the_stanza_names_the_modules_hardy_actually_creates(tmp_path: Path):
    """The stanza must build Hardy's layout, not Lake's default guess.

    Lake documents `roots` as defaulting "to a single root of the target's
    name", so `name = "sylow"` with `srcDir = "sylow/lean"` sent Lake looking
    for a module `sylow` -- while Hardy creates `lean/Main.lean`, module
    `Main`. `lake build` therefore found nothing, immediately after Hardy
    printed "`lake build` now sees its modules". Writing `roots` out is what
    makes that sentence true; Lake recomputes `globs` from the roots given, and
    a root builds itself and everything it imports.
    """
    host = write(tmp_path / "lakefile.toml", 'name = "host"\n')
    write(tmp_path / "sylow" / "lean" / "Main.lean", "import Mathlib\n")
    write(tmp_path / "sylow" / "lean" / "Group" / "Sylow.lean", "import Mathlib\n")

    added = lakefile.register(host, tmp_path, "sylow")

    assert 'roots = ["Group.Sylow", "Main"]' in added
    # And the whole stanza still parses as the TOML Lake will read.
    parsed = tomllib.loads(added)
    assert parsed["lean_lib"][0]["roots"] == ["Group.Sylow", "Main"]
    assert parsed["lean_lib"][0]["srcDir"] == "sylow/lean"


def test_a_problem_with_no_sources_yet_still_declares_a_root(tmp_path: Path):
    """An empty `roots` array is a library that builds nothing.

    Registration can happen before a single file is saved, and `Main` is what
    Hardy's own default path creates, so that is the root to promise.
    """
    host = write(tmp_path / "lakefile.toml", 'name = "host"\n')
    (tmp_path / "sylow" / "lean").mkdir(parents=True)
    assert 'roots = ["Main"]' in lakefile.register(host, tmp_path, "sylow")


@needs_symlinks
def test_a_symlinked_host_lakefile_is_refused(tmp_path: Path):
    """`is_file()`, `tomllib`, and the append all follow a link.

    Reproduced before the fix: `<root>/lakefile.toml -> ../other/lakefile.toml`
    had Hardy parse another project's build definition and append a `lean_lib`
    stanza to it, naming a `srcDir` that does not exist over there.
    """
    other = write(tmp_path / "other" / "lakefile.toml", 'name = "other"\n')
    root = tmp_path / "root"
    root.mkdir()
    host = root / "lakefile.toml"
    host.symlink_to(other)

    with pytest.raises(lakefile.RegistrationRefused, match="resolves to"):
        lakefile.registered_libraries(host)
    with pytest.raises(lakefile.RegistrationRefused, match="resolves to"):
        lakefile.append_stanza(host, '\n[[lean_lib]]\nname = "x"\n')
    assert other.read_text(encoding="utf-8") == 'name = "other"\n'


@needs_symlinks
def test_a_symlinked_source_stops_the_collision_scan(tmp_path: Path):
    """A linked source answers for a file the host build cannot reach."""
    lean = tmp_path / "lean"
    lean.mkdir()
    outside = write(tmp_path / "Outside.lean", "import Mathlib\n")
    (lean / "Main.lean").symlink_to(outside)
    with pytest.raises(lakefile.RegistrationRefused, match="symlink"):
        lakefile.exposed_modules(lean)
