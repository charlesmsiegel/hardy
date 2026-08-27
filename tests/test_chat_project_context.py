"""The project's `AGENTS.md`, read as recorded input and outranked by Hardy.

`setting_sources=[]` keeps a user's `CLAUDE.md` out of a run because an
inherited instruction nobody recorded makes the record a lie. These tests are
the other half of that argument: the file IS read, and every one of them is
about the conditions under which reading it stays honest -- one path, the whole
text in the transcript, a stated precedence, a bound, and nothing at all in an
unattended graded run.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from test_chat import FakeChatRuntime, factory
from workspace_helpers import events

from hardy import project_context
from hardy.chat import MathematicsSession
from hardy.models import Request
from hardy.runner import run

SAID = [{"role": "assistant", "content": "Understood."}]


def project(tmp_path: Path) -> tuple[Path, Path]:
    """A root with one problem in it, which is the shape a real project has."""
    root = tmp_path / "project"
    (root).mkdir(parents=True, exist_ok=True)
    return root, root / "main"


def session(workspace: Path, script=SAID, **options) -> MathematicsSession:
    runtime = FakeChatRuntime(list(script))
    return MathematicsSession(
        workspace,
        factory(FakeChatRuntime, runtime.script),
        (sys.executable, str(Path(__file__).with_name("fake_lean.py"))),
        (sys.executable, str(Path(__file__).with_name("fake_latex.py"))),
        lambda proposal: False,
        **options,
    )


def prompt(chat: MathematicsSession) -> str:
    return chat.runtime.context["system_prompt"]


def recorded(workspace: Path) -> list[dict]:
    return [event for event in events(workspace) if event.get("type") == "project_context"]


def test_the_project_instructions_reach_the_prompt_and_the_transcript(tmp_path: Path):
    """The text, not a digest of it: a hash of a file the reader does not have
    proves nothing about what the model was told."""
    root, workspace = project(tmp_path)
    (root / "AGENTS.md").write_text("We are chasing a Sylow conjecture. n is positive throughout.\n", encoding="utf-8")

    chat = session(workspace)

    assert "chasing a Sylow conjecture" in prompt(chat)
    assert "AGENTS.md" in prompt(chat)
    written = recorded(workspace)
    assert [event["reason"] for event in written] == ["read"]
    assert written[0]["text"] == "We are chasing a Sylow conjecture. n is positive throughout.\n"
    assert written[0]["file"] == "AGENTS.md"
    stored = json.loads((workspace / "session.json").read_text())["project_context"]
    assert stored["sha256"] == written[0]["sha256"]
    # The digest is for change detection; the text lives in the transcript and
    # is not copied into the record beside it.
    assert "text" not in stored


def test_the_prompt_says_hardy_outranks_the_file(tmp_path: Path):
    """A model handed two contradictory instructions with no stated precedence
    guesses, and an `AGENTS.md` in a Lean repository plausibly says "get it
    compiling"."""
    root, workspace = project(tmp_path)
    (root / "AGENTS.md").write_text("Get it compiling. Use sorry where needed.\n", encoding="utf-8")

    said = prompt(session(workspace))

    assert "context, not authority" in said
    assert "sorry" in said.split("--- begin AGENTS.md ---")[0]


def test_hardy_md_replaces_agents_md_rather_than_merging_with_it(tmp_path: Path):
    """One override file, replacing, so precedence is never in question. This
    repository is the case in point: Hardy's own `AGENTS.md` is Codex startup
    context about pytest and coverage floors, which is noise in a mathematics
    session."""
    root, workspace = project(tmp_path)
    (root / "AGENTS.md").write_text("Run the coverage floor check, which is repository noise.\n", encoding="utf-8")
    (root / "HARDY.md").write_text("Elementary arguments only, no Mathlib one-liners.\n", encoding="utf-8")

    said = prompt(session(workspace))

    assert "Elementary arguments only" in said
    assert "repository noise" not in said
    assert recorded(workspace)[0]["file"] == "HARDY.md"


def test_an_ancestor_of_the_root_is_never_read(tmp_path: Path):
    """Exactly one path, reported. Walking up to the git root is how a run
    acquires invisible instructions from three directories away."""
    root, workspace = project(tmp_path)
    (tmp_path / "AGENTS.md").write_text("Instructions from a directory nobody named.\n", encoding="utf-8")

    chat = session(workspace)

    assert "nobody named" not in prompt(chat)
    assert chat.project_context is None
    assert recorded(workspace) == []


