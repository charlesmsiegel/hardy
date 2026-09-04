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
        "automation": {},
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


def test_a_bearer_token_is_removed_whole_and_not_up_to_its_first_base64_character():
    """The alphabet is base64's, not an identifier's.

    `+`, `/` and `=` are ordinary characters in an encoded credential and in
    the padding of a JWT. A class that stopped at the first of them redacted
    the head of the token and left the rest of it on the page, which reads as
    a redaction having happened and is worse than none.
    """
    from hardy.export import redact

    for token in ("abc+123/==", "eyJhbGci.eyJzdWIi.dBjftJeZ4-x_A~9s"):
        # No `Authorization:` in front of it: that key would be redacted by the
        # pair rule and the test would pass without the shape rule doing
        # anything. A bare header value is what this is about.
        cleaned = redact(f"sent Bearer {token} upstream")
        assert token not in cleaned
        for piece in token.replace("+", " ").replace("/", " ").replace(".", " ").split():
            assert piece not in cleaned
        assert "[REDACTED-KEY]" in cleaned


def test_the_wider_alphabet_does_not_start_eating_prose():
    """`+`, `/` and `=` do not appear in the middle of an English word, so the
    words after "bearer" are still words and still have no digit in them."""
    from hardy.export import redact

    assert redact("the bearer bond matured") == "the bearer bond matured"
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


def test_no_recorded_import_is_not_a_claim_that_hardy_wrote_everything():
    """Editing the Lean and TeX directly is supported and untracked, and a
    workspace from before import tracking records nothing either -- so absence
    of provenance is not evidence of authorship."""
    page = build()
    assert "No import was recorded" in page
    assert "everything below was written in this session" not in page


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
    # Reserved rather than merely unused: two sessions exporting in the same
    # second both passed a bare existence test and picked the same name.
    assert second.exists() and second.stat().st_size == 0


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


def test_a_short_bearer_token_is_still_a_token():
    """The comment beside the rule named this exact six-character example as
    something that must not pass, while the rule let it through."""
    assert "abc123" not in export.redact("Bearer abc123")


def test_an_identity_shows_its_separators_rather_than_swallowing_them():
    """`_toolchain_identity` joins with NUL. Written into HTML those bytes are
    not displayed, so the page showed something other than the exact identity
    the audit was established under -- for a value whose whole job is to be
    compared, the one thing it must not do."""
    page = build(toolchain="leanprover/lean4:v4.9.0\x00abc123")

    assert "\x00" not in page
    assert "leanprover/lean4:v4.9.0\\0abc123" in page


def test_a_destination_that_is_not_an_ordinary_file_is_refused(tmp_path):
    """`os.replace` onto a fifo unlinks it and puts an HTML file where another
    process's IPC endpoint was."""
    import os

    import pytest

    destination = tmp_path / "report.html"
    os.mkfifo(destination)

    with pytest.raises(ValueError, match="not an ordinary file"):
        export.write(material(), destination)

    assert destination.is_fifo(), "the fifo was destroyed anyway"


def test_a_quoted_credential_with_an_escaped_quote_is_redacted_whole():
    """The value ended at the escaped quote, so the redaction replaced the
    prefix and left the rest of the credential standing."""
    cleaned = export.redact('{"api_key": "he\\"re"}')
    assert "he" not in cleaned.replace("[REDACTED]", "")
    assert cleaned == '{"api_key": [REDACTED]}'


def test_a_report_carries_the_statement_it_was_about():
    """The Results section shows the statement the tree has now, so a source
    edited afterwards makes the old call look like it reported that."""
    page = build(transcript=[
        {
            "type": "report",
            "theorems": ["sylow"],
            "statements": {"sylow": "theorem sylow : True"},
            "assumptions": ["big"],
            "status": "modulo",
            "open": [],
        },
    ])
    assert "theorem sylow : True" in page
    assert "resting on big" in page
    assert "as it was at the time of the report" in page


