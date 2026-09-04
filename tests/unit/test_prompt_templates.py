"""The user's own `/commands`: `.hardy/prompts/<name>.md` (issue #101)."""

from __future__ import annotations

from pathlib import Path

import pytest

from hardy.prompts import PROMPT_SET_SHA256
from hardy.prompts import user as templates


def write(root: Path, name: str, text: str) -> Path:
    directory = templates.directory(root)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(text, encoding="utf-8")
    return path


def test_a_body_with_no_frontmatter_is_the_whole_file():
    parsed = templates.parse("audit", "Audit the workspace.\n")
    assert parsed.body == "Audit the workspace."
    assert parsed.description == ""


def test_frontmatter_supplies_the_description_and_the_argument_hint():
    parsed = templates.parse(
        "formalize",
        "---\ndescription: State it in Lean, do not prove it\nargument-hint: <claim>\n---\n"
        "Formalize $@. Do not prove it.\n",
    )
    assert parsed.description == "State it in Lean, do not prove it"
    assert parsed.argument_hint == "<claim>"
    assert parsed.body == "Formalize $@. Do not prove it."


def test_an_unclosed_frontmatter_marker_is_body_rather_than_a_swallowed_file():
    parsed = templates.parse("rule", "---\nnot really frontmatter\n")
    assert "not really frontmatter" in parsed.body


def test_a_body_that_is_only_frontmatter_is_refused():
    with pytest.raises(templates.TemplateError, match="no body"):
        templates.parse("empty", "---\ndescription: nothing\n---\n\n")


def test_a_name_that_is_not_a_usable_command_is_refused():
    with pytest.raises(templates.TemplateError, match="usable command name"):
        templates.parse("My Prompt", "body")


def test_dollar_at_is_everything_that_was_typed():
    parsed = templates.parse("restate", "Restate $@ in the existing notation.")
    assert templates.expand(parsed, "  Sylow's theorem  ") == (
        "Restate Sylow's theorem in the existing notation."
    )


def test_positional_arguments_are_substituted_in_order():
    parsed = templates.parse("swap", "Try $2 instead of $1 and report what changed.")
    assert templates.expand(parsed, "Set Finset") == (
        "Try Finset instead of Set and report what changed."
    )


def test_quoting_groups_one_positional_argument():
    parsed = templates.parse("one", "[$1]")
    assert templates.expand(parsed, '"the alternating group" rest') == "[the alternating group]"


def test_backslashes_survive_because_the_split_is_not_posix():
    """A mathematician's argument is full of them; POSIX rules would eat each one."""
    parsed = templates.parse("tex", "Prove $1 for $2.")
    assert templates.expand(parsed, r"\mathbb{Z} \forall") == r"Prove \mathbb{Z} for \forall."


def test_a_missing_positional_is_a_refusal_rather_than_an_empty_string():
    parsed = templates.parse(
        "swap", "---\nargument-hint: <from> <to>\n---\nTry $2 instead of $1.", path=Path("x")
    )
    with pytest.raises(templates.TemplateError) as error:
        templates.expand(parsed, "Set")
    assert "$2" in str(error.value)
    assert "<from> <to>" in str(error.value)


def test_a_doubled_dollar_is_a_literal_one_and_latex_math_survives():
    parsed = templates.parse("math", "Show $$1 < 2$$ and $x + y$ hold.")
    assert templates.expand(parsed, "") == "Show $1 < 2$ and $x + y$ hold."


def test_load_reads_every_markdown_file_and_names_them_by_filename(tmp_path):
    write(tmp_path, "audit.md", "Audit the workspace.")
    write(tmp_path, "formalize.md", "---\ndescription: state it\n---\nFormalize $@.")
    write(tmp_path, "notes.txt", "not a template")
    found, problems = templates.load(tmp_path)
    assert [item.name for item in found] == ["audit", "formalize"]
    assert problems == []


def test_load_refuses_a_name_that_would_shadow_a_built_in(tmp_path):
    write(tmp_path, "status.md", "Say something else entirely.")
    found, problems = templates.load(tmp_path, reserved={"status"})
    assert found == []
    assert "shadow" in problems[0] and "/status" in problems[0]


