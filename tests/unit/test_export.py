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
        "imported": [],
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


def test_a_pasted_authorization_header_loses_its_credential_not_its_scheme():
    """The generic rule had this exactly backwards: its unquoted alternative
    matched the SCHEME, so `Basic` was redacted and the base64 stood."""
    page = build(
        transcript=[
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": "Authorization: Basic dXNlcjpwYXNzd29yZA==",
                },
            }
        ]
    )
    assert "dXNlcjpwYXNzd29yZA" not in page
    assert "Authorization: Basic [REDACTED-KEY]" in page


def test_a_short_bearer_token_is_removed_too():
    from hardy.export import redact

    assert "abc12345" not in redact("Authorization: Bearer abc12345")


def test_ordinary_prose_about_a_bearer_survives():
    from hardy.export import redact

    assert redact("the bearer of bad news") == "the bearer of bad news"


def test_a_name_two_modules_declare_is_not_graded():
    """A workspace permits it, and everything downstream addresses a theorem by
    name: the statement shown is whichever module was read last, while the
    verdict is drawn from both."""
    shown = results(
        build(
            theorems={"result": "theorem result : True"},
            audit={"A": audit_record("result", ["propext"])},
            shared={"result": ["A", "B"]},
        )
    )
    assert "not graded" in shown
    assert 'class="badge verified"' not in shown
    assert "A, B" in shown


def test_the_path_reported_is_where_the_write_actually_landed(tmp_path):
    """`O_NOFOLLOW` guards the leaf and nothing above it, so a linked ancestor
    still redirects the write. The destination is allowed to leave the tree --
    that is what an export is for -- so the line a user reads names the real
    file rather than the one they typed."""
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (tmp_path / "exports").symlink_to(elsewhere)
    written = export.write(material(), tmp_path / "exports" / "report.html")
    assert written == elsewhere / "report.html"
    assert (elsewhere / "report.html").is_file()


def test_a_digest_authorization_header_loses_every_field():
    """The value is a set of fields, not one token. A tail that stopped at the
    first quote redacted `username=` and left the response hash standing."""
    cleaned = export.redact(
        'Authorization: Digest username="Mufasa", realm="private", '
        'nonce="dcd98b7102dd2f0e", response="6629fae49393a05397450978507c4ef1"'
    )
    assert "6629fae49393a05397450978507c4ef1" not in cleaned
    assert "Mufasa" not in cleaned
    # The scheme is not a secret and tells the reader what was there.
    assert "Digest" in cleaned


def test_a_quoted_authorization_value_is_redacted_past_its_escaped_quotes():
    """How a header arrives when a JSON payload or a log line is pasted."""
    cleaned = export.redact(
        '{"Authorization": "Digest username=\\"x\\", response=\\"deadbeef\\""}'
    )
    assert "deadbeef" not in cleaned


def test_redaction_stops_at_the_end_of_the_header_line():
    """The value runs to the end of the line; the next line is not a secret."""
    cleaned = export.redact("Authorization: Basic dXNlcjpwYXNz\nlet G be a group")
    assert "dXNlcjpwYXNz" not in cleaned
    assert "let G be a group" in cleaned


def test_the_project_instructions_the_model_was_given_are_on_the_page():
    """`AGENTS.md` is part of the system prompt, so it is part of the
    experimental condition. The transcript keeps the whole text rather than a
    digest for the reason the page has to carry it: the reader of an export
    does not have the file, and a hash of something they cannot see says
    nothing about what was asked for."""
    page = build(transcript=[
        {
            "type": "project_context",
            "reason": "read",
            "file": "AGENTS.md",
            "sha256": "a" * 64,
            "bytes": 42,
            "truncated": False,
            "text": "Prefer Finset over Set throughout.",
        },
        {"role": "user", "content": "Start."},
    ])
    assert "AGENTS.md" in page
    assert "Prefer Finset over Set throughout." in page


def test_a_discarded_provider_thread_is_a_visible_boundary():
    """Without it the page reads as one continuous exchange, and a reply below
    the cut looks like it was written knowing what is above it."""
    page = build(transcript=[
        {"role": "user", "content": "Before."},
        {"type": "thread", "reason": "fresh"},
        {"role": "user", "content": "After."},
    ])
    assert "discarded" in page
    assert "fresh" in page


def test_a_refused_tool_call_shows_what_it_was_asked_to_do():
    """The source of a refused save is nowhere else on the page -- it was never
    saved -- so without the arguments the reader sees Lean's complaint and not
    the proof that drew it."""
    page = build(transcript=[
        {
            "type": "tool",
            "name": "save_lean",
            "arguments": {"path": "Basic.lean", "source": "theorem t : True := by exact"},
            "result": {"ok": False, "output": "unexpected end of input"},
        },
    ])
    assert "theorem t : True := by exact" in page
    assert "Basic.lean" in page