def test_a_theorem_one_tactic_closed_says_so_beside_its_verdict():
    """The compiled document's banner carries this and the export embeds no
    PDF, so the page was dropping a warning the workspace holds about the very
    result it presents."""
    page = build(
        theorems={"sylow": "theorem sylow : True"},
        audit={"A": audit_record("sylow", ["propext"])},
        automation={"sylow": "aesop"},
    )
    shown = results(page)
    assert "Closed by a single automation call" in shown
    assert "aesop" in shown
    # Still kernel-verified: what one tactic closes the kernel still checked.
    assert 'class="badge verified"' in shown


def test_audited_lean_is_shown_exactly_as_the_kernel_checked_it():
    """Lean writes a type ascription with a colon, so the key/value credential
    rule rewrote ordinary Lean -- and the page then displayed and badged a
    statement that was not the one the kernel graded."""
    statement = "theorem secret : Nat = Nat := rfl"
    page = build(
        theorems={"secret": statement},
        audit={"A": audit_record("secret", ["propext"])},
        lean={"Work": "structure S where\n  password : String\n"},
    )
    assert statement in page
    assert "password : String" in page
    assert "[REDACTED]" not in results(page)


def test_an_audited_source_is_exported_with_nothing_rewritten():
    """An earlier version of this test asserted the opposite, on the reasoning
    that a token shape could not occur in valid Lean by accident. It can:
    `theorem t : "Bearer abc123" = "Bearer abc123" := rfl` is a proposition
    ABOUT a string literal, and rewriting it exports a different proposition
    from the one the kernel checked.

    The cost is stated rather than hidden: a credential a user pastes into
    their own `.lean` file travels with the export, and the page's header says
    the redaction is a filter and not a proof. Altering what Lean checked is
    the worse failure -- it is the one thing the page exists to get right.
    """
    statement = 'theorem t : "Bearer abc123" = "Bearer abc123" := rfl'
    page = build(theorems={"t": statement}, lean={"Work": statement})

    assert page.count("Bearer abc123") >= 2
    assert "[REDACTED-KEY]" not in results(page)


def test_a_writeup_source_is_not_an_audited_artifact():
    """`.tex` is prose the user wrote, not something the kernel graded, so a
    credential in one is a credential -- exempting the writeup along with the
    Lean turned the fidelity fix into a leak."""
    page = build(tex={"tex/paper.tex": "% password: hunter2\n"})
    assert "hunter2" not in page


def test_a_turn_with_no_saved_theorem_keeps_its_warning():
    """An empty obligations list means two different things, and the wrong one
    turns Hardy's warning into apparent completion."""
    page = build(transcript=[
        {"type": "assistant", "message": {"role": "assistant", "content": "Proved it."}},
        {"type": "obligations", "outstanding": [], "saved_theorems": 0},
    ])
    assert "nothing here is reportable" in page
    assert "Nothing outstanding." not in page


def test_a_tool_the_sdk_refused_is_reported_as_refused():
    """A request for `Read` or `Bash` is recorded as `refused_tool`, not as a
    failed `tool`, so the section printed "Nothing was refused" over a run in
    which the model had reached for the host."""
    page = build(transcript=[{"type": "refused_tool", "name": "Bash"}])
    assert "Bash" in page
    assert "not a Hardy tool" in page


def test_a_model_switch_is_marked_where_it_happened():
    page = build(transcript=[
        {"type": "user", "message": {"role": "user", "content": "before"}},
        {"type": "model", "reason": "switched", "previous": {"model": "old"}, "model": "new"},
        {"type": "user", "message": {"role": "user", "content": "after"}},
    ])
    assert "The model changed here" in page


def test_a_theorem_named_like_a_credential_keeps_its_statement():
    """`prepare`'s key-name rule reads `theorems["secret"]` as a credential
    under a key called `secret`. It is a theorem called `secret`, and replacing
    its statement makes the page disagree with what the kernel checked."""
    prepared = export.prepare(material(theorems={"secret": "theorem secret : True"}))
    assert prepared["theorems"]["secret"] == "theorem secret : True"


def test_a_real_credential_key_is_still_redacted_structurally():
    """The exemption is only for the maps keyed by declaration or path."""
    prepared = export.prepare(material(transcript=[{"type": "tool", "password": "hunter2"}]))
    assert prepared["transcript"][0]["password"] == "[REDACTED]"