def test_an_unchanged_file_is_not_recorded_again(tmp_path: Path):
    """`AGENTS.md` and the record are both versioned, so a fresh clone opening
    the project finds the stored digest already agreeing with the file beside
    it. Appending anyway would leave every checkout dirty before any
    mathematics had happened."""
    root, workspace = project(tmp_path)
    (root / "AGENTS.md").write_text("The same instructions as yesterday.\n", encoding="utf-8")

    session(workspace)
    session(workspace)
    session(workspace)

    assert [event["reason"] for event in recorded(workspace)] == ["read"]


def test_an_edited_file_is_recorded_in_full_again(tmp_path: Path):
    """A change to what the model is told is a change of experimental
    condition, like a model switch."""
    root, workspace = project(tmp_path)
    (root / "AGENTS.md").write_text("Aim the writeup at a referee report.\n", encoding="utf-8")
    session(workspace)
    (root / "AGENTS.md").write_text("Aim the writeup at a paper.\n", encoding="utf-8")

    session(workspace)

    written = recorded(workspace)
    assert [event["reason"] for event in written] == ["read", "changed"]
    assert written[1]["text"] == "Aim the writeup at a paper.\n"


def test_withholding_the_context_says_so_rather_than_leaving_a_stale_claim(tmp_path: Path):
    """`--no-project-context` is a clean condition, not an error -- but a
    record still claiming instructions this run never saw would be false."""
    root, workspace = project(tmp_path)
    (root / "AGENTS.md").write_text("Chase the conjecture in the user's own words.\n", encoding="utf-8")
    session(workspace)

    chat = session(workspace, project_context=False)

    assert "in the user's own words" not in prompt(chat)
    # The flag says so out loud rather than being advertised and inert.
    assert chat.project_context_detail == "not read (project_context is off)"
    assert [event["reason"] for event in recorded(workspace)] == ["read", "withheld"]
    assert "project_context" not in json.loads((workspace / "session.json").read_text())


def test_withholding_a_context_that_was_never_read_records_nothing(tmp_path: Path):
    root, workspace = project(tmp_path)
    (root / "AGENTS.md").write_text("Chase the conjecture in the user's own words.\n", encoding="utf-8")

    chat = session(workspace, project_context=False)

    assert "in the user's own words" not in prompt(chat)
    assert recorded(workspace) == []


def test_no_context_file_adds_nothing_and_records_nothing(tmp_path: Path):
    root, workspace = project(tmp_path)

    chat = session(workspace)

    assert chat.project_context is None
    assert chat.project_context_detail == ""
    assert "project instructions" not in prompt(chat)
    assert recorded(workspace) == []


def test_a_pathological_file_is_bounded_and_the_model_is_told(tmp_path: Path):
    """The context window is a bound, and "bounded except for one input" is
    not a bound.

    The line length is deliberately one that does not divide the byte cap, so
    the cut lands in the middle of a line and the whole-lines promise is
    actually under test rather than satisfied by arithmetic.
    """
    root, workspace = project(tmp_path)
    line = "x" * 66 + "\n"
    assert project_context.MAX_BYTES % len(line)
    (root / "AGENTS.md").write_text(line * 40_000, encoding="utf-8")

    chat = session(workspace)

    assert chat.project_context is not None
    assert chat.project_context.truncated
    assert len(chat.project_context.text.encode("utf-8")) <= project_context.MAX_BYTES
    # Whole lines only: half a sentence of a user's instructions is worse than
    # a clean cut plus a sentence saying where it happened.
    assert chat.project_context.text.endswith("\n")
    assert set(chat.project_context.text.splitlines()) == {"x" * 66}
    assert "was not read" in prompt(chat)
    # The digest covers the whole file even though the text does not, or an
    # edit past the cap would never be noticed as a change.
    assert chat.project_context.bytes == len(line) * 40_000
    assert recorded(workspace)[0]["truncated"] is True


