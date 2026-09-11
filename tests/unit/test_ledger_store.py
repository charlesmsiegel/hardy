"""Persistent project transactions preserve exact history and reject partial state."""
from __future__ import annotations

import importlib
import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from hardy.workflows.ledger.contracts import (
    DeclarationDetails,
    MathematicalContext,
    Obligation,
    ProjectItem,
    Relation,
    ResearchState,
    Scope,
    ScopedBinding,
    VersionRef,
)
from hardy.workflows.ledger.store import LedgerStore


@pytest.fixture
def store_type():
    try:
        return importlib.import_module("hardy.workflows.ledger.store").LedgerStore
    except ModuleNotFoundError:
        pytest.fail("B0 event store is not implemented")


def item(name, **kwargs):
    return ProjectItem(id=name, kind="theorem", name=name, origin="human_authored", **kwargs)


def test_restart_preserves_exact_revisions_and_rejects_stale_writer(tmp_path, store_type):
    store = store_type(tmp_path)
    first = item("T", statement="original")
    s1 = store.append((first,), expected_revision=0)
    second = item("T", statement="revised")
    s2 = store.append((second,), expected_revision=s1.revision)
    restarted = store_type(tmp_path).read()
    assert restarted == s2
    assert restarted.get(first.ref) == first
    assert restarted.head("T") == second
    assert restarted.current(ProjectItem) == (second,)
    with pytest.raises(ValueError, match="revision"):
        store.append((item("lost"),), expected_revision=s1.revision)
    assert store.read() == s2
    assert not (tmp_path / "session.json").exists()


def test_context_batch_activation_and_history(tmp_path, store_type):
    store = store_type(tmp_path)
    x = ProjectItem(id="X", kind="declaration", name="X", origin="human_authored",
                    declaration=DeclarationDetails(context_id="C0", symbol="X",
                                                   semantic_type="smooth manifold", role="arbitrary"))
    c0 = MathematicalContext(id="C0", label="setup", origin="human_authored", declarations=(x.ref,))
    s1 = store.append((c0, x), expected_revision=0, activate=c0.ref)
    c1 = MathematicalContext(id="C1", parent=c0.ref, label="case", origin="human_authored")
    s2 = store.append((c1,), expected_revision=1, activate=c1.ref)
    s3 = store.append((), expected_revision=2, activate=c0.ref)
    assert s3.active_context == c0.ref
    assert s3.get(c1.ref) == c1
    assert s1.records == (c0, x)
    assert store_type(tmp_path).read() == s3
    assert s2.active_context == c1.ref


def test_dangling_reference_and_wrong_context_owner_leave_no_event(tmp_path, store_type):
    store = store_type(tmp_path)
    bad = Relation(id="bad", kind="uses", source=VersionRef(id="T", digest="a" * 64),
                   target=VersionRef(id="D", digest="b" * 64))
    with pytest.raises(ValueError, match="reference"):
        store.append((bad,), expected_revision=0)
    theorem = item("T")
    context = MathematicalContext(id="C", label="invalid", origin="human_authored",
                                  declarations=(theorem.ref,))
    with pytest.raises(ValueError, match="declaration"):
        store.append((theorem, context), expected_revision=0)
    assert store.read().revision == 0


def test_context_and_declaration_versions_cannot_be_mutated(tmp_path, store_type):
    store = store_type(tmp_path)
    context = MathematicalContext(id="C", label="original", origin="human_authored")
    store.append((context,), expected_revision=0)
    changed = MathematicalContext(id="C", label="rewritten", origin="human_authored")
    with pytest.raises(ValueError, match="immutable"):
        store.append((changed,), expected_revision=1)
    assert store.read().head("C") == context


def test_scope_trust_requires_policy_and_model_copy_is_revalidated(tmp_path, store_type):
    store = store_type(tmp_path)
    external = item("E")
    trusted = Scope(id="scope", allowed_background=(external.ref,))
    with pytest.raises(ValueError, match="policy"):
        store.append((external, trusted), expected_revision=0)
    malformed = external.model_copy(update={"kind": "invented"})
    with pytest.raises(ValueError):
        store.append((malformed,), expected_revision=0)
    assert store.read().revision == 0


def test_failed_approach_remains_in_history(tmp_path, store_type):
    store = store_type(tmp_path)
    first = ProjectItem(id="A", kind="approach", name="degeneration", origin="generated_local",
                        research=ResearchState(status="blocked", reason="loses data"))
    later = first.model_copy(update={"research": ResearchState(status="promising", reason="new lemma")})
    store.append((first,), expected_revision=0)
    store.append((later,), expected_revision=1)
    assert store_type(tmp_path).read().get(first.ref).research.reason == "loses data"


def test_interrupted_append_keeps_previous_transaction(tmp_path, store_type, monkeypatch):
    from hardy.foundation.files import WriteGuard

    store = store_type(tmp_path)
    before = store.append((item("T"),), expected_revision=0)

    def fail(*args, **kwargs):
        raise OSError("interrupted before commit")

    monkeypatch.setattr(WriteGuard, "write_bytes", fail)
    with pytest.raises(OSError, match="interrupted"):
        store.append((item("U"),), expected_revision=1)
    assert store_type(tmp_path).read() == before


