"""A theorem one tactic closes is disclosed, never refused.

Observed on a live run: refused an axiom for Sylow III, the model saved

    theorem sylow_count_congruence ... :
        ∃ (n_p : ℕ), n_p ∣ Nat.card G ∧ n_p ≡ 1 [MOD p] := by aesop

`n_p = 1` satisfies both conjuncts, so the statement is true in every finite
group and says nothing about Sylow subgroups -- and the banner then read,
truthfully and flatteringly, "1 theorem machine-checked by Lean". The signal
was already computed for axioms: `_assumption_probe` runs the `PROBES` ladder
against a proposed assumption. These tests pin the same ladder run against a
saved theorem, and what is done with the answer -- the appendix rule's
precedent, disclosure rather than prohibition, because a lemma that falls to
one tactic is still a lemma.
"""

from __future__ import annotations

from hardy.lean import LeanDiagnostic, LeanToolResult

SOURCE = "import Mathlib\n\ntheorem vacuous : True := by exact True.intro\n"


def _register(session, name="vacuous"):
    return session._dispatch(
        "record_name",
        {"formal_name": name, "latex_name": f"thm:{name}", "description": "a result"},
    )


def _save(session, source=SOURCE, path="Main.lean"):
    return session._dispatch("save_lean", {"source": source, "path": path})


# --- The probe itself -----------------------------------------------------------


def test_the_probe_file_is_examples_only_with_no_declaration(session, fake_lean) -> None:
    """The theorem under question is already declared in a workspace module,
    so unlike the assumption probe there is no safe place to put a
    declaration: `import Mathlib` alone, and nothing but `example` lines --
    the `PROBES` in order, then the `sorry` sentinel that tells a statement
    that does not elaborate apart from one nothing closes."""
    session._automation_probe({"t": "theorem t : True"})

    lines = fake_lean.last_source.splitlines()
    assert lines[0] == "import Mathlib"
    assert lines[1] == ""
    assert [line.split(" := by ")[1] for line in lines[2:]] == [*session.PROBES, "sorry"]
    assert all(line.startswith("example") for line in lines[2:])
    assert not any(line.startswith(("axiom", "theorem")) for line in lines)


def test_a_statement_one_tactic_closes_reports_that_tactic(session, fake_lean) -> None:
    fake_lean.closes_with = "aesop"

    assert session._automation_probe({"t": "theorem t : True"}) == {"t": "aesop"}


def test_a_statement_nothing_closes_reports_empty(session, fake_lean) -> None:
    assert session._automation_probe({"t": "theorem t : True"}) == {"t": ""}


def test_the_binders_ride_along_into_the_example(session, fake_lean) -> None:
    """A theorem's hypotheses are part of the question -- the live vacuity was
    closable *with* `hp : Nat.Prime p` in scope."""
    session._automation_probe(
        {"t": "theorem t (p : Nat) (hp : Nat.Prime p) : 2 ≤ p + 1"}
    )

    assert (
        "example (p : Nat) (hp : Nat.Prime p) : 2 ≤ p + 1 := by trivial"
        in fake_lean.last_source.splitlines()
    )


def test_a_guillemet_name_with_whitespace_is_parsed_off_whole(session, fake_lean) -> None:
    """`theorem «obvious result» : True` is ordinary Lean. A whitespace split
    read `«obvious` as the name and probed `result» : True` -- garbage whose
    errors were then recorded as the real statement's clean bill."""
    fake_lean.closes_with = "trivial"

    probed = session._automation_probe({"«obvious result»": "theorem «obvious result» : True"})

    assert "example : True := by trivial" in fake_lean.last_source.splitlines()
    assert probed == {"«obvious result»": "trivial"}


def test_the_earliest_closing_tactic_is_the_one_reported(session, monkeypatch) -> None:
    """The order is part of the message, exactly as it is for an axiom:
    `trivial` closing a statement is damning, `exact?` says it was in Mathlib
    all along."""

    def closes_all_but_trivial(source: str, timeout: float | None = None):
        return LeanToolResult(
            False,
            "",
            source,
            diagnostics=(
                LeanDiagnostic(severity="error", message="unsolved goals", line=3),
            ),
        )

    monkeypatch.setattr(session, "_run_lean_source", closes_all_but_trivial)

    assert session._automation_probe({"t": "theorem t : True"}) == {"t": "simp"}


def test_two_statements_are_attributed_independently(session, monkeypatch) -> None:
    """One elaboration carries every statement, so a verdict must be read off
    each statement's own lines and never its neighbour's. Statement `a`'s
    probe lines all fail while its sentinel elaborates: tried and not closed,
    which is "" -- beside `b`, which `trivial` closes."""

    def first_fails_every_tactic(source: str, timeout: float | None = None):
        return LeanToolResult(
            False,
            "",
            source,
            diagnostics=tuple(
                LeanDiagnostic(severity="error", message="unsolved goals", line=3 + index)
                for index in range(len(session.PROBES))
            ),
        )

    monkeypatch.setattr(session, "_run_lean_source", first_fails_every_tactic)

    probed = session._automation_probe(
        {"a": "theorem a : False", "b": "theorem b : True"}
    )

    assert probed == {"a": "", "b": "trivial"}


