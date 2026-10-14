"""Application target proposals preserve exact history without borrowing acceptance."""
import pytest
from test_chat import FakeChatRuntime, session

from hardy.workflows.ledger.contracts import Obligation, Relation, Scope
from hardy.workflows.ledger.store import LedgerStore


def test_target_and_superseding_translation_survive_restart(tmp_path):
    chat = session(tmp_path, FakeChatRuntime([]))
    original = chat.project_target("T", "Every natural is positive", formal="theorem T (n : Nat) : n > 0")
    store = LedgerStore(tmp_path)
    before = store.read()
    assert before.head("T:formal").statement == "theorem T (n : Nat) : n > 0"
    assert before.head("T:scope").must_prove == (original.ref, before.head("T:formal").ref)
    assert len(before.current(Obligation)) == 2
    assert all(o.status == "open" and o.resolution is None for o in before.current(Obligation))
    reopened = session(tmp_path, FakeChatRuntime([]))
    replacement = reopened.project_target("T2", "Every nonzero natural is positive",
                                          formal="theorem T2 (n : Nat) (h : n ≠ 0) : n > 0",
                                          supersedes=f"T@{original.digest}")
    after = LedgerStore(tmp_path).read()
    assert after.get(original.ref) == original
    assert after.get(replacement.ref) == replacement
    assert after.head("T2:supersedes").target == original.ref
    assert len(after.current(Scope)) == 2
    assert len(after.current(Obligation)) == 4
    assert len(after.current(Relation)) == 3
    assert not any(scope.allowed_background or scope.allowed_interfaces for scope in after.current(Scope))


@pytest.mark.parametrize("options", [
    {"identity": "T"}, {"identity": "bad/name"}, {"statement": " "}, {"formal": ""},
    {"context": "missing"}, {"supersedes": "T"}, {"supersedes": "T@" + "0" * 64},
    {"identity": "T:formal"},
])
def test_invalid_target_is_atomic(tmp_path, options):
    chat = session(tmp_path, FakeChatRuntime([]))
    chat.project_target("T", "True", formal="theorem T : True")
    store = LedgerStore(tmp_path)
    before = store.read()
    request = {"identity": "Next", "statement": "True", "formal": "theorem Next : True", **options}
    with pytest.raises(ValueError):
        chat.project_target(**request)
    assert store.read() == before


def test_reviews_authenticate_exact_proposals_and_latest_judgment(tmp_path):
    from hardy.workflows.interactive.evidence import ProjectOwners, TranslationReview

    chat = session(tmp_path, FakeChatRuntime([]))
    target = chat.project_target("T", "True", formal="theorem T : True")
    formal = LedgerStore(tmp_path).read().head("T:formal")
    def review(verdict):
        return chat.project_review(f"T@{target.digest}", f"T:formal@{formal.digest}",
                                   verdict=verdict, reviewer="human:tester", conditions="side by side, no model",
                                   reason="Controlled correspondence review")
    approved = review("approved")
    reference = chat.owners.review_reference(approved)
    reopened = ProjectOwners.reading(tmp_path)
    evidence = reopened.read_evidence(reference)
    assert evidence is not None and evidence.outcome == "faithful"
    assert evidence.scope == LedgerStore(tmp_path).read().head("T:scope").ref
    assert not any(o.resolution for o in LedgerStore(tmp_path).read().current(Obligation))
    disputed = review("disputed")
    assert reopened.read_evidence(reference) is None
    assert reopened.read_evidence(chat.owners.review_reference(disputed)) is None
    assert len(reopened.journal.read().of(TranslationReview)) == 2
    approved_again = review("approved")
    reference = chat.owners.review_reference(approved_again)
    assert reopened.read_evidence(reference) is not None
    store = LedgerStore(tmp_path)
    snapshot = store.read()
    changed = formal.model_copy(update={"statement": "theorem T : False"})
    store.append((changed,), expected_revision=snapshot.revision)
    assert reopened.read_evidence(reference) is None
    with pytest.raises(ValueError, match="stale"):
        review("approved")


