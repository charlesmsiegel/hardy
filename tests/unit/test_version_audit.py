"""Version readings schedule rechecking; no source comparison verifies mathematics."""
import hashlib
import importlib
import json
from dataclasses import replace

import pytest

from hardy.literature.manuscript import SourceSpan
from hardy.workflows.ledger.contracts import (
    ArtifactRef,
    CitationContract,
    EvidenceRef,
    Obligation,
    ProjectItem,
    Relation,
    Resolution,
    Scope,
)
from hardy.workflows.ledger.policy import (
    AcceptanceDecision,
    AuthenticatedEvidence,
    LedgerPolicy,
    ScopeChangeDecision,
)
from hardy.workflows.ledger.store import LedgerStore
from hardy.workflows.ledger.views import LedgerViews
from hardy.workflows.representation import RepresentationModel


def test_actual_referee_c2_mapping_invalidates_only_changed_application(audit, tmp_path):
    from test_referee_recursive import chain

    from hardy.workflows.critique import CritiqueOperations
    from hardy.workflows.referee import RefereeWorkflow

    store, manuscript, resolve, _, _, _ = chain(tmp_path)
    policy = LedgerPolicy(read_scope_change=lambda old, new: ScopeChangeDecision(
        old.ref if old else None, new.ref, policy.digest))
    referee = RefereeWorkflow(store, critique=CritiqueOperations(), resolve_citation=resolve, policy=policy)
    original = store.read().get(manuscript.main_results[0])
    second = original.model_copy(update={"id": "IndependentApplication"})
    scope = store.read().get(manuscript.scope).model_copy(update={"must_prove": (original.ref, second.ref)})
    store.append((second, scope, Relation(id="second-use", kind="uses", source=second.ref,
        target=manuscript.citations[0].required_claim)), expected_revision=store.read().revision, validate=policy.validate)
    manuscript = replace(manuscript, scope=scope.ref)
    first = referee.run(manuscript).citations[0]
    other = referee.run(replace(manuscript, main_results=(second.ref,),
        claims=(replace(manuscript.claims[0], item=second.ref),),
        citations=(replace(manuscript.citations[0], use_site=second.ref),))).citations[0]
    for result in (first, other):
        work = store.read().head(result.obligation.id)
        store.append((work.model_copy(update={"previous": work.ref, "status": "dismissed"}),),
                     expected_revision=store.read().revision)
    unrelated = store.read().head(other.obligation.id)
    assert first.contracts[0].use_site == first.contracts[0].required_claim
    revised = "Theorem A with a changed hypothesis."
    request = audit.VersionAuditRequest(project=store.project, id="audit-c2-use", scope=manuscript.scope,
        before=dict(manuscript.sources), after={"new.tex": revised},
        bindings=(audit.VersionBinding(item=original.ref, span=manuscript.claims[0].statement),))
    auditor = audit.VersionAuditor(store, policy=policy,
        model=RepresentationModel(provider="scripted", model="reader", configuration=()),
        read_version=lambda _: audit.VersionReading(assessments=(audit.VersionAssessment(
            item=original.ref, status="changed", reason="The application changed.",
            after=span(revised, revised, "new.tex")),)))
    result = auditor.audit(request)
    assert first.contracts[0].ref in result.citations
    assert other.contracts[0].ref not in result.citations
    assert store.read().head(first.obligation.id).status == "open"
    assert store.read().head(other.obligation.id) == unrelated


@pytest.fixture
def audit():
    return importlib.import_module("hardy.workflows.version_audit")


def item(id, statement, **kwargs):
    return ProjectItem(id=id, name=id, kind="theorem", origin="human_authored", statement=statement, **kwargs)


def span(text, fragment, path="paper.tex"):
    start = text.index(fragment)
    return SourceSpan(path, hashlib.sha256(text.encode()).hexdigest(), start, start + len(fragment))