def test_a_statement_whose_sentinel_errors_is_no_verdict_for_that_statement(
    session, monkeypatch
) -> None:
    """A statement resting on section `variable`s or a workspace-local
    definition does not elaborate under `import Mathlib` alone: every probe
    line errors for that reason, not because any tactic was tried, and the
    `sorry` sentinel erroring beside them is what says so. Recording "" there
    was a clean bill of health the probe never issued -- and the statement
    beside it still gets its real verdict."""

    def first_does_not_elaborate(source: str, timeout: float | None = None):
        block = len(session.PROBES) + 1
        return LeanToolResult(
            False,
            "",
            source,
            diagnostics=tuple(
                LeanDiagnostic(severity="error", message="unknown identifier 'x'", line=3 + index)
                for index in range(block)
            ),
        )

    monkeypatch.setattr(session, "_run_lean_source", first_does_not_elaborate)

    probed = session._automation_probe(
        {"a": "theorem a : x = x", "b": "theorem b : True"}
    )

    assert probed == {"a": None, "b": "trivial"}


def test_a_probe_that_cannot_run_is_no_verdict(session, fake_lean) -> None:
    """Neither flagged nor recorded clean: nothing is stored, so the next save
    of the file asks again."""
    fake_lean.raises = TimeoutError("lean did not start")

    assert session._automation_probe({"t": "theorem t : True"}) is None


def test_an_elaboration_that_does_not_finish_is_no_verdict(session, monkeypatch) -> None:
    def unfinished(source: str, timeout: float | None = None):
        return LeanToolResult(False, "", source, diagnostics=(), timed_out=True)

    monkeypatch.setattr(session, "_run_lean_source", unfinished)

    assert session._automation_probe({"t": "theorem t : True"}) is None


def test_overflowed_output_is_no_verdict(session, monkeypatch) -> None:
    """Output cut at the process limit was cut before the later lines'
    diagnostics were written, so their silence is not a tactic succeeding --
    without this, a batch of verbose failures recorded false `trivial`s for
    every statement past the cut."""

    def overflowing(source: str, timeout: float | None = None):
        return LeanToolResult(
            False,
            "",
            source,
            diagnostics=(
                LeanDiagnostic(severity="error", message="unsolved goals", line=3),
            ),
            output_overflow=True,
        )

    monkeypatch.setattr(session, "_run_lean_source", overflowing)

    assert session._automation_probe({"t": "theorem t : True"}) is None


def test_an_unplaced_error_is_no_verdict(session, monkeypatch) -> None:
    """Every conclusion is drawn from which line an error landed on; an error
    Lean could not place must never leave a probe line looking clean."""

    def unplaced(source: str, timeout: float | None = None):
        return LeanToolResult(
            False,
            "boom",
            source,
            diagnostics=(LeanDiagnostic(severity="error", message="boom", line=None),),
        )

    monkeypatch.setattr(session, "_run_lean_source", unplaced)

    assert session._automation_probe({"t": "theorem t : True"}) is None


def test_an_error_before_the_examples_is_no_verdict(session, monkeypatch) -> None:
    """`import Mathlib` failing on line 1 leaves every example line without an
    error of its own -- Lean never reached any of them."""

    def stray(source: str, timeout: float | None = None):
        return LeanToolResult(
            False,
            "unknown module Mathlib",
            source,
            diagnostics=(
                LeanDiagnostic(severity="error", message="unknown module Mathlib", line=1),
            ),
        )

    monkeypatch.setattr(session, "_run_lean_source", stray)

    assert session._automation_probe({"t": "theorem t : True"}) is None


def test_a_failure_without_readable_diagnostics_is_no_verdict(session, monkeypatch) -> None:
    def mute(source: str, timeout: float | None = None):
        return LeanToolResult(False, "something went wrong", source, diagnostics=())

    monkeypatch.setattr(session, "_run_lean_source", mute)

    assert session._automation_probe({"t": "theorem t : True"}) is None


def test_a_statement_with_no_proposition_is_skipped_without_lean(session, fake_lean) -> None:
    """`theorem t` mid-edit has nothing to probe and could not have built
    either; asking Lean about it buys nothing, and it is left unanswered
    rather than pronounced clean."""
    assert session._automation_probe({"t": "theorem t"}) == {}
    assert fake_lean.sources == []


# --- The save that records it ---------------------------------------------------


