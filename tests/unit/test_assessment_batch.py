"""Assessment batches validate exact semantics together before gaining authority."""
from dataclasses import replace

import pytest
from test_semantic_operations import fixture

from hardy.foundation.files import WriteGuard
from hardy.foundation.journal import StaleRevision
from hardy.workflows.context import DeclarationSpec
from hardy.workflows.interactive.assessments import MathematicalAssessment
from hardy.workflows.interactive.semantic_operations import SemanticOperations
from hardy.workflows.interactive.semantic_records import (
    AssessmentRequest,
    ItemRequest,
    JudgmentRequest,
)


def request(item, scope, **kwargs):
    return AssessmentRequest(subject=item.ref, scope=scope.ref,
        dimension="argument" if item.kind == "argument" else "statement", verdict="supported",
        reviewer="reader", conditions="Exact batch review", reason="Controlled assessment", **kwargs)


@pytest.mark.parametrize("invalid", ["assumption", "evidence-subject", "evidence-scope", "evidence-context", "undisclosed-assumptions", "relevant"])
def test_assessment_batch_rejects_invalid_last_request_without_recording_prefix(tmp_path, monkeypatch, invalid):
    owners, ops, _, target, scope, original = fixture(tmp_path)
    review_scope = ops.scope("other-scope", must_prove=(target.ref,)) if invalid == "evidence-scope" else scope
    review = ops.judge(JudgmentRequest(subject=original.ref, scope=review_scope.ref, purpose="informal_step",
        verdict="approved", reviewer="reader", conditions="Read exact argument", reason="Valid inference"))
    reference = ops.judgments.reference(review)
    if invalid == "assumption":
        last = request(original, scope, assumptions=(target.ref,))
    elif invalid == "evidence-subject":
        last = request(target, scope, evidence=(reference,))
    elif invalid == "evidence-scope":
        last = request(original, scope, evidence=(reference,))
    elif invalid == "evidence-context":
        other = ops.context(id="other", label="Other context")
        read = owners.read_evidence_at
        monkeypatch.setattr(owners, "read_evidence_at", lambda snapshot, ref:
            replace(read(snapshot, ref), context=other.ref) if ref == reference else read(snapshot, ref))
        last = request(original, scope, evidence=(reference,))
    elif invalid == "undisclosed-assumptions":
        read = owners.read_evidence_at
        # Controlled authenticated-reader output reports an actually used premise;
        # an otherwise exact evidence reference does not excuse its omission.
        monkeypatch.setattr(owners, "read_evidence_at", lambda snapshot, ref:
            replace(read(snapshot, ref), used_assumptions=(target.ref,)) if ref == reference else read(snapshot, ref))
        last = request(original, scope, evidence=(reference,))
    else:
        dependency = ops.item(ItemRequest(id="dependency", kind="definition", name="Dependency", statement="Original definition"))
        ops.revise(dependency.ref, statement="Changed definition")
        last = request(original, scope, relevant=(dependency.ref,))
    before = owners.journal.read()
    with pytest.raises(ValueError):
        ops.assess_many((request(target, scope), last))
    assert owners.journal.read() == before
    assert not owners.journal.read().of(MathematicalAssessment)


def test_assessment_batch_allows_local_assumptions_and_exact_evidence_in_one_transaction(tmp_path):
    owners, ops, _, _, _, _ = fixture(tmp_path)
    root = ops.context(id="root", label="Ambient context")
    context = ops.context(id="local", label="Local hypothesis", parent=root.ref,
        declarations=(DeclarationSpec(id="hypothesis", symbol="h", semantic_type="True", role="local_hypothesis"),))
    target = ops.item(ItemRequest(id="contextual", kind="claim", name="Contextual claim", statement="True", context=context.ref))
    scope = ops.scope("contextual-scope", must_prove=(target.ref,))
    review = ops.judge(JudgmentRequest(subject=target.ref, scope=scope.ref, purpose="correspondence",
        verdict="approved", reviewer="reader", conditions="Read exact context", reason="Faithful"))
    evidence = (ops.judgments.reference(review),)
    prepared = request(target, scope, assumptions=context.declarations, evidence=evidence, relevant=context.declarations)
    before = owners.journal.read()
    records = ops.assess_many((prepared, prepared.model_copy(update={"reason": "Second exact assessment"})))
    after = owners.journal.read()
    assert after.revision == before.revision + 1
    assert after.records[len(before.records):] == records
    assert len(records) == 2
    assert ops.assessments.read(records[-1], snapshot=ops.store.read()) == records[-1]
    assert records[-1].assumptions == context.declarations
    assert records[-1].evidence == evidence
    single = ops.assess(prepared)
    assert ops.assessments.read(single, snapshot=ops.store.read()) == single


def test_empty_assessment_batch_is_a_noop(tmp_path):
    owners, ops, *_ = fixture(tmp_path)
    before = owners.journal.read()
    assert ops.assess_many(()) == ()
    assert owners.journal.read() == before


def test_assessment_batch_journal_failure_cannot_publish_an_earlier_member(tmp_path, monkeypatch):
    owners, ops, _, target, scope, original = fixture(tmp_path)
    before = owners.journal.read()
    write = WriteGuard.write_bytes
    def fail_batch(guard, name, content, **kwargs):
        if guard.directory == owners.journal.directory and b"Final batch member" in content:
            raise OSError("Injected journal transaction failure")
        return write(guard, name, content, **kwargs)
    monkeypatch.setattr(WriteGuard, "write_bytes", fail_batch)
    with pytest.raises(OSError, match="journal transaction failure"):
        ops.assess_many((request(target, scope), request(original, scope).model_copy(update={"reason": "Final batch member"})))
    assert owners.journal.read() == before


@pytest.mark.parametrize("overlap", ["evidence-withdrawal", "ledger-edit"])
def test_assessment_batch_refuses_inputs_changed_during_validation(tmp_path, monkeypatch, overlap):
    owners, ops, _, target, scope, original = fixture(tmp_path)
    other = SemanticOperations(tmp_path)
    common = dict(subject=original.ref, scope=scope.ref, purpose="informal_step", reviewer="reader",
        conditions="Exact inference", reason="Independent reading")
    review = ops.judge(JudgmentRequest(verdict="approved", **common))
    reference = ops.judgments.reference(review)
    read = owners.read_evidence_at
    changed = []
    def overlapping_read(snapshot, ref):
        value = read(snapshot, ref)
        if not changed:
            changed.append(True)
            if overlap == "evidence-withdrawal":
                other.judge(JudgmentRequest(verdict="disputed", **common))
            else:
                other.item(ItemRequest(id="overlap", kind="research_note", name="Concurrent note", statement="Other work"))
        return value
    monkeypatch.setattr(owners, "read_evidence_at", overlapping_read)
    error = StaleRevision if overlap == "evidence-withdrawal" else ValueError
    with pytest.raises(error, match="stale journal revision" if overlap == "evidence-withdrawal" else "inputs changed"):
        ops.assess_many((request(target, scope), request(original, scope, evidence=(reference,))))
    assert changed
    assert owners.journal.read().of(MathematicalAssessment) == ()