@pytest.fixture
def project(tmp_path):
    store = LedgerStore(tmp_path)
    unchanged = item("Unchanged", "A remains true.")
    changed = item("Changed", "If x >= 0, P.")
    dependent = item("Dependent", "The dependent conclusion.")
    scope, other = Scope(id="scope"), Scope(id="other-scope")
    works = tuple(Obligation(id=id, item=subject.ref, kind="prove", scope=selected)
        for id, subject, selected in (("keep", unchanged, scope), ("change", changed, scope),
            ("change-also", changed, scope), ("dependent", dependent, scope), ("foreign", changed, other)))
    store.append((unchanged, changed, dependent, scope, other, *works,
        Relation(id="uses-changed", kind="uses", source=dependent.ref, target=changed.ref)), expected_revision=0)
    evidence_records, decisions = {}, {}
    policy = LedgerPolicy(read_evidence=evidence_records.get, read_decision=decisions.get)
    accepted = {}
    for work in works:
        evidence = EvidenceRef(kind="formal", subject=work.item, producer="scripted-kernel-owner",
            artifact=ArtifactRef(uri=f"{work.id}.proof.json", digest="a" * 64))
        evidence_records[evidence] = AuthenticatedEvidence(evidence, work.scope.ref, None, "kernel_proof")
        proposal = Resolution(id=f"resolution:{work.id}", obligation=work.ref, item=work.item, evidence=(evidence,))
        receipt = ArtifactRef(uri=f"{work.id}.decision.json", digest="b" * 64)
        decisions[receipt] = AcceptanceDecision(proposal.ref, work.ref, work.item, work.scope.ref, None, policy.digest)
        snapshot = store.read()
        resolution = policy.accept(snapshot, proposal, receipt)
        closed = Obligation.model_validate({**work.model_dump(), "previous": work.ref,
            "status": "resolved", "resolution": resolution})
        store.append((closed,), expected_revision=snapshot.revision, validate=policy.validate)
        accepted[work.id] = closed
    return store, scope, unchanged, changed, dependent, policy, accepted


def request(audit, project, *, after=None, bindings=None):
    store, scope, unchanged, changed, *_ = project
    before = f"Old preamble.\n{unchanged.statement}\n{changed.statement}\n"
    after = after if after is not None else f"New preamble.\n{unchanged.statement}\nIf x > 0, P.\nNew theorem."
    return audit.VersionAuditRequest(project=store.project, id="audit-v2", scope=scope.ref,
        before={"paper.tex": before}, after={"paper.tex": after},
        bindings=bindings if bindings is not None else tuple(audit.VersionBinding(item=i.ref, span=span(before, i.statement))
            for i in (unchanged, changed)))


def workflow(audit, project, reader):
    return audit.VersionAuditor(project[0], policy=project[5],
        model=RepresentationModel(provider="scripted", model="version-reader", configuration=(("temperature", "0"),)),
        read_version=reader)


def reading(audit, req, *, changed_status="changed"):
    after = req.after["paper.tex"]
    return audit.VersionReading(assessments=(
        audit.VersionAssessment(item=req.bindings[0].item, status="unchanged", reason="Only its position moved.",
                                after=span(after, "A remains true.")),
        audit.VersionAssessment(item=req.bindings[1].item, status=changed_status, reason="Its hypothesis changed.",
                                after=span(after, "If x > 0, P."))),
        unmapped_new=(span(after, "New theorem."),))


def test_semantic_audit_preserves_unchanged_evidence_and_reopens_every_affected_current_work(audit, project):
    store, scope, unchanged, changed, dependent, policy, accepted = project
    req = request(audit, project)
    before = store.read()
    queries = []

    def read(query):
        queries.append(query)
        assert dict(query.diff.before) == req.before and dict(query.diff.after) == req.after
        assert query.scope == scope and query.items == (unchanged, changed)
        assert query.correspondences[0].status == "exact_text"
        return reading(audit, req)

    result = workflow(audit, project, read).audit(req)
    current = LedgerStore(store.project).read()
    assert len(queries) == 1 and current.revision == before.revision + 1
    assert set(result.affected) == {changed.ref, dependent.ref}
    assert {o.id for o in result.obligations} == {"change", "change-also", "dependent"}
    for work in result.obligations:
        assert work.status == "open" and work.resolution is None
        assert work.previous == accepted[work.id].ref and work.scope == scope
    assert current.head("keep") == accepted["keep"] and current.head("foreign") == accepted["foreign"]
    assert policy.premise_allowed(current, unchanged.ref, scope=scope, context=None)
    assert not policy.premise_allowed(current, changed.ref, scope=scope, context=None)
    assert not policy.premise_allowed(current, dependent.ref, scope=scope, context=None)
    assert policy.is_accepted(current, accepted["change"].resolution)  # History remains authentic.
    assert current.records[:len(before.records)] == before.records
    assert current.head(changed.id) == changed and current.head(scope.id) == scope
    assert result.note.kind == "research_note" and result.note.research.status == "proposed"
    saved = dict(result.note.semantics)
    assert json.loads(saved["model"])["model"] == "version-reader"
    assert json.loads(saved["version-audit"])["before"] == req.before
    assert json.loads(saved["reading"])["assessments"][1]["status"] == "changed"
    assert result.unmapped_new == reading(audit, req).unmapped_new


