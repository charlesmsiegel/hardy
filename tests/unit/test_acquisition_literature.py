"""Literature acquisition reads real source bytes; model/Lean operations are scripted."""
import tarfile
from dataclasses import replace
from datetime import UTC, datetime
from hashlib import sha256
from io import BytesIO
from types import SimpleNamespace
from uuid import UUID

import pytest

from hardy.formal.contracts import EnvironmentIdentity, FormalizationProposal
from hardy.literature.client import ArxivClient
from hardy.literature.library import PaperLibrary
from hardy.literature.metadata import parse_id
from hardy.workflows.acquisition.contracts import ClassifiedGap, GapKind
from hardy.workflows.admission import CheckDecision
from hardy.workflows.contracts import FaithfulnessReview, RunPhase
from hardy.workflows.faithfulness import review_translation
from hardy.workflows.formalization import prepare_candidate
from hardy.workflows.ledger.contracts import (
    ArtifactRef,
    CitationContract,
    EvidenceRef,
    MathematicalContext,
    Obligation,
    ProjectItem,
    Scope,
)
from hardy.workflows.ledger.policy import AuthenticatedEvidence
from hardy.workflows.ledger.state import LedgerSnapshot
from hardy.workflows.ledger.validation import validate_structure
from hardy.workflows.representation import RepresentationModel
from hardy.workflows.storage import RunStore

NOW = datetime(2026, 9, 10, tzinfo=UTC)
GOAL = "Every closed subset of a compact space is compact."
WRONG = "Every subset of a finite space is compact."
ENV = EnvironmentIdentity(lean_version="4", lean_commit="lean", mathlib_revision="mathlib",
                          lake_manifest_sha256="a" * 64)


def fixture(tmp_path):
    library = PaperLibrary(tmp_path / "papers")
    for identifier, statement in (("2401.00001v1", WRONG), ("2401.00002v2", GOAL)):
        feed = f'''<feed xmlns="http://www.w3.org/2005/Atom"
          xmlns:arxiv="http://arxiv.org/schemas/atom"><entry>
          <id>http://arxiv.org/abs/{identifier}</id><title>Synthetic fixture</title>
          <summary>Only source statements support citations.</summary>
          <author><name>Fixture Author</name></author>
          <published>2024-01-01T00:00:00Z</published><updated>2024-01-01T00:00:00Z</updated>
          <arxiv:primary_category term="math.GN"/><category term="math.GN"/>
          </entry></feed>'''.encode()
        ArxivClient(library, transport=lambda *_args, body=feed: body,
                    clock=lambda: 1000000.0, sleep=lambda _: None).fetch(identifier)
        body = (r"\documentclass{article}\begin{document}\begin{theorem}\label{main}" +
                statement + r"\end{theorem}\end{document}").encode()
        buffer = BytesIO()
        with tarfile.open(fileobj=buffer, mode="w") as archive:
            info = tarfile.TarInfo("main.tex")
            info.size = len(body)
            archive.addfile(info, BytesIO(body))
        library.admit_source(parse_id(identifier), buffer.getvalue(),
                             source_url=f"https://arxiv.org/src/{identifier}", fetched_at=NOW.isoformat())
    context = MathematicalContext(id="context", label="Compact ambient space", origin="human_authored")
    subject = ProjectItem(id="needed", kind="external_result", name="Compact closed subset",
                          statement=GOAL, origin="background_paper", context=context.ref)
    scope = Scope(id="scope")
    work = Obligation(id="acquire", kind="acquire_prerequisite", item=subject.ref,
                      context=context.ref, scope=scope)
    snapshot = LedgerSnapshot(records=(context, subject, scope, work), revision=1,
                              active_context=context.ref)
    gap = ClassifiedGap(obligation=work, kind="literature", reason="Established external result",
        searches=(), model=RepresentationModel(provider="fixture", model="classifier", configuration=()))
    return library, snapshot, work, gap


class Operations:
    """Only the external model/Lean boundary is scripted; source and run IO are real."""
    backend = "fixture"
    isolation_guarantee = "fixture-no-tools"

    def __init__(self, tmp_path):
        self.path = tmp_path / "review"
        self.prepared = []
        self.admissions = []
        self.agrees = True

    def prepare(self, request):
        result = prepare_candidate(request, FormalizationProposal(
            restatement=request.text, domains=(), quantifiers=(), assumptions=(),
            interpretation_choices=(), theorem_name="closed_compact", binders="",
            proposition="True"), ENV, NOW, lean=self)
        self.prepared.append(result)
        return result

    def check_proof(self, claim, body, assumptions):
        return SimpleNamespace(success=True)

    def start(self, **kwargs):
        return object()

    def run_structured(self, thread, name, prompt, schema):
        return FaithfulnessReview(formalization_entails_claim=self.agrees,
                                 claim_entails_formalization=self.agrees)

    def review(self, claim):
        from hardy.workflows.acquisition.literature import LiteratureReading
        store = RunStore(self.path, UUID(int=1))
        verdict = review_translation(claim, runtime=self, model="fixture-reader", store=store,
                                     phase=RunPhase.FORMALIZING)
        digest = sha256((self.path / "faithfulness.json").read_bytes()).hexdigest()
        return LiteratureReading(verdict, ArtifactRef(uri=str(self.path / "faithfulness.json"), digest=digest))

    def admit(self, request, source, prepared):
        self.admissions.append((request, source, prepared))
        return CheckDecision(checked="Explicit approval is pending")


