"""A `.hardy/prompts/` file is a `/command`, and it sends its expansion (#101)."""

from __future__ import annotations

import dataclasses

import pytest

from hardy.app.tui import dispatch, handlers
from hardy.app.tui.ports import State
from hardy.prompts import user as templates


@pytest.mark.parametrize(("line", "included"), [
    ("/audit", "current workspace"),
    ("/audit Lemma 4", "Lemma 4"),
    (r"/formalize For all x in \mathbb{Z}, x = x", r"For all x in \mathbb{Z}, x = x"),
    ("/publish Chapter 2", "Chapter 2"),
    ("/restyle Use the notation of Section 1", "Use the notation of Section 1"),
])
def test_bundled_shortcuts_expand_as_recorded_user_input_in_a_real_session(tmp_path, settings, line, included):
    from test_chat import FakeChatRuntime, session

    from hardy.app.tui import plain

    config = dataclasses.replace(settings, root=tmp_path)
    chat = session(tmp_path / "workspace", FakeChatRuntime([]))
    expected = dispatch.classify(line, handlers.build_registry(), turn_running=False)
    assert expected.kind == "send"
    assert included in expected.argument
    before = dict(chat.state)
    inputs = iter([line])

    def read(prompt):
        try:
            return next(inputs)
        except StopIteration as error:
            raise EOFError from error

    plain.run(config, chat, out=lambda text: None, read=read)
    events = list(chat.record._recorded())
    assert [e["message"]["content"] for e in events if e["type"] == "user"] == [expected.argument]
    assert not any(e["type"] in {"tool", "report"} for e in events)
    assert chat.state == before


@pytest.mark.parametrize("name", ["audit", "formalize", "publish", "restyle"])
def test_bundled_shortcuts_remain_refused_during_active_work(name):
    live = handlers.build_registry()
    assert dispatch.classify(f"/{name} target", live, turn_running=True).kind == "refused"
    assert dispatch.classify(f"/{name} target", live, turn_running=False, command_running=True).kind == "refused"


@pytest.mark.parametrize("name", ["formalize", "publish", "restyle"])
def test_shortcuts_require_their_claim_selection_or_style(name):
    outcome = dispatch.classify(f"/{name}", handlers.build_registry(), turn_running=False)
    assert outcome.kind == "refused"
    assert "$@" in outcome.message


async def test_help_distinguishes_bundled_shortcuts_from_project_files(ui, settings):
    await handlers.handle_help(ui, "", State(config=settings, session=None))
    assert "Prompt shortcuts" in ui.text
    assert "/publish" in ui.text
    assert "expanded text" in ui.text
    assert "Your prompts" not in ui.text


@pytest.mark.parametrize("name", ["audit", "formalize", "publish", "restyle"])
def test_project_files_replace_shortcut_defaults_without_duplicate_commands(name, tmp_path, settings):
    directory = templates.directory(tmp_path)
    directory.mkdir(parents=True)
    body = "Use this project's conventions for $@."
    (directory / f"{name}.md").write_text(body, encoding="utf-8")
    loaded, problems = handlers.load_templates(dataclasses.replace(settings, root=tmp_path))
    assert problems == []
    live = handlers.build_registry(loaded)
    assert sum(command.name == name for command in live) == 1
    outcome = dispatch.classify(f"/{name} Lemma 5", live, turn_running=False)
    assert outcome.argument == "Use this project's conventions for Lemma 5."
    # The bundled /audit default never fills a missing project placeholder.
    assert dispatch.classify(f"/{name}", live, turn_running=False).kind == "refused"


def registry(*items: templates.Template):
    return handlers.build_registry(items)


def test_a_template_joins_the_registry_after_the_built_ins():
    audit = templates.parse("audit", "Audit the workspace.")
    names = [command.name for command in registry(audit)]
    assert names[-1] == "audit"
    assert names[:2] == ["help", "model"]


def test_a_template_line_resolves_to_a_message_carrying_the_expansion():
    """The `/name` never reaches the model or the transcript; the text does."""
    audit = templates.parse("swap", "Try $1 instead of Set.")
    outcome = dispatch.classify("/swap Finset", registry(audit), turn_running=False)
    assert outcome.kind == "send"
    assert outcome.argument == "Try Finset instead of Set."


def test_a_template_missing_an_argument_is_refused_rather_than_sent_half_empty():
    swap = templates.parse("swap", "Try $2 instead of $1.")
    outcome = dispatch.classify("/swap Set", registry(swap), turn_running=False)
    assert outcome.kind == "refused"
    assert "$2" in outcome.message


def test_a_template_is_refused_while_a_turn_runs_exactly_as_a_message_is():
    audit = templates.parse("audit", "Audit the workspace.")
    outcome = dispatch.classify("/audit", registry(audit), turn_running=True)
    assert outcome.kind == "refused"
    assert "still running" in outcome.message


def test_completion_and_ghost_text_reach_a_template_like_any_other_command():
    from hardy.app.tui.commands import complete, suggest

    audit = templates.parse("audit", "Audit the workspace.")
    assert suggest("/aud", registry(audit)) == "it"
    assert [c.name for c in complete("/au", registry(audit))] == ["audit"]


def test_the_loader_refuses_a_file_that_would_shadow_a_built_in(tmp_path, settings):
    directory = templates.directory(tmp_path)
    directory.mkdir(parents=True)
    (directory / "exit.md").write_text("Do not leave.", encoding="utf-8")
    (directory / "audit.md").write_text("Audit the workspace.", encoding="utf-8")
    found, problems = handlers.load_templates(dataclasses.replace(settings, root=tmp_path))
    assert [item.name for item in found] == ["audit"]
    assert "/exit" in problems[0]


async def test_help_lists_the_user_prompts_under_their_own_heading(ui, settings):
    audit = templates.parse("audit", "---\ndescription: audit it\n---\nAudit the workspace.")
    live = registry(audit)
    await handlers.handle_help(
        ui, "", State(config=settings, session=None, commands=tuple(live))
    )
    assert "Your prompts" in ui.text
    assert "/audit" in ui.text and "audit it" in ui.text
    assert "records the expanded text" in ui.text


async def test_help_with_no_user_prompts_says_nothing_about_them(ui, settings):
    await handlers.handle_help(ui, "", State(config=settings, session=None))
    assert "Your prompts" not in ui.text


def test_the_transcript_records_the_expansion_rather_than_the_name(tmp_path, settings):
    """The record has to be readable without the template file that produced it."""
    import dataclasses
    import json

    from hardy.app.tui import plain

    directory = templates.directory(tmp_path)
    directory.mkdir(parents=True)
    (directory / "audit.md").write_text(
        "Audit the workspace: every theorem, what it rests on, whether $1 resolves.",
        encoding="utf-8",
    )
    config = dataclasses.replace(settings, root=tmp_path)
    recorded: list[dict] = []

    class Session:
        def stream(self, text: str):
            recorded.append({"type": "user", "message": {"role": "user", "content": text}})
            return iter(())

        def cancel(self, reason: str = "user_cancelled") -> None:
            pass

    lines = iter(["/audit the-registry"])

    def read(prompt: str) -> str:
        try:
            return next(lines)
        except StopIteration as stop:
            raise EOFError from stop

    plain.run(config, Session(), out=lambda text: None, read=read)
    assert recorded == [
        {
            "type": "user",
            "message": {
                "role": "user",
                "content": (
                    "Audit the workspace: every theorem, what it rests on, "
                    "whether the-registry resolves."
                ),
            },
        }
    ]
    assert "/audit" not in json.dumps(recorded)