def test_reviews_refuse_unrelated_translations_and_incomplete_attribution(tmp_path):
    chat = session(tmp_path, FakeChatRuntime([]))
    target = chat.project_target("T", "True", formal="theorem T : True")
    chat.project_target("Other", "False", formal="theorem Other : False")
    store = LedgerStore(tmp_path)
    formal = store.read().head("T:formal")
    other = store.read().head("Other:formal")
    with pytest.raises(ValueError, match="belong"):
        chat.owners.review_translation(target.ref, other.ref, verdict="approved", reviewer="tester",
                                       conditions="read", reason="yes")
    for field in ("reviewer", "conditions", "reason", "verdict"):
        options = {"verdict": "approved", "reviewer": "tester", "conditions": "read", "reason": "yes", field: " "}
        with pytest.raises(ValueError):
            chat.owners.review_translation(target.ref, formal.ref, **options)
    assert not chat.owners.journal.read().records


def test_caller_cannot_turn_an_unresolved_review_into_evidence(tmp_path):
    chat = session(tmp_path, FakeChatRuntime([]))
    target = chat.project_target("T", "True", formal="theorem T : True")
    formal = LedgerStore(tmp_path).read().head("T:formal")
    record = chat.owners.review_translation(target.ref, formal.ref, verdict="unresolved", reviewer="tester",
                                           conditions="read", reason="not yet established")
    forged = record.model_copy(update={"verdict": "approved"})
    assert chat.owners.read_evidence(chat.owners.review_reference(forged)) is None
    assert chat.owners.read_evidence(chat.owners.review_reference(record)) is None


def test_changed_correspondence_link_withdraws_review_evidence(tmp_path):
    chat = session(tmp_path, FakeChatRuntime([]))
    target = chat.project_target("T", "True", formal="theorem T : True")
    other = chat.project_target("Other", "False", formal="theorem Other : False")
    store = LedgerStore(tmp_path)
    snapshot = store.read()
    formal = snapshot.head("T:formal")
    review = chat.owners.review_translation(target.ref, formal.ref, verdict="approved", reviewer="tester",
                                           conditions="read", reason="same statement")
    reference = chat.owners.review_reference(review)
    assert chat.owners.read_evidence(reference) is not None
    edge = snapshot.head("T:formalizes")
    store.append((edge.model_copy(update={"target": other.ref}),), expected_revision=snapshot.revision)
    assert chat.owners.read_evidence(reference) is None


def test_translation_approval_cannot_establish_a_critique(tmp_path):
    chat = session(tmp_path, FakeChatRuntime([]))
    target = chat.project_target("T", "True", formal="theorem T : True")
    snapshot = LedgerStore(tmp_path).read()
    formal = snapshot.head("T:formal")
    record = chat.owners.review_translation(target.ref, formal.ref, verdict="approved", reviewer="tester",
                                           conditions="read translation", reason="same statement")
    reference = chat.owners.review_reference(record)
    formalization = snapshot.head("T:translation-review")
    assert chat.owners.policy._evidence(reference, formalization).outcome == "faithful"
    critique = Obligation(id="critique", item=target.ref, kind="critique", scope=formalization.scope)
    with pytest.raises(ValueError, match="category"):
        chat.owners.policy._evidence(reference, critique)


def test_review_policy_uses_transaction_snapshot_without_relocking(tmp_path):
    from hardy.workflows.ledger.contracts import ProjectItem

    chat = session(tmp_path, FakeChatRuntime([]))
    target = chat.project_target("T", "True", formal="theorem T : True")
    store = LedgerStore(tmp_path)
    snapshot = store.read()
    formal = snapshot.head("T:formal")
    review = chat.owners.review_translation(target.ref, formal.ref, verdict="approved", reviewer="tester",
                                           conditions="read", reason="same statement")
    reference = chat.owners.review_reference(review)
    obligation = snapshot.head("T:translation-review")
    note = ProjectItem(id="note", name="note", kind="research_note", origin="human_authored")
    def validate(before, after):
        chat.owners.policy.validate(before, after)
        assert chat.owners.policy._evidence(reference, obligation, snapshot=after).outcome == "faithful"
    store.append((note,), expected_revision=snapshot.revision, validate=validate)
    assert store.read().head("note") == note


@pytest.mark.parametrize('identity', ['project', 'lean:future_result'])
def test_semantic_targets_cannot_take_formal_save_owner_identities(tmp_path, identity):
    chat = session(tmp_path, FakeChatRuntime([]))
    with pytest.raises(ValueError, match='reserved'):
        chat.project_target(identity, 'True', formal='theorem future_result : True')
    assert not LedgerStore(tmp_path).read().records