def resolver(tmp_path, library, **changes):
    from hardy.workflows.acquisition.literature import (
        HypothesisComparison,
        LiteratureComparison,
        LiteratureResolver,
        LiteratureSelection,
    )
    operations = Operations(tmp_path)

    def compare(query, candidate):
        wrong = candidate.statement.text == WRONG
        hypotheses = (HypothesisComparison(hypothesis="ambient space is finite", availability="unavailable",
                          reason="Compactness does not imply finiteness"),) if wrong else tuple(
            HypothesisComparison(hypothesis=h, availability="unresolved", reason="Needs contextual discharge")
            for h in ("ambient space is compact", "subset is closed"))
        return LiteratureComparison(required_statement=query.subject.statement,
            required_hypotheses=("ambient space is compact", "subset is closed"),
            hypotheses=hypotheses, conclusion="subset is compact", conclusion_matches=True,
            conclusion_reason="This is the required conclusion")

    values = dict(library=library, model=RepresentationModel(provider="fixture", model="matcher", configuration=()),
        search=lambda query: (LiteratureSelection(paper_id="2401.00001v1", statement_ref="main"),
                              LiteratureSelection(paper_id="2401.00002v2", statement_ref="main")),
        compare=compare, prepare=operations.prepare, review=operations.review, request_admission=operations.admit)
    values.update(changes)
    return LiteratureResolver(**values), operations


def test_unusable_hypotheses_skip_superficial_hit_and_keep_correct_source_exact(tmp_path):
    library, snapshot, work, gap = fixture(tmp_path)
    service, operations = resolver(tmp_path, library)
    result = service.resolve(snapshot, work, gap)
    citation, = (record for record in result.records if isinstance(record, CitationContract))
    assert citation.paper_id == "2401.00002"
    assert citation.paper_version == "v2"
    assert citation.source_statement.digest == sha256(GOAL.encode()).hexdigest()
    assert citation.source_statement.locator == "main.tex#main"
    assert citation.required_claim == work.item
    assert citation.status.value == "open"
    assert len(operations.prepared) == 1
    assert operations.prepared[0].claim.original_text == GOAL
    assert len(operations.admissions) == 1
    assert operations.admissions[0][0].scope == work.scope
    assert {child.kind.value for child in result.children} == {
        "discharge_citation_hypotheses", "resolve_ambiguity"}
    assert len([child for child in result.children if child.kind.value == "discharge_citation_hypotheses"]) == 2
    assert all(child.context == work.context and child.scope == work.scope for child in result.children)
    assert result.evidence == ()  # No capability reader was installed.
    assert all(child.status.value == "open" for child in result.children)
    assert any(WRONG in value for record in result.records if isinstance(record, ProjectItem)
               for _key, value in record.semantics)
    validate_structure(snapshot, LedgerSnapshot(records=(*snapshot.records, *result.records, *result.children)))


def test_disputed_translation_does_not_reach_admission(tmp_path):
    library, snapshot, work, gap = fixture(tmp_path)
    service, operations = resolver(tmp_path, library)
    operations.agrees = False
    result = service.resolve(snapshot, work, gap)
    assert not operations.admissions
    assert not result.evidence
    assert "quarantine" in result.detail.lower()
    assert any(child.kind.value == "check_citation" for child in result.children)


def test_modified_downloaded_source_is_refused_before_model_translation(tmp_path):
    library, snapshot, work, gap = fixture(tmp_path)
    path = library.path_for(parse_id("2401.00002v2")) / "source" / "main.tex"
    path.write_text("Theorem invented after download.", encoding="utf-8")
    service, operations = resolver(tmp_path, library)
    with pytest.raises(Exception, match="digest"):
        service.resolve(snapshot, work, gap)
    assert not operations.prepared


