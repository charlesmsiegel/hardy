from __future__ import annotations

from pathlib import Path

import pytest
from web_fakes import make_problem

from hardy.app.web import uploads


@pytest.mark.parametrize(
    "bad",
    ["", ".", "..", ".hidden", "a/b", "a\\b", "con.lean", "x:y", "trailing ", "nul",
     "con.v2.lean", "nul.tar.gz", "a\x7fb"],
)
def test_safe_name_refuses(bad: str) -> None:
    with pytest.raises(ValueError):
        uploads.safe_name(bad)


def test_safe_name_refuses_overlong() -> None:
    with pytest.raises(ValueError):
        uploads.safe_name("a" * 256 + ".lean")


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


def test_stage_takes_a_name_exclusively_across_threads(tmp_path: Path) -> None:
    """Two tabs dropping `A.lean` at once each get their own file and their
    own bytes: the name is taken by creating it, not by looking first."""
    import threading

    problem = make_problem(tmp_path)
    results: list[dict] = []
    errors: list[BaseException] = []
    go = threading.Barrier(8)

    def drop(index: int) -> None:
        try:
            go.wait(5)
            results.append(uploads.stage(problem, "A.lean", f"-- {index}\n".encode()))
        except BaseException as error:  # noqa: BLE001 - reported below
            errors.append(error)

    threads = [threading.Thread(target=drop, args=(index,)) for index in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    assert errors == []
    names = sorted(result["name"] for result in results)
    assert len(set(names)) == 8 and names[0] == "A-2.lean" and "A.lean" in names
    for result in results:
        assert Path(result["path"]).read_bytes().startswith(b"-- ")
    assert len({Path(result["path"]).read_bytes() for result in results}) == 8


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