def test_unchanged_text_with_changed_surrounding_notation_requires_semantic_reader(audit, project):
    store, scope, unchanged, changed, _, policy, _ = project
    req = request(audit, project, after=f"Now A means its complement.\n{unchanged.statement}\n{changed.statement}")

    def read(query):
        assert all(match.status == "exact_text" for match in query.correspondences)
        return audit.VersionReading(assessments=tuple(audit.VersionAssessment(item=b.item, status=status,
            reason="Ambient notation changes A.", after=match.after)
            for b, match, status in zip(query.bindings, query.correspondences, ("changed", "unchanged"), strict=True)))

    result = workflow(audit, project, read).audit(req)
    assert result.affected == (unchanged.ref,)
    assert not policy.premise_allowed(store.read(), unchanged.ref, scope=scope, context=None)
    assert policy.premise_allowed(store.read(), changed.ref, scope=scope, context=None)


@pytest.mark.parametrize("status", ["removed", "uncertain"])
def test_removed_or_uncertain_claim_creates_refresh_work_without_rewriting_math(audit, tmp_path, status):
    store = LedgerStore(tmp_path)
    theorem, scope = item("T", "Deleted claim."), Scope(id="scope")
    store.append((theorem, scope), expected_revision=0)
    req = audit.VersionAuditRequest(project=tmp_path, id="audit", scope=scope.ref,
        before={"paper.tex": theorem.statement}, after={},
        bindings=(audit.VersionBinding(item=theorem.ref, span=span(theorem.statement, theorem.statement)),))
    result = audit.VersionAuditor(store, model=RepresentationModel(provider="scripted", model="reader", configuration=()),
        read_version=lambda q: audit.VersionReading(assessments=(audit.VersionAssessment(
            item=theorem.ref, status=status, reason="The new source no longer contains it."),))).audit(req)
    assert result.affected == (theorem.ref,)
    assert len(result.obligations) == 1 and result.obligations[0].kind == "refresh_stale_artifact"
    assert store.read().head(theorem.id) == theorem


@pytest.mark.parametrize("invalid", ["missing", "duplicate", "foreign", "span", "old-text", "stale-item", "stale-scope"])
def test_invalid_or_stale_bindings_and_readings_leave_no_partial_audit(audit, project, invalid):
    store = project[0]
    req = request(audit, project)
    decision = reading(audit, req)
    if invalid == "missing":
        decision = decision.model_copy(update={"assessments": decision.assessments[:1]})
    elif invalid == "duplicate":
        decision = decision.model_copy(update={"assessments": decision.assessments * 2})
    elif invalid == "foreign":
        decision = decision.model_copy(update={"assessments": (decision.assessments[0].model_copy(
            update={"item": project[4].ref}), decision.assessments[1])})
    elif invalid == "span":
        decision = decision.model_copy(update={"assessments": (decision.assessments[0].model_copy(
            update={"after": span("foreign", "foreign")}), decision.assessments[1])})
    elif invalid == "old-text":
        req = req.model_copy(update={"bindings": (req.bindings[0].model_copy(
            update={"span": req.bindings[1].span}), req.bindings[1])})
    elif invalid == "stale-item":
        store.append((project[2].model_copy(update={"statement": "Revised independently"}),), expected_revision=store.read().revision)
    else:
        scope_policy = LedgerPolicy(read_scope_change=lambda old, new: ScopeChangeDecision(old.ref, new.ref, scope_policy.digest))
        store.append((project[1].model_copy(update={"must_prove": (project[2].ref,)}),),
                     expected_revision=store.read().revision, validate=scope_policy.validate)
    before = store.read()
    with pytest.raises(ValueError):
        workflow(audit, project, lambda q: decision).audit(req)
    assert store.read() == before


def test_concurrent_reader_edit_prevents_all_audit_writes(audit, project):
    store = project[0]
    req = request(audit, project)
    revision = store.read().revision

    def read(query):
        store.append((item("Concurrent", "Concurrent human work."),), expected_revision=revision)
        return reading(audit, req)

    with pytest.raises(ValueError, match="revision"):
        workflow(audit, project, read).audit(req)
    assert store.read().revision == revision + 1
    assert store.read().head("change") == project[-1]["change"]