def test_an_approved_axiom_is_stated_exactly_as_it_was_approved():
    """`axiom secret : True` is a valid axiom named `secret`, and the key/value
    rule rewrote it -- misstating the assumption the page says its
    verified-modulo results depend on."""
    page = build(
        assumptions=[
            {
                "formal_name": "secret",
                "lean_statement": "True",
                "source": "Aschbacher 1.2",
                "reason": "Mathlib does not expose it",
                "status": "user-approved",
            }
        ],
    )
    assert "axiom secret : True" in page


def test_an_empty_workspace_is_not_reported_as_finished():
    """"Nothing outstanding: every saved theorem is written up" over a
    workspace with no theorem presents emptiness as completion."""
    page = build(theorems={}, obligations=[])
    assert "nothing here is reportable" in page
    assert "every saved theorem is written up" not in page


def test_a_module_named_like_a_credential_does_not_break_the_export():
    """`storage.SECRET_KEY` matches a key exactly, so a module called `Secret`
    had its whole audit RECORD replaced by the string `[REDACTED]` -- and
    `declaration_status` then called `.get()` on a string, so `/export` raised
    instead of writing a page."""
    page = build(
        theorems={"t": "theorem t : True"},
        audit={"Secret": audit_record("t", ["propext"])},
    )
    assert 'class="badge verified"' in results(page)


def test_a_theorem_named_like_a_credential_keeps_its_automation_note():
    prepared = export.prepare(material(automation={"secret": "aesop"}))
    assert prepared["automation"]["secret"] == "aesop"


def test_a_reported_statement_survives_the_structural_pass():
    """`statements` is keyed by theorem name and is the only durable copy of
    what was reported once the source moves on."""
    prepared = export.prepare(
        material(
            transcript=[
                {
                    "type": "report",
                    "theorems": ["secret"],
                    "statements": {"secret": "theorem secret : True"},
                    "status": "clean",
                }
            ]
        )
    )
    assert prepared["transcript"][0]["statements"]["secret"] == "theorem secret : True"


def test_a_reported_statement_is_rendered_verbatim():
    page = build(transcript=[
        {
            "type": "report",
            "theorems": ["secret"],
            "statements": {"secret": "theorem secret : True"},
            "status": "clean",
        }
    ])
    assert "theorem secret : True" in page


def test_a_real_credential_inside_a_transcript_event_is_still_redacted():
    """The exemption is for name-keyed maps, not for the transcript at large."""
    prepared = export.prepare(
        material(transcript=[{"type": "tool", "password": "hunter2"}])
    )
    assert prepared["transcript"][0]["password"] == "[REDACTED]"


def test_the_page_says_the_formal_sources_are_exempt_from_redaction():
    """The header cannot promise a filter it deliberately does not apply.

    An audited Lean source is rendered with nothing rewritten, on purpose: a
    page whose Lean no longer hashes to what the kernel saw is worse than one
    carrying a string that was never a credential. That is a real exception to
    the sentence above it, and a reader deciding whether an export is safe to
    share has to be told about it rather than left to infer it.
    """
    page = build()
    # Whitespace-collapsed: the paragraph is wrapped in the source, so a
    # sentence to assert on straddles a newline.
    exemption = " ".join(
        page.split("That is a filter", 1)[1].split("<h2>Goal</h2>", 1)[0].split()
    )
    assert "exempt" in exemption
    assert "Lean modules" in exemption
    assert "reaches this file intact" in exemption


def test_a_recent_refusal_is_not_pushed_out_by_older_denials():
    """Order first, clip second.

    Gathering failed tool calls and SDK denials in two passes and concatenating
    them put every denial after every failure whatever the times were, so the
    newest-fifty slice kept the newest fifty of a list that was not in time
    order -- and the failure the reader most needs went missing.
    """
    transcript = [
        {"type": "refused_tool", "name": f"Bash{index}"} for index in range(50)
    ]
    transcript.append(
        {
            "type": "tool",
            "name": "save_lean",
            "result": {"ok": False, "output": "Lean rejected the proof"},
        }
    )
    page = build(transcript=transcript)
    # The section itself, not the page: the same call is also rendered in the
    # conversation below, which would answer for this assertion without the
    # withheld section listing it at all.
    listed = page.split("<h2>Tool calls Hardy refused</h2>", 1)[1].split("<h2>", 1)[0]
    assert "save_lean" in listed
    assert "Lean rejected the proof" in listed