def test_a_tool_call_with_no_arguments_renders_without_an_empty_block():
    page = build(transcript=[
        {"type": "tool", "name": "list_lean", "arguments": {}, "result": {"ok": True, "output": "Basic"}},
    ])
    assert "list_lean" in page
    assert "<p class=\"tool\"></p>" not in page


def test_imported_work_is_not_presented_as_work_authored_here():
    """The sources section shows an imported module exactly like one Hardy
    wrote. The origin and the arriving digest are the only things that let a
    reader check it against the file it came from."""
    page = build(
        imported=[
            {
                "kind": "lean",
                "path": "Sylow.lean",
                "origin": "/home/someone/mathlib-notes/Sylow.lean",
                "sha256": "b" * 64,
            }
        ],
    )
    assert "Sylow.lean" in page
    assert "/home/someone/mathlib-notes/Sylow.lean" in page
    assert "b" * 64 in page


def test_a_session_that_imported_nothing_says_so():
    assert "Nothing was imported" in build()


def test_an_import_is_visible_at_the_point_it_happened():
    page = build(transcript=[
        {
            "type": "imported",
            "kind": "lean",
            "path": "Sylow.lean",
            "origin": "/elsewhere/Sylow.lean",
            "sha256": "c" * 64,
        },
    ])
    assert "Hardy did not write it" in page


def test_a_failed_write_does_not_destroy_the_export_it_was_replacing(tmp_path, monkeypatch):
    """A full disk part way through an O_TRUNC write left half a page of HTML
    that still opens in a browser and still looks like a report."""
    import os

    import pytest

    destination = tmp_path / "report.html"
    destination.write_text("<html>last week's report</html>", encoding="utf-8")
    opened = os.fdopen

    class Full:
        """A stream that runs out of disk on its first write."""

        def __init__(self, stream):
            self._stream = stream

        def __enter__(self):
            return self

        def __exit__(self, *failure):
            self._stream.close()
            return False

        def write(self, text):
            raise OSError(28, "No space left on device")

    monkeypatch.setattr(os, "fdopen", lambda *a, **k: Full(opened(*a, **k)))
    with pytest.raises(OSError):
        export.write(material(), destination)

    assert destination.read_text(encoding="utf-8") == "<html>last week's report</html>"
    assert list(tmp_path.iterdir()) == [destination], "a temporary file was left behind"


def test_a_successful_write_replaces_the_previous_export(tmp_path):
    destination = tmp_path / "report.html"
    destination.write_text("<html>old</html>", encoding="utf-8")

    landed = export.write(material(), destination)

    assert landed == destination.parent.resolve() / destination.name
    assert "<!doctype html>" in destination.read_text(encoding="utf-8")
    assert list(tmp_path.iterdir()) == [destination]


def test_two_exports_in_one_second_do_not_overwrite_each_other(tmp_path):
    """`write` replaces its destination deliberately, so a name Hardy chose
    that lands on one already taken destroys the account it was meant to keep."""
    from datetime import datetime as clock

    when = clock(2026, 9, 4, 12, 0, 0)
    first = export.default_path(tmp_path, "sylow", now=when)
    first.write_text("<html>first</html>", encoding="utf-8")

    second = export.default_path(tmp_path, "sylow", now=when)

    assert second != first
    assert not second.exists()


def test_an_export_is_not_forced_world_readable(tmp_path, monkeypatch):
    """The page holds the whole conversation and every source, and says in its
    own header that redaction is a filter rather than a proof."""
    import os
    import stat

    monkeypatch.setattr(os, "umask", lambda mask: 0o077)
    destination = tmp_path / "report.html"

    export.write(material(), destination)

    assert not stat.S_IMODE(destination.stat().st_mode) & 0o077


def test_replacing_an_export_keeps_the_mode_it_had(tmp_path):
    import stat

    destination = tmp_path / "report.html"
    destination.write_text("<html>old</html>", encoding="utf-8")
    destination.chmod(0o600)

    export.write(material(), destination)

    assert stat.S_IMODE(destination.stat().st_mode) == 0o600


def test_the_evidence_shown_before_an_axiom_was_approved_is_on_the_page():
    """`checked`, `searched` and `previous` reach the confirmation and never
    the stored record, so the transcript event is the only durable copy of what
    the human was actually looking at when they approved."""
    page = build(transcript=[
        {
            "type": "assumption_prompt",
            "formal_name": "sylow_big",
            "checked": "Mathlib.GroupTheory.Sylow has no such statement",
            "searched": "no result for 'sylow subgroup count'",
            "previous": "an earlier version quantified over all groups",
        },
    ])
    assert "Mathlib.GroupTheory.Sylow has no such statement" in page
    assert "an earlier version quantified over all groups" in page
    assert "sylow_big" in page
