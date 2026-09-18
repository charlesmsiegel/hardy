from __future__ import annotations

from pathlib import Path

import pytest

from hardy.foundation.files import LayoutError
from hardy.workflows import layout


def test_main_chat_uses_the_legacy_paths(tmp_path: Path) -> None:
    paths = layout.Layout(root=tmp_path, slug="sylow")
    assert paths.chat == layout.DEFAULT_CHAT
    assert paths.transcript == tmp_path / "sylow" / "transcript.jsonl"
    assert paths.transcript_dir == tmp_path / "sylow"
    assert paths.local_state == tmp_path / "sylow" / ".local" / "state.json"
    assert paths.local_chat == tmp_path / "sylow" / ".local"
    assert paths.chats_root == tmp_path / "sylow" / "chats"


def test_another_chat_lives_under_chats(tmp_path: Path) -> None:
    paths = layout.Layout(root=tmp_path, slug="sylow", chat="lean-proof")
    assert paths.transcript == tmp_path / "sylow" / "chats" / "lean-proof" / "transcript.jsonl"
    assert paths.transcript_dir == tmp_path / "sylow" / "chats" / "lean-proof"
    assert paths.local_state == tmp_path / "sylow" / ".local" / "chats" / "lean-proof" / "state.json"
    # Shared parts do not move.
    assert paths.record == tmp_path / "sylow" / "session.json"
    assert paths.lean == tmp_path / "sylow" / "lean"
    assert paths.input_history == tmp_path / "sylow" / ".local" / "input-history"


def test_ensure_creates_the_chat_directories(tmp_path: Path) -> None:
    paths = layout.Layout(root=tmp_path, slug="sylow", chat="lean-proof")
    paths.ensure()
    assert paths.transcript_dir.is_dir()
    assert paths.local_chat.is_dir()
    # And the legacy layout is still made for main.
    layout.Layout(root=tmp_path, slug="sylow").ensure()
    assert (tmp_path / "sylow" / ".local").is_dir()


def test_ensure_for_main_does_not_create_chats_dir(tmp_path: Path) -> None:
    layout.Layout(root=tmp_path, slug="sylow").ensure()
    assert not (tmp_path / "sylow" / "chats").exists()


@pytest.mark.parametrize("bad", ["", ".", "..", ".hidden", "a/b", "a\\b", "con", "trailing ", "x:y"])
def test_validate_chat_refuses_bad_names(bad: str) -> None:
    with pytest.raises(LayoutError):
        layout.validate_chat(bad)


def test_validate_chat_accepts_a_slug() -> None:
    assert layout.validate_chat("lean-proof-2") == "lean-proof-2"


def test_session_record_writes_a_chats_transcript(tmp_path: Path) -> None:
    from hardy.workflows.interactive.record import SessionRecord

    problem = tmp_path / "sylow"
    layout.Layout(root=tmp_path, slug="sylow", chat="lean-proof").ensure()
    record = SessionRecord(problem, chat="lean-proof")
    record.load()
    record._record({"type": "user", "message": {"role": "user", "content": "hi"}})
    assert (problem / "chats" / "lean-proof" / "transcript.jsonl").read_text(encoding="utf-8").count("\n") == 1
    assert not (problem / "transcript.jsonl").exists()
    record._save_local()
    assert (problem / ".local" / "chats" / "lean-proof" / "state.json").exists()


def test_config_layout_carries_the_chat(tmp_path: Path) -> None:
    from hardy.app import config as configuration

    config = configuration.Config(
        model="m", lean_command=("lean",), lean_project=None, lean_timeout=1.0,
        latex_command=("pdflatex",), root=tmp_path, project="sylow", chat="lean-proof",
    )
    assert config.layout.transcript == tmp_path / "sylow" / "chats" / "lean-proof" / "transcript.jsonl"


def test_chat_flag_is_parsed() -> None:
    from hardy.app.cli import build_parser

    args = build_parser().parse_args(["chat", "--chat", "lean-proof"])
    assert args.chat == "lean-proof"
    assert build_parser().parse_args(["chat"]).chat is None


def _cli_config(tmp_path: Path):
    from hardy.app import config as configuration

    return configuration.Config(
        model="m", lean_command=("lean",), lean_project=None, lean_timeout=1.0,
        latex_command=("pdflatex",), root=tmp_path, project="sylow",
    )


def test_chat_flag_refuses_a_chat_the_browser_never_made(tmp_path: Path, monkeypatch) -> None:
    """`--chat` names an existing chat; it does not conjure one.

    `prepare_layout` would happily `ensure` `chats/missing/` and leave a
    transcript there with no `chat.json` beside it -- a directory `list_chats`
    ignores, so the browser never shows it and nothing can ever reopen it.
    `prepare_layout` is replaced by a marker here so the refusal can be shown
    to land BEFORE it, rather than being inferred from a missing directory.
    """
    from types import SimpleNamespace

    from hardy.app import cli

    def reached(_config):
        raise RuntimeError("reached prepare_layout")

    monkeypatch.setattr(cli, "prepare_layout", reached)
    with pytest.raises(layout.LayoutError) as excinfo:
        cli._chat(_cli_config(tmp_path), plain=True, args=SimpleNamespace(chat="missing"))
    assert "missing" in str(excinfo.value) and "hardy web" in str(excinfo.value)
    assert not (tmp_path / "sylow" / "chats" / "missing").exists()


def test_web_refuses_the_same_chat_through_the_parser(tmp_path: Path, capsys, monkeypatch) -> None:
    from types import SimpleNamespace

    from hardy.app import cli

    def reached(_config):
        raise RuntimeError("reached prepare_layout")

    from hardy.app.project_registry import Entry, ProjectRegistry

    monkeypatch.setattr(cli, "prepare_layout", reached)
    parser = cli.build_parser()
    config = _cli_config(tmp_path)
    registry = ProjectRegistry(tmp_path / "registry.json", default_root=tmp_path / "projects")
    entry = Entry(path=config.layout.problem, added=0.0, last_opened=None)
    with pytest.raises(SystemExit) as excinfo:
        cli._web(config, parser=parser,
                 args=SimpleNamespace(chat="missing", port=0, open=False),
                 registry=registry, entry=entry)
    assert excinfo.value.code == 2
    assert "hardy web" in capsys.readouterr().err
    assert not (tmp_path / "sylow" / "chats" / "missing").exists()


def test_an_existing_chat_is_accepted_by_the_flag(tmp_path: Path, monkeypatch) -> None:
    """The gate names a chat, not every chat: one the browser made goes through."""
    from types import SimpleNamespace

    from hardy.app import cli
    from hardy.app.web import chats

    def reached(_config):
        raise RuntimeError("reached prepare_layout")

    monkeypatch.setattr(cli, "prepare_layout", reached)
    made = chats.create_chat(tmp_path / "sylow", "Lean proof")
    with pytest.raises(RuntimeError, match="reached prepare_layout"):
        cli._chat(_cli_config(tmp_path), plain=True, args=SimpleNamespace(chat=made.id))


def test_session_record_main_is_the_legacy_transcript(tmp_path: Path) -> None:
    from hardy.workflows.interactive.record import SessionRecord

    problem = tmp_path / "sylow"
    layout.Layout(root=tmp_path, slug="sylow").ensure()
    record = SessionRecord(problem)
    record.load()
    record._record({"type": "user", "message": {"role": "user", "content": "hi"}})
    assert (problem / "transcript.jsonl").exists()