def test_frozen_source_review_and_admission_are_retained_in_candidate_records(tmp_path):
    library, snapshot, work, gap = fixture(tmp_path)
    service, operations = resolver(tmp_path, library)
    result = service.resolve(snapshot, work, gap)
    semantics = [(key, value) for record in result.records if isinstance(record, ProjectItem)
                 for key, value in record.semantics]
    assert ("frozen-claim", operations.prepared[0].claim.model_dump_json()) in semantics
    assert any(key == "admission-request" for key, value in semantics)
    assert any(artifact.uri.endswith("faithfulness.json") for record in result.records
               if isinstance(record, ProjectItem) for artifact in record.artifacts)
    source_item, = (record for record in result.records if isinstance(record, ProjectItem)
                   and record.kind.value == "external_result")
    assert operations.admissions[0][0].subject == source_item.ref
    assert operations.admissions[0][1].subject == source_item.ref


@pytest.mark.parametrize("change", ["subject", "scope", "work", "gap", "context"])
def test_stale_or_mismatched_request_cannot_start_source_formalization(tmp_path, change):
    library, snapshot, work, gap = fixture(tmp_path)
    service, operations = resolver(tmp_path, library)
    if change == "subject":
        subject = snapshot.get(work.item)
        snapshot = replace(snapshot, records=(*snapshot.records, subject.model_copy(update={"name": "Revised"})))
    elif change == "scope":
        snapshot = replace(snapshot, records=(*snapshot.records, Scope(id="scope", must_prove=(work.item,))))
    elif change == "work":
        snapshot = replace(snapshot, records=(*snapshot.records, work.model_copy(update={"reason": "Revised"})))
    elif change == "gap":
        gap = gap.model_copy(update={"obligation": work.model_copy(update={"reason": "Unrelated"})})
    else:
        work = work.model_copy(update={"context": None})
        snapshot = replace(snapshot, records=(*snapshot.records, work))
        gap = gap.model_copy(update={"obligation": work})
    with pytest.raises(ValueError, match="stale|context|classified"):
        service.resolve(snapshot, work, gap)
    assert not operations.prepared


def test_scope_must_prove_subject_cannot_be_assumed_from_literature(tmp_path):
    library, snapshot, work, gap = fixture(tmp_path)
    scope = Scope(id="scope", must_prove=(work.item,))
    work = work.model_copy(update={"scope": scope})
    snapshot = replace(snapshot, records=(*snapshot.records, scope, work))
    gap = gap.model_copy(update={"obligation": work})
    service, operations = resolver(tmp_path, library)
    result = service.resolve(snapshot, work, gap)
    assert "must prove" in result.detail
    assert not operations.prepared
    assert not result.evidence


def test_wrong_source_translation_never_reaches_review_or_admission(tmp_path):
    library, snapshot, work, gap = fixture(tmp_path)
    service, operations = resolver(tmp_path, library)
    prepare = service.prepare
    service.prepare = lambda request: prepare(request.model_copy(update={"text": WRONG}))
    with pytest.raises(ValueError, match="exact source statement"):
        service.resolve(snapshot, work, gap)
    assert not operations.path.exists()
    assert not operations.admissions


def test_review_for_another_frozen_claim_is_refused(tmp_path):
    library, snapshot, work, gap = fixture(tmp_path)
    service, operations = resolver(tmp_path, library)
    review = service.review

    def wrong_review(claim):
        reading = review(claim)
        return replace(reading, verdict=reading.verdict.model_copy(update={"claim_sha256": "f" * 64}))

    service.review = wrong_review
    with pytest.raises(ValueError, match="different frozen source"):
        service.resolve(snapshot, work, gap)
    assert not operations.admissions


@pytest.mark.parametrize("mismatch", [None, "scope", "context", "subject", "citation", "outcome", "hypothesis", "assumptions"])
def test_capability_evidence_must_authenticate_the_exact_current_use(tmp_path, mismatch):
    library, snapshot, work, gap = fixture(tmp_path)
    service, operations = resolver(tmp_path, library)
    first = service.resolve(snapshot, work, gap)
    citation, = (record for record in first.records if isinstance(record, CitationContract))

    def reader(reference):
        value = AuthenticatedEvidence(reference, work.scope.ref, work.context,
            "source_read" if reference.kind.value == "literature" else "faithful", citation=citation.ref)
        if mismatch == "subject":
            return replace(value, reference=reference.model_copy(update={"subject": work.scope.ref}))
        if mismatch == "assumptions":
            return replace(value, used_assumptions=(work.item,))
        changes = {"scope": work.item, "context": None, "citation": work.ref,
                   "outcome": "kernel_proof", "hypothesis": "a different premise"}
        return replace(value, **{mismatch: changes[mismatch]}) if mismatch else value

    service.read_evidence = reader
    if mismatch:
        with pytest.raises(ValueError, match="authenticate exact"):
            service.resolve(snapshot, work, gap)
    else:
        result = service.resolve(snapshot, work, gap)
        assert len(result.evidence) == 2
        assert result.children
        assert work.status.value == "open"