def test_affected_citation_use_reopens_check_without_revising_contract(audit, project):
    store, scope, _, changed, _, _, _ = project
    external = item("External", "The source conclusion.")
    contract = CitationContract(id="citation", use_site=changed.ref, required_claim=external.ref,
        paper_id="Paper", paper_version="v1", conclusion=external.statement,
        source_statement=ArtifactRef(uri="paper-v1.txt", digest="c" * 64))
    closed = Obligation(id="citation-check", kind="check_citation", item=external.ref, scope=scope, status="dismissed")
    store.append((external, contract, closed), expected_revision=store.read().revision)
    req = request(audit, project)
    result = workflow(audit, project, lambda q: reading(audit, req)).audit(req)
    assert result.citations == (contract.ref,)
    assert store.read().head(contract.id) == contract
    reopened = store.read().head(closed.id)
    assert reopened.previous == closed.ref and reopened.status == "open"
    assert reopened.item == external.ref and reopened.kind == "check_citation"


def test_ambiguous_legacy_citation_work_is_not_reopened_by_shared_subject(audit, project):
    store, scope, unchanged, changed, *_ = project
    external = item("External", "A shared source.")
    contracts = tuple(CitationContract(id=f"citation:{owner.id}", use_site=owner.ref,
        required_claim=external.ref, paper_id="Paper", paper_version="v1", conclusion=external.statement,
        source_statement=ArtifactRef(uri="paper-v1.txt", digest="c" * 64)) for owner in (unchanged, changed))
    works = tuple(Obligation(id=f"check:{owner.id}", kind="check_citation", item=external.ref,
        scope=scope, status="dismissed") for owner in (unchanged, changed))
    store.append((external, *contracts, *works), expected_revision=store.read().revision)
    req = request(audit, project)
    before = store.read()
    with pytest.raises(ValueError, match="ambiguous citation use"):
        workflow(audit, project, lambda _: reading(audit, req)).audit(req)
    assert store.read() == before


def test_old_referee_without_use_associations_requires_reinventory(audit, tmp_path, monkeypatch):
    from test_referee_recursive import chain

    from hardy.workflows.critique import CritiqueOperations
    from hardy.workflows.referee import RefereeWorkflow

    store, manuscript, resolve, _, _, _ = chain(tmp_path)
    RefereeWorkflow(store, critique=CritiqueOperations(), resolve_citation=resolve).run(manuscript)
    current = store.read()
    legacy = replace(current, records=tuple(r for r in current.records
        if not isinstance(r, Relation) or not r.id.endswith(":use")))
    monkeypatch.setattr(store, "read", lambda: legacy)
    claim = manuscript.claims[0]
    req = audit.VersionAuditRequest(project=store.project, id="legacy-audit", scope=manuscript.scope,
        before=dict(manuscript.sources), after={}, bindings=(audit.VersionBinding(item=claim.item, span=claim.statement),))
    auditor = audit.VersionAuditor(store,
        model=RepresentationModel(provider="scripted", model="reader", configuration=()),
        read_version=lambda _: audit.VersionReading(assessments=(audit.VersionAssessment(
            item=claim.item, status="removed", reason="Retracted application."),)))
    with pytest.raises(ValueError, match="legacy citation work.*rerun Referee"):
        auditor.audit(req)
    assert LedgerStore(store.project).read() == current


def test_semantically_unchanged_relocation_preserves_all_current_acceptance(audit, project):
    store, _, unchanged, changed, *_ = project
    req = request(audit, project, after=f"Inserted introduction.\n{unchanged.statement}\n{changed.statement}")
    before = store.read()

    def read(query):
        return audit.VersionReading(assessments=tuple(audit.VersionAssessment(item=b.item, status="unchanged",
            reason="The full source reading confirms the same meaning and prerequisites.", after=m.after)
            for b, m in zip(query.bindings, query.correspondences, strict=True)))

    result = workflow(audit, project, read).audit(req)
    assert not result.affected and not result.obligations and not result.citations
    assert store.read().current(Obligation) == before.current(Obligation)
    assert store.read().records == (*before.records, result.note)