def test_a_wall_of_denials_cannot_hide_the_failure_that_stopped_the_work():
    """Both compete for the fifty slots and both have to survive.

    The denials are what a reader is here for; the newest failures are what
    says why the work stopped. Filling the cap with denials dropped the current
    Lean complaint, and filling it with failures dropped the evidence that the
    model reached for the host.
    """
    transcript = [{"type": "refused_tool", "name": f"Bash{index}"} for index in range(50)] + [
        {
            "type": "tool",
            "name": f"save{index}",
            "result": {"ok": False, "output": "no"},
        }
        for index in range(60)
    ]
    listed = (
        build(transcript=transcript)
        .split("<h2>Tool calls Hardy refused</h2>", 1)[1]
        .split("<h2>", 1)[0]
    )
    assert listed.count("<li>") <= 50
    assert "save59" in listed, "the newest failure was pushed out by older denials"
    assert "not a Hardy tool" in listed, "the denials were pushed out by newer failures"
    assert "not listed here" in listed


def test_an_outstanding_obligation_is_the_sentence_the_user_was_shown():
    """The event carries `Obligation.as_dict`, not its string."""
    page = build(
        transcript=[
            {
                "type": "obligations",
                "outstanding": [
                    {"kind": "open", "subject": "sylow", "detail": "the proof has a hole"}
                ],
            }
        ]
    )
    assert "sylow: the proof has a hole" in page
    assert "'kind'" not in page
    assert "&#x27;kind&#x27;" not in page


def test_the_project_instructions_are_exported_whole():
    """Not through the tool-result clipper.

    `project_context` bounds itself at 50,000 bytes from the head; the clipper
    keeps 4,000 from the tail. Sending one through the other showed the reader
    the middle of the file, labelled as its end -- and the text is the system
    prompt, so an export that cannot reproduce it cannot be used to judge the
    replies made under it.
    """
    text = "\n".join(f"instruction line {index}" for index in range(2000))
    page = build(
        transcript=[
            {"type": "project_context", "reason": "read", "file": "AGENTS.md", "text": text}
        ]
    )
    assert "instruction line 0" in page
    assert "instruction line 1999" in page
    assert "Showing the end of this result" not in page


def test_an_unquoted_credential_is_removed_past_its_first_space():
    """A passphrase may contain spaces, and an unquoted YAML scalar keeps them.

    Stopping the value at the first space published three words of a four-word
    passphrase under a `[REDACTED]` that told the reader it had been handled --
    the worst of both, since the page looked filtered.
    """
    from hardy.export import redact

    cleaned = redact("password: correct horse battery staple")
    for word in ("correct", "horse", "battery", "staple"):
        assert word not in cleaned


def test_widening_the_value_did_not_eat_the_scheme_word():
    """`\\s*[:=]\\s*` can give back its trailing space.

    A value allowed to begin with one then matched " Basic <token>" from a
    separator of just ":", sliding past the lookahead that keeps the scheme
    word and eating what the authorization rule deliberately left. `\\S+` could
    not start with a space, so this only became reachable when the value was
    widened.
    """
    from hardy.export import redact

    assert redact("Authorization: Basic zzz") == "Authorization: Basic [REDACTED-KEY]"


def test_a_quoted_value_still_stops_at_its_closing_quote():
    """Otherwise the wider unquoted rule would take the rest of a JSON line."""
    from hardy.export import redact

    cleaned = redact('{"api_key": "abc", "user": "bob"}')
    assert "abc" not in cleaned
    assert '"user": "bob"' in cleaned


def test_the_page_does_not_claim_the_writeup_tex_is_exempt_from_redaction():
    """Only the formal sources are verbatim.

    A `.tex` file is prose the user wrote rather than something the kernel
    graded, so it goes through the filter -- and a notice saying otherwise
    would invite sharing a writeup carrying a credential.
    """
    page = build(tex={"paper.tex": "% password: hunter2\n\\section{Sylow}"})
    exemption = page.split("That is a filter", 1)[1].split("<h2>Goal</h2>", 1)[0]
    assert "not</em> exempt" in exemption or "<em>not</em> exempt" in exemption
    assert "hunter2" not in page