def test_missing_comparison_evidence_cannot_discharge_claimed_established_hypotheses(tmp_path):
    library, snapshot, work, gap = fixture(tmp_path)
    service, operations = resolver(tmp_path, library)
    compare = service.compare

    def claimed(query, source):
        value = compare(query, source)
        if source.statement.text == GOAL:
            value = value.model_copy(update={"hypotheses": tuple(
                h.model_copy(update={"availability": "established"}) for h in value.hypotheses)})
        return value

    service.compare = claimed
    result = service.resolve(snapshot, work, gap)
    assert len([child for child in result.children if child.kind.value == "discharge_citation_hypotheses"]) == 2


def test_wrong_hypothesis_evidence_cannot_reach_admission(tmp_path):
    library, snapshot, work, gap = fixture(tmp_path)
    service, operations = resolver(tmp_path, library)
    compare = service.compare
    reference = EvidenceRef(kind="formal", subject=work.item, producer="fixture-lean",
                            artifact=ArtifactRef(uri="fixture:proof", digest="d" * 64))

    def claimed(query, source):
        value = compare(query, source)
        if source.statement.text == GOAL:
            hypotheses = (value.hypotheses[0].model_copy(update={"evidence": (reference,)}), *value.hypotheses[1:])
            value = value.model_copy(update={"hypotheses": hypotheses})
        return value

    service.compare = claimed
    service.read_evidence = lambda ref: AuthenticatedEvidence(ref, work.scope.ref, work.context,
        "kernel_proof", hypothesis="ambient space is finite")
    with pytest.raises(ValueError, match="authenticate exact"):
        service.resolve(snapshot, work, gap)
    assert not operations.admissions


def test_another_gap_category_cannot_request_literature_admission(tmp_path):
    library, snapshot, work, gap = fixture(tmp_path)
    service, operations = resolver(tmp_path, library)
    gap = gap.model_copy(update={"kind": GapKind.TARGET_PAPER})
    with pytest.raises(ValueError, match="literature classification"):
        service.resolve(snapshot, work, gap)
    assert not operations.prepared


def test_foreign_semantic_blocker_is_not_attached_to_this_context(tmp_path):
    from hardy.workflows.formalization import SemanticBlockers
    library, snapshot, work, gap = fixture(tmp_path)
    blocker = Obligation(id="foreign", item=work.item, context=None, scope=work.scope,
                         kind="resolve_declaration", reason="Unknown carrier")
    service, operations = resolver(tmp_path, library,
        prepare=lambda request: SemanticBlockers(obligations=(blocker,)))
    with pytest.raises(ValueError, match="semantic prerequisite.*context"):
        service.resolve(snapshot, work, gap)
    assert not operations.admissions


def test_admitted_exact_source_is_reused_without_repeating_admission(tmp_path):
    library, snapshot, work, gap = fixture(tmp_path)
    service, operations = resolver(tmp_path, library)
    initial = service.resolve(snapshot, work, gap)
    source_item, = (record for record in initial.records if isinstance(record, ProjectItem)
                   and record.kind.value == "external_result")
    scope = Scope(id="scope", allowed_background=(source_item.ref,))
    work = work.model_copy(update={"scope": scope})
    snapshot = replace(snapshot, records=(*snapshot.records, source_item, scope, work))
    gap = gap.model_copy(update={"obligation": work})
    operations.admissions.clear()
    result = service.resolve(snapshot, work, gap)
    assert not operations.admissions
    assert all(child.kind.value == "discharge_citation_hypotheses" for child in result.children)
    assert source_item not in result.records
    validate_structure(snapshot, LedgerSnapshot(records=(*snapshot.records, *result.records, *result.children)))


def test_unavailable_independent_reader_leaves_citation_open(tmp_path):
    library, snapshot, work, gap = fixture(tmp_path)
    service, operations = resolver(tmp_path, library)

    def unavailable(*args):
        raise ConnectionError("reader unavailable")

    operations.run_structured = unavailable
    result = service.resolve(snapshot, work, gap)
    assert "unavailable" in result.detail
    assert not operations.admissions
    assert not result.evidence


def test_unversioned_search_hit_cannot_become_a_citation(tmp_path):
    from hardy.workflows.acquisition.literature import LiteratureSelection
    library, snapshot, work, gap = fixture(tmp_path)
    service, operations = resolver(tmp_path, library, search=lambda query: (
        LiteratureSelection(paper_id="2401.00002", statement_ref="main"),))
    with pytest.raises(ValueError, match="exact paper version"):
        service.resolve(snapshot, work, gap)
    assert not operations.prepared


def test_candidate_limit_leaves_unresolved_instead_of_claiming_absence(tmp_path):
    library, snapshot, work, gap = fixture(tmp_path)
    service, operations = resolver(tmp_path, library, max_candidates=1)
    result = service.resolve(snapshot, work, gap)
    assert "limit reached" in result.detail
    assert not operations.prepared
    assert not result.evidence
    assert not any(isinstance(record, CitationContract) for record in result.records)