def test_affected_admitted_assumption_refuses_to_claim_revocation_without_scope_revision(audit, tmp_path):
    store = LedgerStore(tmp_path)
    theorem = item("Admitted", "An admitted claim.")
    scope = Scope(id="scope", allowed_background=(theorem.ref,))
    policy = LedgerPolicy(read_scope_change=lambda old, new: ScopeChangeDecision(
        old.ref if old else None, new.ref, policy.digest))
    before = store.append((theorem, scope), expected_revision=0, validate=policy.validate)
    req = audit.VersionAuditRequest(project=tmp_path, id="audit", scope=scope.ref,
        before={"old.tex": theorem.statement}, after={},
        bindings=(audit.VersionBinding(item=theorem.ref, span=span(theorem.statement, theorem.statement, "old.tex")),))
    auditor = audit.VersionAuditor(store, policy=policy,
        model=RepresentationModel(provider="scripted", model="reader", configuration=()),
        read_version=lambda q: audit.VersionReading(assessments=(audit.VersionAssessment(
            item=theorem.ref, status="removed", reason="The source retracted this claim."),)))
    with pytest.raises(ValueError, match="scope revision: Admitted"):
        auditor.audit(req)
    assert store.read() == before
    assert policy.premise_allowed(before, theorem.ref, scope=scope, context=None)


def test_reverse_closure_does_not_reopen_historical_dependent_work(audit, project):
    store, _, _, _, dependent, _, accepted = project
    revised = dependent.model_copy(update={"statement": "Current independent theorem."})
    store.append((revised,), expected_revision=store.read().revision)
    req = request(audit, project)
    result = workflow(audit, project, lambda q: reading(audit, req)).audit(req)
    assert dependent.ref not in result.affected and revised.ref not in result.affected
    assert store.read().head("dependent") == accepted["dependent"]


@pytest.mark.parametrize("invalid", ["project", "audit-id", "empty", "duplicate-binding", "missing-new", "removed-with-span", "duplicate-new", "mapped-new", "foreign-new"])
def test_remaining_request_and_reading_validation_is_atomic(audit, project, invalid):
    store = project[0]
    req = request(audit, project)
    decision = reading(audit, req)
    if invalid == "project":
        req = req.model_copy(update={"project": store.project / "foreign"})
    elif invalid == "audit-id":
        req = req.model_copy(update={"id": project[2].id})
    elif invalid == "empty":
        req = req.model_copy(update={"bindings": ()})
    elif invalid == "duplicate-binding":
        req = req.model_copy(update={"bindings": req.bindings * 2})
    elif invalid in {"missing-new", "removed-with-span"}:
        updates = {"after": None} if invalid == "missing-new" else {"status": "removed"}
        decision = decision.model_copy(update={"assessments": (decision.assessments[0].model_copy(update=updates),
                                                              decision.assessments[1])})
    else:
        spans = decision.unmapped_new * 2 if invalid == "duplicate-new" else (
            decision.assessments[0].after if invalid == "mapped-new" else span("foreign", "foreign"),)
        decision = decision.model_copy(update={"unmapped_new": spans})
    before = store.read()
    with pytest.raises(ValueError):
        workflow(audit, project, lambda q: decision).audit(req)
    assert store.read() == before


def test_source_limit_and_failed_reader_produce_no_audit_or_reopened_work(audit, project):
    store = project[0]
    req = request(audit, project)
    before = store.read()

    def fail(query):
        raise RuntimeError("scripted reader unavailable")

    auditor = workflow(audit, project, fail)
    auditor.max_bytes = 1
    with pytest.raises(ValueError, match="limit"):
        auditor.audit(req)
    auditor.max_bytes = 2 * 1024 * 1024
    with pytest.raises(RuntimeError, match="unavailable"):
        auditor.audit(req)
    assert store.read() == before


def test_reader_cannot_label_changed_hypothesis_unchanged_to_preserve_verification(audit, project):
    req = request(audit, project)
    decision = reading(audit, req)
    decision = decision.model_copy(update={"assessments": (decision.assessments[0],
        decision.assessments[1].model_copy(update={"status": "unchanged"}))})
    before = project[0].read()
    with pytest.raises(ValueError, match="unchanged.*statement"):
        workflow(audit, project, lambda q: decision).audit(req)
    assert project[0].read() == before


