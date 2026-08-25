"""Registering a problem with a host Lake project, and refusing to when it collides."""

from __future__ import annotations

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
