"""One shareable HTML account of a session, which must not flatten it (#105)."""

from __future__ import annotations

import re

import pytest

from hardy import export


def audit_record(name: str, axioms: list[str], *, assumed: list[str] = ()):
    return {
        "status": "clean",
        "declarations": [{"name": name, "axioms": axioms}],
        "forbidden": [],
        "unapproved": [],
        "assumed": list(assumed),
    }


def material(**overrides):
    fields = {
        "project": "sylow",
        "workspace": "/tmp/sylow",
        "goal": "Classify the Sylow subgroups",
        "assumptions": [],
        "registry": [],
        "audit": {},
        "theorems": {},
        "open": [],
        "lean": {},
        "tex": {},
        "obligations": [],
        "document": "No compiled document was found in this workspace.",
        "usage": [],
        "provenance": {"model": "claude-opus-5", "backend": "claude", "endpoint": "sdk"},
        "toolchain": "lean-4.9.0",
        "environment": "env-abc",
        "transcript": [],
    }
    return {**fields, **overrides}


def build(**overrides):
    return export.build(export.prepare(material(**overrides)))


def results(page: str) -> str:
    """Just the results, so the legend's own badges cannot answer for them."""
    return page.split("<h2>Results</h2>", 1)[1].split("<h2>Standing assumptions</h2>", 1)[0]


def test_the_page_is_self_contained_and_fetches_nothing():
    page = build()
    assert "<!doctype html>" in page
    assert "<style>" in page
    for tag in ("<script", "<link", "<img", "src=", "@import", "url("):
        assert tag not in page, tag
    assert not re.search(r"https?://", page)


def test_three_statuses_are_visibly_different_rather_than_alike():
    page = build(
        theorems={
            "clean_": "theorem clean_ : True",
            "assumed_": "theorem assumed_ : True",
            "open_": "theorem open_ : True",
        },
        open=["open_"],
        audit={
            "A": audit_record("clean_", ["propext"]),
            "B": audit_record("assumed_", ["propext", "big"], assumed=["big"]),
        },
        assumptions=[
            {
                "formal_name": "big",
                "lean_statement": "True",
                "source": "Aschbacher 1.2",
                "reason": "Mathlib does not expose it",
                "status": "user-approved",
                "approved_at": "2026-09-04T10:00:00+00:00",
            }
        ],
    )
    shown = results(page)
    assert 'class="badge verified"' in shown
    assert 'class="badge assumed"' in shown
    assert 'class="badge open"' in shown
    # And the assumption is named where the theorem resting on it is stated.
    assert "user-approved on 2026-09-04T10:00:00+00:00" in shown
    assert "Mathlib does not expose it" in shown


def test_a_theorem_no_verdict_covers_is_shown_as_unaudited_not_as_verified():
    shown = results(build(theorems={"lonely": "theorem lonely : True"}, audit={}))
    assert 'class="badge unaudited"' in shown
    assert 'class="badge verified"' not in shown


def test_a_workspace_with_no_theorem_says_so_rather_than_showing_an_empty_table():
    page = build(transcript=[{"type": "assistant", "message": {"role": "assistant", "content": "Proved it!"}}])
    assert "No theorem is saved in this workspace" in page
    assert "rests on the conversation alone" in page


def test_the_conversation_is_labelled_as_not_evidence():
    page = build(
        transcript=[{"type": "user", "message": {"role": "user", "content": "prove Sylow"}}]
    )
    assert "prove Sylow" in page
    assert "None of it is evidence" in page


def test_the_withheld_material_a_human_wants_is_present():
    page = build(
        usage=["7 turns", "$1.20"],
        transcript=[
            {"type": "model", "reason": "switched", "previous": {"model": "old"}, "model": "new"},
            {
                "type": "tool",
                "name": "save_lean",
                "arguments": {"path": "Work.lean"},
                "result": {"ok": False, "output": "unsolved goals"},
            },
        ],
    )
    assert "$1.20" in page
    assert "old -&gt; new" in page or "old -> new" in page
    assert "unsolved goals" in page