def test_a_flagged_save_is_saved_and_says_so_in_its_own_result(session, fake_lean) -> None:
    """Disclosure, not prohibition: the save lands, and the model that made it
    -- the one that can still strengthen the statement -- is told now rather
    than a compile later."""
    fake_lean.closes_with = "aesop"
    _register(session)

    result = _save(session)

    assert result.ok
    assert "automation probe" in result.output
    assert "`vacuous` (by `aesop`)" in result.output
    assert session.state["automation"]["vacuous"] == {
        "statement": "theorem vacuous : True",
        "tactic": "aesop",
        "environment": session._toolchain,
    }


def test_a_clean_save_records_the_verdict_silently(session, fake_lean) -> None:
    _register(session)

    result = _save(session)

    assert result.ok
    assert "automation probe" not in result.output
    assert session.state["automation"]["vacuous"]["tactic"] == ""


def test_a_recorded_statement_is_not_asked_twice(session, fake_lean) -> None:
    """The answer depends on nothing but the statement text, and `import
    Mathlib` costs the same tens of seconds every time."""
    _register(session)
    _save(session)
    assert len(fake_lean.sources) == 1

    _save(session, source=SOURCE + "\nlemma extra : True := by exact True.intro\n")

    assert len(fake_lean.sources) == 1


def test_a_changed_statement_is_asked_again(session, fake_lean) -> None:
    _register(session)
    _save(session)
    fake_lean.closes_with = "simp"

    _save(
        session,
        source="import Mathlib\n\ntheorem vacuous : 1 = 1 := by exact True.intro\n",
    )

    assert len(fake_lean.sources) == 2
    assert session.state["automation"]["vacuous"] == {
        "statement": "theorem vacuous : 1 = 1",
        "tactic": "simp",
        "environment": session._toolchain,
    }


def test_a_statement_moved_on_disk_is_reprobed_by_the_next_save_of_anything(
    session, fake_lean
) -> None:
    """A record can expire without its own file being saved -- an edit on
    disk, or a shared-name collision overwriting the entry -- and the same
    single elaboration that serves the saved file re-asks it, rather than
    leaving the disclosure missing until that file happens to be saved."""
    _register(session)
    _save(session)
    assert len(fake_lean.sources) == 1
    fake_lean.closes_with = "aesop"
    (session.lean_workspace.root / "Main.lean").write_text(
        "import Mathlib\n\ntheorem vacuous : 2 = 2 := by exact True.intro\n",
        encoding="utf-8",
    )

    _save(
        session,
        path="Other.lean",
        source="import Mathlib\n\nlemma side : True := by exact True.intro\n",
    )

    assert len(fake_lean.sources) == 2
    assert session.state["automation"]["vacuous"]["statement"] == "theorem vacuous : 2 = 2"
    assert session._automation_closed() == {"vacuous": "aesop"}


def test_a_verdict_from_another_toolchain_is_reprobed(session, fake_lean) -> None:
    """What standard automation closes moves with Mathlib and the toolchain,
    so a verdict is only current under the environment it was asked in --
    the audit's own rule for its records."""
    _register(session)
    _save(session)
    assert len(fake_lean.sources) == 1
    session.state["automation"]["vacuous"]["environment"] = "another machine"
    fake_lean.closes_with = "simp"

    _save(session, source=SOURCE + "\nlemma extra : True := by exact True.intro\n")

    assert len(fake_lean.sources) == 2
    assert session.state["automation"]["vacuous"]["environment"] == session._toolchain
    assert session._automation_closed() == {"vacuous": "simp"}


def test_an_unelaboratable_statement_is_recorded_as_unanswered_and_said_once(
    session, monkeypatch
) -> None:
    """Stored as `tactic: None` -- a different fact from "": nothing closed it
    because nothing could be tried. Not flagged, not pronounced clean, not
    re-asked while the statement stands, and named in the save's own note."""

    def nothing_elaborates(source: str, timeout: float | None = None):
        return LeanToolResult(
            False,
            "",
            source,
            diagnostics=tuple(
                LeanDiagnostic(severity="error", message="unknown identifier", line=line)
                for line in range(3, 3 + len(source.splitlines()) - 2)
            ),
        )

    monkeypatch.setattr(session, "_run_lean_source", nothing_elaborates)
    _register(session)

    result = _save(session)

    assert result.ok
    assert "did not elaborate outside the workspace" in result.output
    assert session.state["automation"]["vacuous"]["tactic"] is None
    assert session._automation_closed() == {}

    second = _save(session, source=SOURCE + "\nlemma extra : True := by exact True.intro\n")

    assert "did not elaborate" not in second.output


