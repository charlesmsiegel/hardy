from __future__ import annotations

from pathlib import Path

import pytest
from web_fakes import make_problem

from hardy.app.web import uploads


@pytest.mark.parametrize("bad", ["", ".", "..", ".hidden", "a/b", "a\\b", "con.lean", "x:y", "trailing ", "nul"])
def test_safe_name_refuses(bad: str) -> None:
    with pytest.raises(ValueError):
        uploads.safe_name(bad)


def test_safe_name_keeps_interior_dots() -> None:
    assert uploads.safe_name("Sylow.v2.lean") == "Sylow.v2.lean"


def test_kind_of() -> None:
    assert uploads.kind_of("A.lean") == "lean"
    assert uploads.kind_of("a.TEX") == "tex"
    assert uploads.kind_of("paper.pdf") == "source"
    assert uploads.kind_of("notes.md") == "source"
    assert uploads.kind_of("data.bin") == "other"


def test_stage_lists_and_suffixes(tmp_path: Path) -> None:
    problem = make_problem(tmp_path)
    first = uploads.stage(problem, "A.lean", b"theorem t : True := trivial\n")
    second = uploads.stage(problem, "A.lean", b"other")
    assert first["name"] == "A.lean" and second["name"] == "A-2.lean"
    assert first["kind"] == "lean" and first["size"] == 28
    assert Path(first["path"]) == (problem / ".local" / "uploads" / "A.lean").resolve()
    assert [s["name"] for s in uploads.staged(problem)] == ["A-2.lean", "A.lean"]
    uploads.discard(problem, "A.lean")
    assert [s["name"] for s in uploads.staged(problem)] == ["A-2.lean"]
    with pytest.raises(ValueError):
        uploads.discard(problem, "missing.lean")


def test_stage_refuses_oversize(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(uploads, "MAX_UPLOAD", 4)
    with pytest.raises(ValueError):
        uploads.stage(make_problem(tmp_path), "a.lean", b"12345")


def test_library_import_admits_and_seeds(tmp_path: Path) -> None:
    problem = make_problem(tmp_path)
    uploads.stage(problem, "notes.md", b"# A note\n\nTheorem 1. Everything is fine.\n")
    out = uploads.library_import(problem, "notes.md", title="Notes", intent="background", library_root=tmp_path / "library")
    assert len(out["artifact"]) == 64 and out["seed"].startswith("seed")
    from hardy.literature.sources.seeds import SeedStore
    seeds = SeedStore(problem).seeds()
    assert [s.artifact_sha256 for s in seeds] == [out["artifact"]]
    assert seeds[0].intent == "background"