def test_refused_calls_are_not_filed_under_what_the_model_never_saw():
    """The dispatcher hands a failed `ToolResult` back to the provider.

    Listing those under "Withheld from the model" told the reader a failed
    proof attempt had carried on with no feedback, when the model read Lean's
    complaint and was meant to act on it.
    """
    page = build(
        transcript=[
            {
                "type": "tool",
                "name": "save_lean",
                "result": {"ok": False, "output": "Lean rejected the proof"},
            }
        ]
    )
    withheld = page.split("<h2>Withheld from the model</h2>", 1)[1].split("<h2>", 1)[0]
    assert "save_lean" not in withheld
    refusals = page.split("<h2>Tool calls Hardy refused</h2>", 1)[1].split("<h2>", 1)[0]
    assert "save_lean" in refusals


def test_an_approval_carries_the_goal_it_was_given_for():
    """`/goal` overwrites a singleton.

    The goal printed at the top of the page is the workspace's current one. An
    axiom approved before the goal moved was not approved for that question,
    and a page that showed only the new goal above the standing assumptions
    credited the approval to something nobody was asked about.
    """
    page = build(
        goal="Classify the Sylow subgroups",
        assumptions=[
            {
                "formal_name": "chebotarev",
                "lean_statement": "True",
                "informal_statement": "Chebotarev density",
                "source": "Neukirch VII.13.4",
                "reason": "standard, out of scope here",
                "latex_name": "chebotarev",
                "status": "user-approved",
                "approved_at": "2026-01-01T00:00:00+00:00",
                "goal_at_approval": "Prove the density theorem for cyclotomic fields",
            }
        ],
    )
    assert "Goal at approval" in page
    assert "Prove the density theorem for cyclotomic fields" in page


def test_an_approval_without_a_recorded_goal_says_the_page_cannot_tell():
    """Records written before the field lack it, and must not borrow the
    heading's goal to fill the gap."""
    page = build(
        goal="Classify the Sylow subgroups",
        assumptions=[
            {
                "formal_name": "old",
                "lean_statement": "True",
                "informal_statement": "an older approval",
                "source": "somewhere",
                "reason": "recorded before the field existed",
                "latex_name": "old",
                "status": "user-approved",
            }
        ],
    )
    assert "not recorded" in page
    assert "may not be the one it was given for" in page


def test_an_approval_made_with_no_goal_says_that_rather_than_nothing():
    """Three states, not two.

    An absent key means the record predates the field; an empty one means the
    user was asked with no goal set. Collapsing them made a genuine "no goal"
    approval read as an unrecorded one, and the page says different things
    about those for good reason.
    """
    page = build(
        goal="Classify the Sylow subgroups",
        assumptions=[
            {
                "formal_name": "early",
                "lean_statement": "True",
                "informal_statement": "approved before a goal was set",
                "source": "s",
                "reason": "r",
                "latex_name": "early",
                "status": "user-approved",
                "goal_at_approval": "",
            }
        ],
    )
    assert "no goal was set when this was approved" in page
    # Scoped to the assumptions: other sections have their own "predates the
    # field" fallbacks, and asserting over the whole page would pass or fail
    # for reasons that have nothing to do with this record.
    block = page.split("<h2>Standing assumptions</h2>", 1)[1].split("<h2>", 1)[0]
    assert "predates the field" not in block


def test_a_clipped_refusal_list_says_it_was_clipped():
    """A silent slice let an export show no evidence the model reached for the
    host: `refused_tool` is not rendered in the conversation either, so there
    was nowhere else to find it. The denials are kept whatever the count, and
    the cut is stated."""
    transcript = [{"type": "refused_tool", "name": "Bash"}] + [
        {
            "type": "tool",
            "name": f"save_lean{index}",
            "result": {"ok": False, "output": "no"},
        }
        for index in range(60)
    ]
    page = build(transcript=transcript)
    listed = page.split("<h2>Tool calls Hardy refused</h2>", 1)[1].split("<h2>", 1)[0]
    assert "Bash: not a Hardy tool" in listed
    assert "further failed tool calls were recorded" in listed
    # The newest failures survive; the oldest are what is cut.
    assert "save_lean59" in listed
    assert "save_lean0:" not in listed