def test_html_in_a_transcript_cannot_escape_into_the_page():
    page = build(
        transcript=[
            {
                "type": "user",
                "message": {"role": "user", "content": "<script>alert(1)</script>"},
            }
        ]
    )
    assert "<script>" not in page
    assert "&lt;script&gt;" in page


def test_known_credential_shapes_are_removed_before_anything_is_written():
    page = build(
        transcript=[
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": (
                        "here is sk-ant-api03-abcdefghijklmnopqrstuvwxyz012345 and "
                        "ghp_abcdefghijklmnopqrstuvwxyz0123 and "
                        "Authorization: Bearer abcdefghijklmnopqrstuvwxyz"
                    ),
                },
            }
        ]
    )
    assert "sk-ant-api03" not in page
    assert "ghp_abcdefghij" not in page
    assert "[REDACTED-KEY]" in page


def test_a_credential_under_a_credential_shaped_key_is_removed_too():
    """The key-name rule the trajectory is already written under."""
    prepared = export.prepare(material(transcript=[{"type": "tool", "api_key": "hunter2"}]))
    assert prepared["transcript"][0]["api_key"] == "[REDACTED]"


def test_the_page_says_what_its_redaction_is_and_is_not():
    page = build()
    assert "That is a filter, not a\nproof" in page or "filter, not a" in page


def test_lean_and_writeup_sources_travel_with_the_export():
    page = build(
        lean={"Work": "theorem t : True := trivial"},
        tex={"tex/writeup.tex": "\\documentclass{article}"},
    )
    assert "theorem t : True := trivial" in page
    assert "documentclass" in page


def test_write_puts_the_page_on_disk(tmp_path):
    target = tmp_path / "out" / "session.html"
    written = export.write(material(), target)
    assert written == target
    assert target.read_text(encoding="utf-8").startswith("<!doctype html>")


def test_the_default_path_names_the_project_and_the_time(tmp_path):
    path = export.default_path(tmp_path, "sylow")
    assert path.parent == tmp_path
    assert path.name.startswith("sylow-") and path.suffix == ".html"


def test_what_hardy_told_the_user_is_in_the_conversation_too():
    """A transcript of the model's replies alone leaves out the half that
    contradicts them."""
    page = build(
        transcript=[
            {"type": "assistant", "message": {"role": "assistant", "content": "Proved it."}},
            {"type": "obligations", "outstanding": ["lean hardyOne: still open"]},
        ]
    )
    assert "what the workspace still owed" in page
    assert "hardyOne: still open" in page


def test_a_refused_tool_call_keeps_both_of_its_classes():
    page = build(
        transcript=[
            {
                "type": "tool",
                "name": "save_lean",
                "result": {"ok": False, "output": "unsolved goals"},
            }
        ]
    )
    assert 'class="tool fail"' in page


def test_an_axiom_no_approval_record_covers_is_called_out_rather_than_glossed():
    page = build(
        theorems={"t": "theorem t : True"},
        audit={"A": audit_record("t", ["propext", "ghost"], assumed=["ghost"])},
        assumptions=[],
    )
    assert "does not list as approved" in results(page)


def test_a_block_structured_message_is_rendered_from_its_text_parts():
    page = build(
        transcript=[
            {
                "type": "assistant",
                "message": {"role": "assistant", "content": [{"type": "text", "text": "hello"}]},
            }
        ]
    )
    assert "hello" in page


def test_a_stale_verdict_is_not_rendered_as_a_verification():
    stale = {
        **audit_record("sylow", ["propext"]),
        "status": "not established",
        "reason": "the toolchain moved since this was established",
        "stale": True,
    }
    page = build(theorems={"sylow": "theorem sylow : True"}, audit={"Work": stale})
    shown = results(page)
    assert "audit no longer established" in shown
    assert 'class="badge verified"' not in shown
    assert "the toolchain moved" in shown


