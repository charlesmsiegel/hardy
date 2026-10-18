"""The browser's list of projects: one file under `~/.hardy`, one entry per problem directory."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

import pytest
from web_fakes import make_problem

from hardy.app import project_registry
from hardy.app.project_registry import SCHEMA, ProjectRegistry
from hardy.foundation.locking import FileLock, LockTimeout
from hardy.workflows import layout


def _registry(tmp_path: Path, **kw) -> ProjectRegistry:
    return ProjectRegistry(
        tmp_path / "home" / ".hardy" / "projects.json",
        default_root=tmp_path / "home" / ".hardy" / "projects",
        **kw,
    )


def test_a_missing_file_is_an_empty_registry(tmp_path: Path) -> None:
    assert _registry(tmp_path).entries() == []
    assert _registry(tmp_path).last_opened() is None


def test_add_one_problem_records_its_path_and_derives_slug_and_root(tmp_path: Path) -> None:
    problem = make_problem(tmp_path / "math", "sylow")
    clock = iter([100.0, 200.0])
    registry = _registry(tmp_path, now=lambda: next(clock))
    [entry] = registry.add(problem)
    assert entry.path == problem.resolve() and entry.slug == "sylow"
    assert entry.root == problem.resolve().parent
    assert entry.added == 100.0 and entry.last_opened is None
    written = json.loads((tmp_path / "home" / ".hardy" / "projects.json").read_text(encoding="utf-8"))
    assert written["schema"] == SCHEMA and written["last_opened"] is None
    assert written["projects"] == [{"path": str(problem.resolve()), "added": 100.0, "last_opened": None}]
    # Adding again changes nothing, not even the stamp.
    assert registry.add(problem) == [entry]


def test_add_a_root_adds_every_recorded_problem_in_it(tmp_path: Path) -> None:
    root = tmp_path / "math"
    make_problem(root, "sylow")
    make_problem(root, "burnside")
    (root / "notes").mkdir()
    entries = _registry(tmp_path).add(root)
    assert [entry.slug for entry in entries] == ["burnside", "sylow"]


def test_add_refuses_a_directory_that_is_not_a_project_and_holds_none(tmp_path: Path) -> None:
    (tmp_path / "empty").mkdir()
    with pytest.raises(ValueError, match="not a Hardy project and holds none"):
        _registry(tmp_path).add(tmp_path / "empty")
    with pytest.raises(ValueError, match="does not exist"):
        _registry(tmp_path).add(tmp_path / "missing")


def test_add_refuses_a_problem_whose_directory_name_is_not_a_slug(tmp_path: Path) -> None:
    bad = tmp_path / "math" / ".hidden"
    bad.mkdir(parents=True)
    (bad / layout.RECORD).write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError):
        _registry(tmp_path).add(bad)
    assert _registry(tmp_path).entries() == []


def test_touch_marks_last_opened_and_adds_an_unknown_path(tmp_path: Path) -> None:
    problem = make_problem(tmp_path / "math", "sylow")
    clock = iter([1.0, 2.0, 3.0])
    registry = _registry(tmp_path, now=lambda: next(clock))
    [entry] = registry.touch(problem)
    assert entry.last_opened == 1.0 and entry.added == 1.0
    assert registry.last_opened() == entry
    registry.touch(problem)
    assert registry.entries()[0].last_opened == 2.0


def test_last_opened_is_withheld_once_the_record_is_gone(tmp_path: Path) -> None:
    problem = make_problem(tmp_path / "math", "sylow")
    registry = _registry(tmp_path)
    registry.touch(problem)
    (problem / layout.RECORD).unlink()
    assert registry.last_opened() is None
    # The entry itself is kept; only the default open is withheld.
    assert [entry.slug for entry in registry.entries()] == ["sylow"]


def test_forget_removes_the_entry_and_never_the_directory(tmp_path: Path) -> None:
    problem = make_problem(tmp_path / "math", "sylow")
    registry = _registry(tmp_path)
    registry.touch(problem)
    assert registry.forget(problem) == []
    assert problem.is_dir()
    assert registry.last_opened() is None
    assert registry.forget(problem) == []


def test_find_by_slug_or_path_and_the_ambiguity_refusal(tmp_path: Path) -> None:
    a = make_problem(tmp_path / "one", "main")
    b = make_problem(tmp_path / "two", "main")
    registry = _registry(tmp_path)
    registry.add(a)
    registry.add(b)
    assert registry.find(str(b)).path == b.resolve()
    with pytest.raises(ValueError) as refused:
        registry.find("main")
    assert str(a.resolve()) in str(refused.value) and str(b.resolve()) in str(refused.value)
    assert registry.find("nothing") is None
    registry.forget(b)
    assert registry.find("main").path == a.resolve()


def test_create_path_uses_the_default_root_unless_told_otherwise(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    assert registry.create_path("sylow") == (tmp_path / "home" / ".hardy" / "projects").resolve() / "sylow"
    assert registry.create_path("sylow", tmp_path / "elsewhere") == (tmp_path / "elsewhere").resolve() / "sylow"
    with pytest.raises(layout.LayoutError):
        registry.create_path("../escape")


def test_an_unreadable_file_is_refused_with_its_path(tmp_path: Path) -> None:
    path = tmp_path / "home" / ".hardy" / "projects.json"
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError) as refused:
        _registry(tmp_path).entries()
    assert str(path) in str(refused.value)
    path.write_text(json.dumps({"schema": "hardy.projects/v9", "projects": []}), encoding="utf-8")
    with pytest.raises(ValueError, match="hardy.projects/v9"):
        _registry(tmp_path).entries()


@pytest.mark.parametrize("operation", ["add", "touch", "forget"])
def test_registry_instances_share_the_whole_write_transaction(tmp_path: Path, monkeypatch, operation):
    first, second = _registry(tmp_path), _registry(tmp_path)
    a = make_problem(tmp_path / "math", "sylow")
    b = make_problem(tmp_path / "math", "burnside")
    if operation == "forget":
        first.touch(a)
    read_started, second_started, second_done = Event(), Event(), Event()
    original_read = first._read

    def hold_snapshot():
        data = original_read()
        read_started.set()
        assert second_started.wait(5), "second writer never started"
        # An independent writer used to finish while this snapshot was held.
        # A shared transaction lock makes it wait until this write commits.
        second_done.wait(1)
        return data

    def add_other_project():
        second_started.set()
        try:
            second.touch(b)
        finally:
            second_done.set()

    monkeypatch.setattr(first, "_read", hold_snapshot)
    with ThreadPoolExecutor(max_workers=2) as writers:
        pending = writers.submit(getattr(first, operation), a)
        assert read_started.wait(5), "first writer never read its snapshot"
        other = writers.submit(add_other_project)
        pending.result(timeout=10)
        other.result(timeout=10)
    expected = {b.resolve()} if operation == "forget" else {a.resolve(), b.resolve()}
    assert {entry.path for entry in _registry(tmp_path).entries()} == expected


@pytest.mark.parametrize("operation", ["add", "touch", "forget"])
def test_busy_registry_lock_refuses_writes_and_releases_afterward(tmp_path, monkeypatch, operation):
    registry = _registry(tmp_path)
    a = make_problem(tmp_path / "math", "sylow")
    b = make_problem(tmp_path / "math", "burnside")
    registry.touch(a)
    before = registry.path.read_bytes()
    monkeypatch.setattr(project_registry, "FileLock", lambda path: FileLock(path, timeout=0))
    with FileLock(registry.path.with_name(registry.path.name + ".lock")):
        with pytest.raises(LockTimeout):
            getattr(registry, operation)(a if operation == "forget" else b)
        assert registry.path.read_bytes() == before
    registry.touch(b)
    assert {entry.path for entry in registry.entries()} == {a.resolve(), b.resolve()}