def test_changed_citation_use_revokes_current_coverage_but_preserves_independent_source_proof(audit, tmp_path):
    store = LedgerStore(tmp_path)
    use, external = item("Use", "The original application."), item("External", "The independent source result.")
    scope = Scope(id="scope")
    source = ArtifactRef(uri="source-v1.txt", digest="a" * 64)
    read = EvidenceRef(kind="literature", subject=external.ref, producer="source-owner", artifact=source)
    faithful = EvidenceRef(kind="faithfulness", subject=external.ref, producer="reader",
        artifact=ArtifactRef(uri="faithful.json", digest="b" * 64))
    formal = EvidenceRef(kind="formal", subject=external.ref, producer="kernel-owner",
        artifact=ArtifactRef(uri="proof.json", digest="c" * 64))
    contract = CitationContract(id="contract", use_site=use.ref, required_claim=external.ref,
        paper_id="Paper", paper_version="v1", source_statement=source, conclusion=external.statement,
        evidence=(read, faithful))
    proof = Obligation(id="proof", item=external.ref, kind="prove", scope=scope)
    check = Obligation(id="check", item=external.ref, kind="check_citation", scope=scope)
    store.append((use, external, scope, contract, proof, check), expected_revision=0)
    evidence = {
        read: AuthenticatedEvidence(read, scope.ref, None, "source_read", citation=contract.ref),
        faithful: AuthenticatedEvidence(faithful, scope.ref, None, "faithful", citation=contract.ref),
        formal: AuthenticatedEvidence(formal, scope.ref, None, "kernel_proof"),
    }
    decisions = {}
    policy = LedgerPolicy(read_evidence=evidence.get, read_decision=decisions.get)
    for work, refs in ((proof, (formal,)), (check, (read, faithful))):
        proposal = Resolution(id=f"resolution:{work.id}", item=external.ref, obligation=work.ref, evidence=refs)
        receipt = ArtifactRef(uri=f"{work.id}.decision", digest="d" * 64)
        decisions[receipt] = AcceptanceDecision(proposal.ref, work.ref, external.ref, scope.ref, None, policy.digest)
        snapshot = store.read()
        accepted = policy.accept(snapshot, proposal, receipt)
        store.append((Obligation.model_validate({**work.model_dump(), "previous": work.ref,
            "status": "resolved", "resolution": accepted}),), expected_revision=snapshot.revision, validate=policy.validate)
    before = store.read()
    assert LedgerViews(before, policy).coverage().citations_checked == (contract.ref,)
    req = audit.VersionAuditRequest(project=tmp_path, id="audit", scope=scope.ref,
        before={"paper.tex": use.statement}, after={"paper.tex": "The changed application."},
        bindings=(audit.VersionBinding(item=use.ref, span=span(use.statement, use.statement)),))
    auditor = audit.VersionAuditor(store, policy=policy,
        model=RepresentationModel(provider="scripted", model="reader", configuration=()),
        read_version=lambda q: audit.VersionReading(assessments=(audit.VersionAssessment(item=use.ref,
            status="changed", reason="The source result is now applied differently.",
            after=span(req.after["paper.tex"], req.after["paper.tex"])),)))
    result = auditor.audit(req)
    current = store.read()
    assert result.citations == (contract.ref,) and result.affected == (use.ref,)
    assert LedgerViews(current, policy).coverage().citations_open == (contract.ref,)
    assert current.head(check.id).status == "open" and current.head(check.id).resolution is None
    assert current.head(proof.id) == before.head(proof.id)
    assert policy.premise_allowed(current, external.ref, scope=scope, context=None)
    assert current.head(contract.id) == contract


def test_new_citation_checks_keep_distinct_exact_historical_subject_versions(audit, project):
    store, scope, _, changed, *_ = project
    old = item("External", "Old source result.")
    current = old.model_copy(update={"statement": "New source result."})
    contracts = tuple(CitationContract(id=f"citation-{n}", use_site=changed.ref, required_claim=subject.ref,
        paper_id="Paper", paper_version=f"v{n}", conclusion=subject.statement,
        source_statement=ArtifactRef(uri=f"source-v{n}.txt", digest="c" * 64))
        for n, subject in enumerate((old, current), 1))
    store.append((old,), expected_revision=store.read().revision)
    store.append((current, *contracts), expected_revision=store.read().revision)
    req = request(audit, project)
    result = workflow(audit, project, lambda q: reading(audit, req)).audit(req)
    checks = tuple(o for o in result.obligations if o.kind == "check_citation")
    assert len(checks) == 2 and len({o.id for o in checks}) == 2
    assert {o.item for o in checks} == {old.ref, current.ref}
    assert all(o.scope == scope for o in checks)
    assert set(result.citations) == {c.ref for c in contracts}