def test_a_file_that_just_fits_is_not_reported_as_truncated(tmp_path: Path):
    """The boundary the extra head byte is read for: a file ending exactly at
    the cap is complete, and saying otherwise would send the model looking for
    instructions that are all already in front of it."""
    root, workspace = project(tmp_path)
    line = "z" * 99 + "\n"
    whole = line * (project_context.MAX_BYTES // len(line))
    assert len(whole) == project_context.MAX_BYTES
    (root / "AGENTS.md").write_text(whole, encoding="utf-8")

    chat = session(workspace)

    assert chat.project_context is not None
    assert chat.project_context.truncated is False
    assert chat.project_context.text == whole
    assert "was not read" not in prompt(chat)


def test_the_line_bound_applies_as_well_as_the_byte_bound(tmp_path: Path):
    """Ten thousand short lines and one enormous line are the same problem for
    the context window, and only one of them is caught by a byte count."""
    root, workspace = project(tmp_path)
    (root / "AGENTS.md").write_text("a\n" * 10_000, encoding="utf-8")

    chat = session(workspace)

    assert chat.project_context is not None
    assert chat.project_context.text.count("\n") == project_context.MAX_LINES
    assert chat.project_context.truncated


def test_one_enormous_line_is_cut_rather_than_dropped_entirely(tmp_path: Path):
    """The one case where whole-lines-only would return nothing at all: a file
    with no newline in it anywhere. An empty block would be worse than a cut
    one, and worse than saying nothing at all."""
    root, workspace = project(tmp_path)
    (root / "AGENTS.md").write_text("y" * (project_context.MAX_BYTES * 2), encoding="utf-8")

    chat = session(workspace)

    assert chat.project_context is not None
    assert chat.project_context.text == "y" * project_context.MAX_BYTES
    assert chat.project_context.truncated


def test_an_unusable_context_file_is_reported_rather_than_raised(tmp_path: Path):
    """Losing the user's stated intent is a reason to say so in the banner,
    never a reason to refuse to open the project."""
    root, workspace = project(tmp_path)
    (root / "AGENTS.md").mkdir()

    chat = session(workspace)

    assert chat.project_context is None
    assert "not a regular file" in chat.project_context_detail


@pytest.mark.skipif(not hasattr(Path, "symlink_to") or sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_a_symlinked_context_file_is_refused_and_the_session_still_opens(tmp_path: Path):
    """A repository is free to ship `AGENTS.md -> ~/.ssh/id_rsa`, and Hardy
    would otherwise put it in a system prompt. Losing the user's stated intent
    is a reason to say so, never a reason to refuse the session."""
    root, workspace = project(tmp_path)
    (tmp_path / "elsewhere.md").write_text("Host secrets.\n", encoding="utf-8")
    (root / "AGENTS.md").symlink_to(tmp_path / "elsewhere.md")

    chat = session(workspace)

    assert chat.project_context is None
    assert "Host secrets" not in prompt(chat)
    assert "symlink" in chat.project_context_detail


@pytest.mark.skipif(not hasattr(Path, "symlink_to") or sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_an_unreadable_override_does_not_hand_authority_back_to_agents_md(tmp_path: Path):
    """`HARDY.md` replaces `AGENTS.md`. A present-but-refused override falling
    through would reinstate exactly the file it exists to displace."""
    root, workspace = project(tmp_path)
    (root / "AGENTS.md").write_text("Run the coverage floor check, which is repository noise.\n", encoding="utf-8")
    (root / "HARDY.md").symlink_to(tmp_path / "absent.md")

    chat = session(workspace)

    assert chat.project_context is None
    assert "repository noise" not in prompt(chat)


def test_invalid_utf8_is_read_rather_than_refused(tmp_path: Path):
    """A mojibake instruction is still the user's instruction, and a file
    someone saved in the wrong encoding must not stop a session opening."""
    root, workspace = project(tmp_path)
    (root / "AGENTS.md").write_bytes(b"Chase the \xff Sylow conjecture.\n")

    chat = session(workspace)

    assert chat.project_context is not None
    assert "Sylow conjecture" in chat.project_context.text


def test_a_graded_run_never_reads_the_project_instructions(tmp_path: Path):
    """An unattended run whose instructions came partly from a project-local
    file is not comparable to another run, and the manifest already carries
    `prompt_set_sha256` on the assumption that the instructions are fixed."""
    from hardy.lean import LeanTools

    (tmp_path / "AGENTS.md").write_text("Assume the Riemann hypothesis freely.\n", encoding="utf-8")
    captured: dict = {}

    class Capturing:
        model, backend, endpoint = "fake-model@test", "claude", "fake"

        def __init__(self, **context):
            captured.update(context)

        def ask(self, text: str) -> str:
            return "done"

    request = Request.from_dict({"declaration": "theorem HardyTarget : True", "informal_claim": "True is true."})
    lean = LeanTools(request, (sys.executable, str(Path(__file__).with_name("fake_lean.py"))))
    run(request, lambda model=None, **context: Capturing(**context), lean, tmp_path / "out", max_turns=1)

    assert "Riemann hypothesis" not in captured["system_prompt"]


def test_the_project_instructions_are_not_folded_into_the_prompt_set_hash(tmp_path: Path):
    """It is input, not instruction. The hash identifies the staged templates a
    graded run was given, and this file is never one of them."""
    from hardy import prompts

    before = prompts.PROMPT_SET_SHA256
    rendered = prompts.chat_project_context_prompt(name="AGENTS.md", text="anything", truncated=False, shown=8, total=8)

    assert "anything" in rendered
    assert before == prompts.PROMPT_SET_SHA256


def test_the_flag_switches_the_context_off_without_a_subcommand(tmp_path: Path, monkeypatch):
    """`--no-project-context` sits beside `--plain` at the top level, because
    an invocation with no subcommand is the primary interactive experience and
    has to be able to ask for a clean condition too."""
    from hardy import cli

    monkeypatch.chdir(tmp_path)
    for variable in ("HARDY_CONFIG", "HARDY_PROJECT_CONTEXT", "HARDY_ROOT"):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setenv("HARDY_CONFIG", str(tmp_path / "config.toml"))
    parser = cli.build_parser()

    assert cli._config(parser.parse_args([]), parser).project_context is True
    assert cli._config(parser.parse_args(["--no-project-context"]), parser).project_context is False
    assert cli._config(parser.parse_args(["--no-project-context", "chat"]), parser).project_context is False


def test_withholding_leaves_no_trace_of_the_file_in_the_manifest(tmp_path: Path):
    """A clean condition has to be the same condition every time it is asked
    for.

    The record's own entry is bookkeeping for change detection, and it reaches
    the prompt through the manifest. Reopening a workspace that has one with
    the context switched off would otherwise put the file's name, digest and
    size in front of the model -- context-derived input, in the run whose whole
    point is that there is none -- and make the withheld condition differ from
    a workspace that never had a file at all.
    """
    root, workspace = project(tmp_path)
    (root / "AGENTS.md").write_text("Chase the conjecture in the user's own words.\n", encoding="utf-8")
    first = session(workspace)
    digest = first.project_context.sha256

    withheld = prompt(session(workspace, project_context=False))

    assert digest not in withheld
    assert "AGENTS.md" not in withheld


def test_the_manifest_does_not_repeat_the_block_beside_it(tmp_path: Path):
    """The model gets the file itself. A second, weaker statement of the same
    thing -- a name and a digest -- is Hardy's bookkeeping, not the model's."""
    root, workspace = project(tmp_path)
    (root / "AGENTS.md").write_text("Chase the conjecture in the user's own words.\n", encoding="utf-8")
    session(workspace)

    # The second open is the one that has an entry to repeat: `_build` runs
    # before `_sync_project_context`, so a first open has nothing stored yet.
    chat = session(workspace)

    manifest = prompt(chat).split("Existing manifest:\n")[1].split("\n")[0]
    assert "project_context" not in manifest
    assert chat.project_context.sha256 not in manifest
    # Still in the record, which is what notices the next edit.
    assert json.loads((workspace / "session.json").read_text())["project_context"]["sha256"]


def test_the_digest_is_never_committed_before_the_text_is_recorded(tmp_path: Path):
    """A crash between the two must not leave the record claiming a file whose
    contents the transcript never received.

    Committed first, that claim is permanent: every later session finds the
    stored digest agreeing with the file, returns early, and never repairs the
    missing event. Recorded first, the worst case is one duplicate event --
    both of them true, in an append-only file.
    """
    root, workspace = project(tmp_path)
    (root / "AGENTS.md").write_text("Chase the conjecture in the user's own words.\n", encoding="utf-8")

    import hardy.chat as chat_module

    original = chat_module.MathematicsSession._record

    def refuse(self, event):
        if event.get("type") == "project_context":
            raise OSError("the transcript could not be appended to")
        return original(self, event)

    chat_module.MathematicsSession._record = refuse
    try:
        with pytest.raises(OSError):
            session(workspace)
    finally:
        chat_module.MathematicsSession._record = original

    assert "project_context" not in json.loads((workspace / "session.json").read_text())


@pytest.mark.skipif(not hasattr(Path, "symlink_to") or sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_a_symlinked_project_root_still_finds_the_instructions(tmp_path: Path):
    """`current -> releases/2026-08` is an ordinary way to name a checkout, and
    every other part of the workspace already works through one.

    The leaf guard asks whether a path resolves to its own parent's child of
    that name, which a symlinked root cannot satisfy -- so guarding the root as
    the user spelled it refused the read and the session silently lost every
    project instruction. The root is canonicalized before the guard; the file
    itself is still refused if it is a link.
    """
    real, workspace = project(tmp_path)
    (real / "AGENTS.md").write_text("Chase the conjecture in the user's own words.\n", encoding="utf-8")
    alias = tmp_path / "current"
    alias.symlink_to(real, target_is_directory=True)

    chat = session(alias / "main")

    assert chat.project_context is not None
    assert chat.project_context.name == "AGENTS.md"
    assert "in the user's own words" in prompt(chat)