def test_a_probe_that_cannot_run_stores_nothing_and_the_save_still_lands(
    session, fake_lean
) -> None:
    """A machine whose Lean will not start must not be one where every save is
    refused, nor one where the disclosure is silently recorded clean."""
    fake_lean.raises = TimeoutError("lean did not start")
    _register(session)

    result = _save(session)

    assert result.ok
    assert "could not be asked" in result.output
    assert session.state.get("automation") == {}


def test_a_verdict_for_a_vanished_theorem_is_pruned_at_the_next_save(
    session, fake_lean
) -> None:
    """The store must not accumulate names the tree no longer declares --
    `_automation_closed` would ignore them anyway, but a record nothing can
    ever expire is clutter in the state file forever."""
    session.state.setdefault("automation", {})["ghost"] = {
        "statement": "theorem ghost : True",
        "tactic": "simp",
    }
    _register(session)

    _save(session)

    assert "ghost" not in session.state["automation"]
    assert "vacuous" in session.state["automation"]


def test_lemmas_are_not_probed(session, fake_lean) -> None:
    """The banner counts theorems; a lemma is scaffolding and owes nothing --
    the same line `_saved_theorems` draws."""
    result = _save(session, source="import Mathlib\n\nlemma step : True := by exact True.intro\n")

    assert result.ok
    assert fake_lean.sources == []


# --- What the record means later ------------------------------------------------


def _plant(session, name="extra", statement="theorem extra : True", tactic="simp") -> None:
    """A saved theorem with a recorded verdict, written directly: these tests
    are about reading the record, not making it."""
    session.lean_workspace.root.mkdir(parents=True, exist_ok=True)
    (session.lean_workspace.root / "Extra.lean").write_text(
        f"{statement} := trivial\n", encoding="utf-8"
    )
    session.state.setdefault("automation", {})[name] = {
        "statement": statement,
        "tactic": tactic,
        "environment": session._toolchain,
    }


def test_the_verdict_expires_with_the_statement_it_was_established_against(session) -> None:
    """A record that outlives its statement is the failure `_obligations`'
    "never stored" rule exists to prevent."""
    _plant(session)
    assert session._automation_closed() == {"extra": "simp"}

    (session.lean_workspace.root / "Extra.lean").write_text(
        "theorem extra : 1 = 1 := rfl\n", encoding="utf-8"
    )

    assert session._automation_closed() == {}


def test_a_verdict_for_a_deleted_theorem_says_nothing(session) -> None:
    session.state.setdefault("automation", {})["gone"] = {
        "statement": "theorem gone : True",
        "tactic": "simp",
    }

    assert session._automation_closed() == {}


def test_a_clean_verdict_is_not_a_flag(session) -> None:
    _plant(session, tactic="")

    assert session._automation_closed() == {}


# --- The surfaces that must agree -----------------------------------------------


def test_the_stamp_names_the_flagged_theorem_and_its_tactic(session) -> None:
    _plant(session)

    stamp = session._stamp()

    assert "1 theorem statement here is closed outright by a single automation call" in stamp
    assert "extra by simp" in stamp


def test_an_unflagged_workspace_stamps_no_automation_clause(session) -> None:
    stamp = session._stamp()

    assert "automation call" not in stamp


def test_a_new_flag_stales_the_writeup(session) -> None:
    """The overstating direction: a PDF compiled before the flag goes on
    counting the theorem on the same terms as every other, with the
    disclosure missing."""
    before = session._tex_signature()
    _plant(session)

    assert session._tex_signature() != before


def test_the_steering_block_carries_the_flag(session) -> None:
    _plant(session)
    session._tally("save_lean", True)

    block = session._steering_block()

    assert "statements closed by a single automation call: extra (by simp)" in block


def test_the_workspace_listing_carries_the_flag(session) -> None:
    _plant(session)

    listing = session._workspace_listing()

    assert listing["automation"] == {"extra": "simp"}


def test_the_raw_store_reaches_neither_manifest_surface(session) -> None:
    """The model gets the checked view -- the steering block each turn, the
    listing's own key -- and never the raw records, whose statement can have
    moved on disk between sessions and contradict it."""
    _plant(session)

    assert "automation" not in session._context()
    assert "automation" not in session._workspace_listing()["manifest"]


def test_the_flag_is_a_disclosure_and_never_an_obligation(session) -> None:
    """The precedent is the appendix rule: make the gap visible, do not judge
    it. Recording the flag must not add a line to what `report_result` refuses
    over -- the theorem owes exactly what it owed before."""
    session.lean_workspace.root.mkdir(parents=True, exist_ok=True)
    (session.lean_workspace.root / "Extra.lean").write_text(
        "theorem extra : True := trivial\n", encoding="utf-8"
    )
    before = session._obligations()

    session.state.setdefault("automation", {})["extra"] = {
        "statement": "theorem extra : True",
        "tactic": "simp",
        "environment": session._toolchain,
    }

    assert session._obligations() == before
    assert session.automation_closed() == {"extra": "simp"}
