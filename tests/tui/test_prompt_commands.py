"""A `.hardy/prompts/` file is a `/command`, and it sends its expansion (#101)."""

from __future__ import annotations

import dataclasses

from hardy.prompts import user as templates
from hardy.tui import dispatch, handlers
from hardy.tui.ports import State


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
    from hardy.tui.commands import complete, suggest

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

    from hardy.tui import plain

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
