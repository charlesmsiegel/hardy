"""`/export`: one shareable file, written from the workspace (#105)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from hardy.tui import handlers
from hardy.tui.ports import State


def session(**overrides):
    material = {
        "project": "workspace",
        "workspace": "/tmp/workspace",
        "goal": "Classify the Sylow subgroups",
        "assumptions": [],
        "registry": [],
        "audit": {},
        "theorems": {"sylow": "theorem sylow : True"},
        "open": [],
        "lean": {"Work": "theorem sylow : True := trivial"},
        "tex": {},
        "obligations": [],
        "document": "No compiled document was found in this workspace.",
        "usage": [],
        "provenance": {"model": "claude-opus-5"},
        "toolchain": "lean-4.9.0",
        "environment": "env-abc",
        "transcript": [],
    }
    material.update(overrides)
    return SimpleNamespace(export_material=lambda: material)


async def test_export_writes_the_named_file(ui, settings, tmp_path):
    target = tmp_path / "session.html"
    await handlers.handle_export(ui, str(target), State(config=settings, session=session()))
    page = target.read_text(encoding="utf-8")
    assert page.startswith("<!doctype html>")
    assert "theorem sylow : True := trivial" in page
    assert str(target) in ui.text


async def test_export_with_no_path_writes_into_the_problem_directory(ui, settings):
    settings.layout.ensure()
    await handlers.handle_export(ui, "", State(config=settings, session=session()))
    written = list(settings.layout.problem.glob("*.html"))
    assert len(written) == 1
    assert written[0].name.startswith(f"{settings.project}-")


async def test_export_into_a_directory_names_the_file_itself(ui, settings, tmp_path, monkeypatch):
    """And names it *there*.

    The name comes from `default_path`, which reserves what it returns by
    creating it. Asking it for a name in one directory and then writing the
    file in another leaves an empty file in the first -- and the first was the
    process's current directory, which is the user's shell, not the workspace.
    """
    elsewhere = tmp_path / "cwd"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    somewhere = tmp_path / "out"
    somewhere.mkdir()
    await handlers.handle_export(ui, str(somewhere), State(config=settings, session=session()))
    assert len(list(somewhere.glob("*.html"))) == 1
    assert list(elsewhere.iterdir()) == []


async def test_export_says_what_the_reader_must_not_assume(ui, settings, tmp_path):
    await handlers.handle_export(
        ui, str(tmp_path / "out.html"), State(config=settings, session=session())
    )
    assert "not evidence" in ui.text
    assert "filter," in ui.text


async def test_export_without_a_session_refuses(ui, settings, tmp_path):
    state = State(config=settings, session=None)
    assert await handlers.handle_export(ui, str(tmp_path / "x.html"), state) is state
    assert "nothing to export" in ui.text


async def test_a_path_that_cannot_be_written_is_a_line_rather_than_a_lost_session(
    ui, settings, tmp_path
):
    blocked = tmp_path / "file"
    blocked.write_text("not a directory", encoding="utf-8")
    state = State(config=settings, session=session())
    assert await handlers.handle_export(ui, str(blocked / "out.html"), state) is state
    assert "Could not write" in ui.text


async def test_export_is_refused_while_a_turn_is_running(settings):
    from hardy.tui import dispatch

    outcome = dispatch.classify("/export", handlers.build_registry(), turn_running=True)
    assert outcome.kind == "refused"


async def test_a_quoted_path_is_unquoted_before_it_is_used(ui, settings, tmp_path):
    target = tmp_path / "with space.html"
    await handlers.handle_export(
        ui, f'"{target}"', State(config=settings, session=session())
    )
    assert Path(target).is_file()


async def test_a_workspace_that_cannot_be_read_is_a_line_rather_than_a_lost_session(
    ui, settings, tmp_path
):
    def boom():
        raise RuntimeError("tex/ is unreadable")

    state = State(config=settings, session=SimpleNamespace(export_material=boom))
    assert await handlers.handle_export(ui, str(tmp_path / "x.html"), state) is state
    assert "tex/ is unreadable" in ui.text


async def test_a_destination_that_cannot_be_reserved_is_a_line_not_a_traceback(
    ui, settings, tmp_path, monkeypatch
):
    """`default_path` reserves the name it returns, so it touches the disk and
    can refuse. The TTY shell happens to catch a handler's exception; the plain
    session does not, so this refusal ended the whole session rather than
    printing the diagnostic the handler was written to print."""
    from hardy import export as export_module

    def refuses(*_arguments, **_keywords):
        raise ValueError("every name this second is taken")

    monkeypatch.setattr(export_module, "default_path", refuses)
    state = State(config=settings, session=session())

    assert await handlers.handle_export(ui, "", state) is state
    assert "Could not write" in ui.text
    assert "every name this second is taken" in ui.text