@pytest.mark.parametrize("mutation", ["schema", "content", "gap"])
def test_corrupt_or_unknown_event_refused(tmp_path, store_type, mutation):
    store = store_type(tmp_path)
    store.append((item("T"),), expected_revision=0)
    event = next((tmp_path / "ledger").glob("*.json"))
    if mutation == "gap":
        event.rename(event.with_name("00000000000000000002.json"))
    else:
        value = json.loads(event.read_text(encoding="utf-8"))
        if mutation == "schema":
            value["schema"] = "future/v99"
        else:
            value["records"][0]["value"]["name"] = "tampered"
        event.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError):
        store.read()


def test_two_writers_cannot_both_commit_same_revision(tmp_path, store_type):
    def write(name):
        try:
            return store_type(tmp_path).append((item(name),), expected_revision=0).revision
        except ValueError:
            return "stale"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(write, ["T", "U"]))
    assert sorted(results, key=str) == [1, "stale"]
    assert store_type(tmp_path).read().revision == 1


def test_validator_runs_under_revision_guard_and_sees_whole_batch(tmp_path, store_type):
    seen = []

    def validate(before, after):
        seen.append((before.revision, after.revision, tuple(r.id for r in after.records)))
        raise ValueError("policy refused")

    store = store_type(tmp_path)
    with pytest.raises(ValueError, match="policy refused"):
        store.append((item("T"), item("U")), expected_revision=0, validate=validate)
    assert seen == [(0, 1, ("T", "U"))]
    assert store.read().records == ()


def test_default_store_cannot_rewrite_a_disproved_conjecture(tmp_path, store_type):
    store = store_type(tmp_path)
    original = ProjectItem(id="C", kind="conjecture", name="C", origin="human_authored",
                           statement="All X are Y", research=ResearchState(status="disproved"))
    store.append((original,), expected_revision=0)
    corrected = original.model_copy(update={"statement": "Some X are Y"})
    with pytest.raises(ValueError, match="conjecture|supersed"):
        store.append((corrected,), expected_revision=1)
    assert store.read().head("C") == original


def test_failure_at_atomic_rename_keeps_previous_event(tmp_path, store_type, monkeypatch):
    store = store_type(tmp_path)
    first = store.append((item("T"),), expected_revision=0)

    def fail(*args):
        raise OSError("rename interrupted")

    monkeypatch.setattr("hardy.foundation.files.os.replace", fail)
    with pytest.raises(OSError, match="rename interrupted"):
        store.append((item("U"),), expected_revision=1)
    assert store_type(tmp_path).read() == first
    assert {p.name for p in (tmp_path / "ledger").iterdir()} == {
        "writer.lock", "00000000000000000001.json"}


def test_store_refuses_obligation_with_a_theorem_as_its_context(tmp_path):
    theorem, scope = item("T"), Scope(id="scope")
    malformed = Obligation(id="prove", item=theorem.ref, kind="prove", scope=scope,
                           context=theorem.ref)
    with pytest.raises(ValueError):
        LedgerStore(tmp_path).append((theorem, scope, malformed), expected_revision=0)



def test_store_refuses_alias_to_a_sibling_scoped_binding(tmp_path):
    root = MathematicalContext(id="root", label="root", origin="human_authored")
    x = ProjectItem(id="X", name="X", kind="declaration", origin="human_authored",
                    declaration=DeclarationDetails(context_id="left", symbol="X",
                                                   semantic_type="Type", role="arbitrary"))
    left_alias = ScopedBinding(id="left-alias", context_id="left", symbol="L", kind="alias",
                               meaning="X", target=x.ref)
    left = MathematicalContext(id="left", label="left", parent=root.ref,
                               origin="human_authored", declarations=(x.ref,),
                               bindings=(left_alias.ref,))
    right_alias = ScopedBinding(id="right-alias", context_id="right", symbol="R", kind="alias",
                                meaning="left-only name", target=left_alias.ref)
    right = MathematicalContext(id="right", label="right", parent=root.ref,
                                origin="human_authored", bindings=(right_alias.ref,))
    store = LedgerStore(tmp_path)
    store.append((root, x, left_alias, left), expected_revision=0)
    with pytest.raises(ValueError):
        store.append((right_alias, right), expected_revision=1)



def test_replay_refuses_case_changed_event_instead_of_treating_ledger_as_empty(tmp_path):
    store = LedgerStore(tmp_path)
    store.append((item("T"),), expected_revision=0)
    event = next((tmp_path / "ledger").glob("*.json"))
    event.rename(event.with_suffix(".JSON"))
    with pytest.raises(ValueError):
        store.read()


def test_a_base_snapshot_is_replayed_beneath_local_transactions(tmp_path, store_type):
    """An overlay store sees the base's records but writes only its own files."""
    authoritative = store_type(tmp_path / "project")
    theorem = item("T", statement="base")
    authoritative.append((theorem,), expected_revision=0)
    local = store_type(tmp_path / "local", base=lambda: authoritative.read())
    assert local.read().head("T") == theorem and local.read().revision == 0
    uses = Relation(id="uses-T", kind="uses", source=theorem.ref, target=theorem.ref)
    after = local.append((uses,), expected_revision=0)
    assert after.revision == 1 and after.head("uses-T") == uses and after.head("T") == theorem
    assert authoritative.read().revision == 1 and "uses-T" not in {r.id for r in authoritative.read().records}
    assert store_type(tmp_path / "local", base=lambda: authoritative.read()).read().head("uses-T") == uses
    with pytest.raises(ValueError, match="revision"):
        local.append((item("U"),), expected_revision=0)