def test_load_reports_a_bad_template_without_losing_the_good_ones(tmp_path):
    write(tmp_path, "audit.md", "Audit the workspace.")
    write(tmp_path, "broken.md", "---\ndescription: nothing\n---\n")
    found, problems = templates.load(tmp_path)
    assert [item.name for item in found] == ["audit"]
    assert "broken.md" in problems[0]


def test_a_root_with_no_prompts_directory_is_not_a_problem(tmp_path):
    assert templates.load(tmp_path) == ([], [])


def test_a_user_template_never_moves_the_recorded_prompt_set_hash(tmp_path):
    """It is input, not instruction: a staged run must not hash differently for it."""
    before = PROMPT_SET_SHA256
    write(tmp_path, "audit.md", "Audit the workspace.")
    templates.load(tmp_path)
    import importlib

    import hardy.prompts as prompts_module

    assert before == importlib.reload(prompts_module).PROMPT_SET_SHA256


def test_a_file_that_cannot_be_decoded_is_reported_rather_than_raised(tmp_path):
    directory = templates.directory(tmp_path)
    directory.mkdir(parents=True)
    (directory / "bad.md").write_bytes(b"\xff\xfe not utf-8")
    found, problems = templates.load(tmp_path)
    assert found == []
    assert "bad.md" in problems[0]


def test_two_files_naming_one_command_keep_the_first_and_report_the_second(tmp_path):
    write(tmp_path, "audit.md", "First.")
    write(tmp_path, "AUDIT.md", "Second.")
    found, problems = templates.load(tmp_path)
    assert [item.name for item in found] == ["audit"]
    assert "already does" in problems[0]


def test_a_directory_that_cannot_be_listed_is_reported_rather_than_raised(tmp_path, monkeypatch):
    templates.directory(tmp_path).mkdir(parents=True)

    def refuse(self):
        raise PermissionError("no")

    monkeypatch.setattr(Path, "iterdir", refuse)
    found, problems = templates.load(tmp_path)
    assert found == []
    assert "Could not read" in problems[0]


def test_dollar_at_with_nothing_to_fill_it_is_a_refusal():
    """`/formalize` with no argument was sending and recording "Formalize ."."""
    parsed = templates.parse("formalize", "Formalize $@. Do not prove it.")
    with pytest.raises(templates.TemplateError) as error:
        templates.expand(parsed, "   ")
    assert "$@" in str(error.value)


def test_dollar_at_with_an_argument_still_expands():
    parsed = templates.parse("formalize", "Formalize $@.")
    assert templates.expand(parsed, "Sylow") == "Formalize Sylow."


def test_a_linked_hardy_directory_is_refused_like_a_linked_prompts_one(tmp_path):
    """`.hardy` itself can be the link. A check on the `prompts` leaf alone
    passed a checkout shipping `.hardy -> elsewhere` with ordinary files
    beneath it, and a template's body is sent to the model."""
    elsewhere = tmp_path / "elsewhere" / "prompts"
    elsewhere.mkdir(parents=True)
    (elsewhere / "audit.md").write_text("Audit the workspace.", encoding="utf-8")
    root = tmp_path / "project"
    root.mkdir()
    templates.directory(root).parent.symlink_to(elsewhere.parent)

    found, problems = templates.load(root)

    assert found == []
    assert "symlink" in problems[0]


def test_inline_mathematics_with_a_zero_is_not_a_placeholder():
    """Positional arguments are one-based, so `$0` is not one -- and matching
    it made `Show $0 < x$` a template that could never expand."""
    parsed = templates.parse("show", "Show $0 < x$ for the base case.")
    assert templates.expand(parsed, "") == "Show $0 < x$ for the base case."


def test_a_positional_index_too_long_to_convert_is_refused_rather_than_raised(tmp_path):
    """Python refuses to turn more than 4,300 digits into an int, and `$`
    followed by 5,000 of them fits inside a template -- so a checked-in file
    could raise `ValueError` where the dispatcher expects `TemplateError`, and
    in a plain session that ends the session."""
    parsed = templates.parse("big", "Show $" + "9" * 5000 + ".")
    with pytest.raises(templates.TemplateError):
        templates.expand(parsed, "one two")


def test_an_ordinary_two_digit_placeholder_still_works():
    parsed = templates.parse("tenth", "$10 came last")
    assert templates.expand(parsed, " ".join(str(n) for n in range(1, 12))) == "10 came last"