def test_an_assumption_is_attributed_only_to_the_declarations_that_use_it():
    record = {
        "status": "modulo",
        "declarations": [
            {"name": "clean_", "axioms": ["propext"]},
            {"name": "leans_", "axioms": ["propext", "big"]},
        ],
        "forbidden": [],
        "unapproved": [],
        "assumed": ["big"],
    }
    shown = results(
        build(
            theorems={"clean_": "theorem clean_ : True", "leans_": "theorem leans_ : True"},
            audit={"Work": record},
            assumptions=[{"formal_name": "big", "lean_statement": "True", "reason": "r"}],
        )
    )
    clean, leans = shown.split("<code>leans_</code>")
    assert 'class="badge verified"' in clean
    assert "Rests on" not in clean
    assert 'class="badge assumed"' in shown


def test_an_open_theorem_still_names_the_axiom_it_also_rests_on():
    record = {
        "status": "open",
        "declarations": [{"name": "both_", "axioms": ["sorryAx", "big"]}],
        "forbidden": ["sorryAx"],
        "unapproved": [],
        "assumed": ["big"],
    }
    shown = results(
        build(
            theorems={"both_": "theorem both_ : True"},
            audit={"Work": record},
            assumptions=[
                {"formal_name": "big", "lean_statement": "True", "reason": "not in Mathlib"}
            ],
        )
    )
    assert 'class="badge open"' in shown
    assert "not in Mathlib" in shown


def test_an_interrupted_fragment_is_not_rendered_as_a_completed_answer():
    """The runtime records what the user watched arrive as `partial`; an export
    that dropped the flag would show a cut-off sentence as a finished reply."""
    page = build(
        transcript=[
            {
                "type": "assistant",
                "message": {"role": "assistant", "content": "The proof goes by indu"},
                "partial": True,
            },
            {"type": "turn", "status": "cancelled", "reason": "user_pressed_escape"},
        ]
    )
    assert "interrupted, not a completed answer" in page
    assert "This turn was cancelled (user_pressed_escape)" in page


def test_a_wall_clock_limit_is_shown_rather_than_dropped():
    page = build(transcript=[{"type": "wall_clock_limit", "seconds": 600}])
    assert "wall-clock limit fired after 600s" in page


def test_a_completed_reply_carries_no_interruption_note():
    page = build(
        transcript=[{"type": "assistant", "message": {"role": "assistant", "content": "Done."}}]
    )
    assert "interrupted" not in page


def test_a_symlinked_destination_is_refused_rather_than_followed(tmp_path):
    """`report.html -> ~/.bashrc` in a checkout would otherwise overwrite the
    host file on an `/export report.html` that looks entirely local."""
    victim = tmp_path / "victim"
    victim.write_text("do not overwrite me", encoding="utf-8")
    link = tmp_path / "report.html"
    link.symlink_to(victim)
    with pytest.raises(ValueError, match="symlink"):
        export.write(material(), link)
    assert victim.read_text(encoding="utf-8") == "do not overwrite me"


def test_an_ordinary_destination_is_still_written_and_overwritten(tmp_path):
    target = tmp_path / "out.html"
    target.write_text("stale", encoding="utf-8")
    export.write(material(), target)
    assert target.read_text(encoding="utf-8").startswith("<!doctype html>")


def test_a_long_tool_result_keeps_the_end_where_the_diagnostic_is():
    """Lean and Tectonic print setup first and the failure last, so a head
    slice showed a page of imports and not one word of why the call failed."""
    output = "\n".join([f"import Mathlib.Line{index}" for index in range(400)])
    output += "\nerror: unsolved goals\n⊢ False"
    page = build(
        transcript=[
            {"type": "tool", "name": "save_lean", "result": {"ok": False, "output": output}}
        ]
    )
    assert "unsolved goals" in page
    assert "import Mathlib.Line0" not in page          # the head was the part dropped


def test_a_cut_tool_result_says_that_it_was_cut():
    page = build(
        transcript=[
            {"type": "tool", "name": "save_lean", "result": {"ok": False, "output": "x\n" * 5000}}
        ]
    )
    assert "Showing the end of this result" in page
    assert "truncated by" in page


def test_a_short_tool_result_is_shown_whole_and_unannotated():
    page = build(
        transcript=[{"type": "tool", "name": "save_lean", "result": {"ok": True, "output": "saved"}}]
    )
    assert "saved" in page
    assert "Showing the end" not in page