def test_an_unclipped_refusal_list_claims_no_omission():
    page = build(
        transcript=[
            {"type": "tool", "name": "save_lean", "result": {"ok": False, "output": "no"}}
        ]
    )
    listed = page.split("<h2>Tool calls Hardy refused</h2>", 1)[1].split("<h2>", 1)[0]
    assert "not listed here" not in listed


def test_a_partial_report_names_which_theorems_were_still_open():
    """`partial` says some had holes; the event says which.

    The tool result below repeats the names, but it keeps only its last 4,000
    bytes -- so a run with enough unrelated obligations pushed them off the
    page, leaving a reader who can see a report was partial unable to see what
    it was partial about.
    """
    page = build(
        transcript=[
            {
                "type": "report",
                "theorems": ["sylow_one", "sylow_two"],
                "status": "partial",
                "open": ["sylow_two"],
                "statements": {"sylow_one": "theorem sylow_one : True"},
            }
        ]
    )
    assert "still open: sylow_two" in page


def test_the_page_records_what_the_session_was_allowed_to_do():
    """Model and toolchain say who ran; these say what they could reach.

    Two sessions on the same model and the same Lean are different experiments
    if one gave Lean thirty seconds and the other three minutes, or if one had
    a computer algebra kernel and the other had none.
    """
    page = build(
        settings={
            "Lean timeout": "30s per call",
            "Computer algebra": "sympy",
            "Literature search": "none: no backend was configured",
        }
    )
    identity = page.split("<h2>Identity</h2>", 1)[1].split("<h2>", 1)[0]
    assert "Lean timeout" in identity and "30s per call" in identity
    assert "sympy" in identity
    assert "no backend was configured" in identity


def test_an_export_with_no_recorded_settings_says_so():
    """Rather than showing nothing, which reads as "there was nothing to say"."""
    identity = build().split("<h2>Identity</h2>", 1)[1].split("<h2>", 1)[0]
    assert "not recorded" in identity
    assert "predates the field" in identity


def test_a_default_name_is_refused_rather_than_handed_back_unreserved(tmp_path):
    """Exhausting the range must not return a path the function never took.

    Two exporters could pick it together and `write` replaces its destination,
    which recreates exactly the loss the reservation exists to prevent.
    """
    from datetime import datetime

    when = datetime(2026, 1, 1, 12, 0, 0)
    (tmp_path / "sylow-20260101T120000.html").write_text("taken", encoding="utf-8")
    for suffix in range(1, 1000):
        (tmp_path / f"sylow-20260101T120000-{suffix}.html").write_text("taken", encoding="utf-8")

    with pytest.raises(ValueError, match="Name the file to write instead"):
        export.default_path(tmp_path, "sylow", now=when)


def test_shared_lean_a_verdict_rests_on_travels_with_the_page():
    """`.hardy/lean` modules are elaborated with the sources they support.

    A verdict on a theorem that imports one rests on that text as much as on
    its own module, and the recipient of a "standalone" page has no other copy
    of a locally authored library to compare against.
    """
    page = build(shared_sources={"Shared.Basic": "theorem helper : True := trivial"})
    assert "Shared.Basic" in page
    assert "theorem helper : True := trivial" in page


def test_a_workspace_with_no_shared_library_says_so():
    assert "imports no locally authored shared module" in build()


def test_shared_lean_is_printed_verbatim_like_the_audited_sources():
    """Its bytes are hashed into the identity that stamps every verdict, so
    rewriting them makes the page's own identity claim uncheckable."""
    page = build(shared_sources={"Shared.Keys": 'def k : String := "Bearer abc123"'})
    assert "Bearer abc123" in page


def test_a_shared_module_named_like_a_credential_key_is_not_replaced_wholesale():
    """`prepare` descends into name-keyed maps unless they are exempt.

    A module called `Password` or `Secret` had its entire body replaced with
    `[REDACTED]`, so the page omitted the exact local dependency its displayed
    audit was established against -- while rendering the section as an audited
    source.
    """
    page = build(shared_sources={"Password": "theorem helper : True := trivial"})
    assert "theorem helper : True := trivial" in page
