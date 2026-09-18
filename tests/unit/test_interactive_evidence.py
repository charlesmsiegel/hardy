"""The session's own verifier passes a worker's change set through no fewer gates than a save.

Each test stages a copy of the authoritative tree, edits it as a reconciled
change set would, and asks the session's owners to verify it for one
obligation. What a save refuses before Lean is asked (an unapproved axiom, a
registered name dropped), what it builds (the changed modules and everything
that imports them) and what it will not do (credit a subject with a proof of
something else) hold here too.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hardy.workflows.delegation.admission import VerificationRequest
from hardy.workflows.ledger import contracts as c

BASE = "import Mathlib\n\nlemma base_fact : True := by exact True.intro\n"
USE = "import Mathlib\nimport Main\n\nlemma use_fact : True := by exact True.intro\n"
HELPER = "import Mathlib\n\nnamespace Foo\nlemma helper_fact : True := by exact True.intro\nend Foo\n"


def _head(chat, **modules: str):
    root = chat.lean_workspace.root
    root.mkdir(parents=True, exist_ok=True)
    for module, source in modules.items():
        (root / f"{module}.lean").write_text(source, encoding="utf-8")
    assert chat.lean_workspace.build_modules(list(modules)) is None


def _staged(chat, tmp_path: Path, **changes: str | None):
    """A copy of the head with a change set applied: a `None` deletes the module."""
    staged = chat.lean_workspace.copy_to(tmp_path / "staging" / "lean", tmp_path / "staging" / "build")
    for module, source in changes.items():
        path = staged.root / f"{module}.lean"
        if source is None:
            path.unlink()
            staged.forget(module)
        else:
            path.write_text(source, encoding="utf-8")
    return staged


def _obligation(name: str, statement: str | None = None):
    item = c.ProjectItem(id="cand", kind=c.ProjectItemKind.LEMMA, name=name, statement=statement,
                         origin=c.ProjectOrigin.GENERATED_LOCAL)
    return c.Obligation(id="cand:prove", item=item.ref, kind=c.ObligationKind.PROVE, scope=c.Scope(id="scope"))


def _request(*files: str, name: str | None, statement: str | None = None) -> VerificationRequest:
    return VerificationRequest(files=files, candidate_id="cand", change_set_id="cs", subject_name=name,
                               subject_statement=statement)


def test_a_change_set_that_breaks_an_unchanged_dependent_is_refused(session_factory, tmp_path):
    """`Use` imports `Main` and is not in the change set; deleting `Main` must still be seen to break it."""
    chat = session_factory()
    _head(chat, Main=BASE, Use=USE)
    staged = _staged(chat, tmp_path, Main=None, Helper=HELPER)
    evidence, detail = chat.owners.verify(staged, _request("Main.lean", "Helper.lean", name="Foo.helper_fact"),
                                          _obligation("Foo.helper_fact"))
    assert evidence is None and "Use" in detail and "does not build" in detail


def test_a_change_set_that_drops_a_registered_name_is_refused(session_factory, tmp_path):
    chat = session_factory()
    _head(chat, Main=BASE)
    chat.state["names"].append({"formal_name": "base_fact", "latex_name": "thm:base", "description": "the base"})
    staged = _staged(chat, tmp_path, Main=BASE.replace("base_fact", "renamed_fact"))
    evidence, detail = chat.owners.verify(staged, _request("Main.lean", name="renamed_fact"),
                                          _obligation("renamed_fact"))
    assert evidence is None and "registered names" in detail and "base_fact" in detail


def test_an_unapproved_axiom_in_a_change_set_is_refused_before_any_build(session_factory, tmp_path):
    chat = session_factory()
    _head(chat, Main=BASE)
    staged = _staged(chat, tmp_path, Helper="import Mathlib\n\naxiom Bad : False\n\n" + HELPER.split("\n\n", 1)[1])
    built = []
    staged.build_modules = lambda targets: built.append(tuple(targets))                 # type: ignore[method-assign]
    evidence, detail = chat.owners.verify(staged, _request("Helper.lean", name="Foo.helper_fact"),
                                          _obligation("Foo.helper_fact"))
    assert evidence is None and "unapproved" in detail and "Bad" in detail
    assert built == []


def test_a_candidate_no_declaration_corresponds_to_is_refused_not_credited(session_factory, tmp_path):
    chat = session_factory()
    _head(chat, Main=BASE)
    staged = _staged(chat, tmp_path, Helper=HELPER)
    evidence, detail = chat.owners.verify(
        staged, _request("Helper.lean", name="a hard result", statement="Every hard thing holds"),
        _obligation("a hard result", "Every hard thing holds"))
    assert evidence is None and "no audited declaration corresponds" in detail and "Foo.helper_fact" in detail


@pytest.mark.parametrize("name, statement", [
    ("Foo.helper_fact", None),                                # the qualified name
    ("helper_fact", None),                                    # the bare name, carried by one declaration
    ("a helper", "lemma helper_fact : True"),                 # the exact statement
    ("a helper", "helper_fact :  True"),                      # the statement without its keyword, respaced
])
def test_evidence_is_minted_for_the_one_declaration_the_subject_picks_out(session_factory, tmp_path, name, statement):
    chat = session_factory()
    _head(chat, Main=BASE)
    staged = _staged(chat, tmp_path, Helper=HELPER)
    obligation = _obligation(name, statement)
    evidence, detail = chat.owners.verify(staged, _request("Helper.lean", name=name, statement=statement), obligation)
    assert evidence is not None and len(evidence) == 1, detail
    assert evidence[0].artifact.locator == "Foo.helper_fact" and evidence[0].subject == obligation.item
    authenticated = chat.owners.read_evidence(evidence[0])
    assert authenticated is not None and authenticated.outcome == "kernel_proof"


def test_a_bare_name_two_declarations_carry_corresponds_to_neither(session_factory, tmp_path):
    chat = session_factory()
    _head(chat, Main=BASE)
    two = "import Mathlib\n\nnamespace A\nlemma fact : True := by exact True.intro\nend A\n" \
          "namespace B\nlemma fact : True := by exact True.intro\nend B\n"
    staged = _staged(chat, tmp_path, Helper=two)
    evidence, detail = chat.owners.verify(staged, _request("Helper.lean", name="fact"), _obligation("fact"))
    assert evidence is None and "no audited declaration corresponds" in detail


def test_a_subject_whose_declaration_rests_on_a_hole_is_refused(session_factory, tmp_path):
    chat = session_factory()
    _head(chat, Main=BASE)
    staged = _staged(chat, tmp_path, Helper=HELPER.replace("by exact True.intro", "by exact True.intro -- axioms: sorryAx"))
    evidence, detail = chat.owners.verify(staged, _request("Helper.lean", name="Foo.helper_fact"),
                                          _obligation("Foo.helper_fact"))
    assert evidence is None and "hole" in detail


def test_admission_refreshes_shared_libraries_before_staging_the_head(session_factory, monkeypatch):
    chat = session_factory()
    refreshed = []
    monkeypatch.setattr(chat, "build_shared", lambda: refreshed.append(True))
    with pytest.raises(ValueError):
        chat.admit("no-such-delegation")
    assert refreshed == [True]


def test_a_named_declaration_that_states_something_else_is_refused(session_factory, tmp_path):
    """`HardResult : False` is not proved by a clean `theorem HardResult : True` of the same name."""
    chat = session_factory()
    _head(chat, Main=BASE)
    staged = _staged(chat, tmp_path, Helper=HELPER)
    evidence, detail = chat.owners.verify(
        staged, _request("Helper.lean", name="Foo.helper_fact", statement="Foo.helper_fact : False"),
        _obligation("Foo.helper_fact", "Foo.helper_fact : False"))
    assert evidence is None and "no audited declaration corresponds" in detail


def test_admission_reconciles_recorded_results_the_change_set_removed(session_factory, monkeypatch):
    """A worker's files land without a save; the ledger still walks the tree afterwards."""
    from hardy.workflows.interactive import session as session_module
    from hardy.workflows.ledger.store import LedgerStore

    chat = session_factory()
    _head(chat, Main=BASE)
    # Recorded as a save would record it.
    note = chat.owners.record_saved({"Main": BASE}, {"Main": {"status": "clean", "declarations": [
        {"name": "base_fact", "axioms": []}], "forbidden": [], "unapproved": [], "assumed": []}}, {"Main": "sig"})
    assert "base_fact recorded; proof obligation resolved" in note

    def committing(admission, delegation_id):
        (chat.lean_workspace.root / "Main.lean").write_text(HELPER, encoding="utf-8")
        return ()

    monkeypatch.setattr(session_module, "admit_delegation", committing)
    monkeypatch.setattr(chat, "build_shared", lambda: None)
    assert chat.admit("any") == ()
    snapshot = LedgerStore(chat.workspace).read()
    prove = next(o for o in snapshot.current(c.Obligation) if o.item == snapshot.head("lean:base_fact").ref)
    assert prove.status is c.ObligationStatus.OPEN and "no longer declares base_fact" in (prove.reason or "")
    assert any("base_fact proof obligation reopened" in notice for notice in chat.notices)
