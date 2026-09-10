"""E1/V0: real workflow composition over authored, explicitly scripted readings.

The boundary doubles supply semantics and capability receipts, never model or
Lean results. Inventory, ledger persistence, graph checks and policy run for real.
"""
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

import pytest

from hardy.literature.manuscript import SourceSpan, inventory
from hardy.workflows.acquisition.contracts import ResolverResult
from hardy.workflows.context import BindingSpec, ContextManager, DeclarationSpec
from hardy.workflows.critique import CritiqueFinding, CritiqueOperations, ReviewPass
from hardy.workflows.ledger.contracts import (
    ArtifactRef,
    CitationContract,
    EvidenceRef,
    HypothesisMapping,
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
)
from hardy.workflows.ledger.store import LedgerStore
from hardy.workflows.referee import (
    CitationUse,
    ManuscriptClaim,
    RefereeRequest,
    RefereeWorkflow,
)

FIXTURES = Path(__file__).parents[1] / "fixtures" / "referee_manuscript"


def statement_spans(sources):
    """Bind authored labels to exact statement bytes, without interpreting them."""
    scanned = inventory(sources)
    result = {}
    for env in scanned.environments:
        if env.kind == "proof":
            continue
        label, = (label for label in scanned.labels
                  if env.opening.end <= label.command.start < env.closing.start)
        text = sources[env.opening.path]
        start, end = label.command.end, env.closing.start
        while text[start].isspace():
            start += 1
        while text[end - 1].isspace():
            end -= 1
        result[label.value] = (env.opening, SourceSpan(
            env.opening.path, env.opening.digest, start, end))
    return result


@dataclass
class ManuscriptCase:
    flow: RefereeWorkflow
    request: RefereeRequest
    evidence: dict[EvidenceRef, AuthenticatedEvidence]
    decisions: dict[ArtifactRef, AcceptanceDecision]

    @property
    def store(self):
        return self.flow.store

    def accept(self, work, evidence, *, citation=None):
        """Explicit synthetic authority; the real policy still validates it."""
        outcomes = {"formal": "kernel_proof", "literature": "source_read", "faithfulness": "faithful"}
        for ref in evidence:
            self.evidence[ref] = AuthenticatedEvidence(
                ref, work.scope.ref, work.context, outcomes[ref.kind], citation=citation)
        proposal = Resolution(id=work.id + ":accept", obligation=work.ref,
                              item=work.item, evidence=evidence)
        receipt = ArtifactRef(uri="fixture-decision:" + work.id, digest=proposal.digest)
        self.decisions[receipt] = AcceptanceDecision(
            proposal.ref, work.ref, work.item, work.scope.ref, work.context, self.flow.policy.digest)
        accepted = self.flow.policy.accept(self.store.read(), proposal, receipt)
        closed = Obligation.model_validate({**work.model_dump(), "previous": work.ref,
                                           "status": "resolved", "resolution": accepted})
        self.store.append((closed,), expected_revision=self.store.read().revision,
                          validate=self.flow.policy.validate)


@pytest.fixture
def manuscript_case(tmp_path):
    sources = {"manuscript.tex": (FIXTURES / "manuscript.tex").read_text(encoding="utf-8")}
    background = {"background.tex": (FIXTURES / "background.tex").read_text(encoding="utf-8")}
    spans = statement_spans(sources)
    source_spans = statement_spans(background)
    store = LedgerStore(tmp_path / "project")
    manager = ContextManager(store)
    root = manager.create_root(id="ambient", label="Authored manuscript context")
    base = manager.extend(root.ref, id="declared", label="Declared X and M",
        declarations=(DeclarationSpec(id="X", symbol="X", semantic_type="topological space"),),
        bindings=(BindingSpec(id="M-original", kind="notation", symbol="M", meaning="the natural numbers"),))
    hidden = manager.extend(base.ref, id="hidden-finite", label="Unstated finite X",
        declarations=(DeclarationSpec(id="finite-X", symbol="h", semantic_type="X is finite",
                                      role="local_hypothesis"),))
    shadowed = manager.extend(base.ref, id="shadowed", label="Proof changes M",
        bindings=(BindingSpec(id="M-shadow", kind="notation", symbol="M", meaning="the singleton zero"),))
    items = {}
    for name, (_, span) in spans.items():
        items[name] = ProjectItem(id=name, kind="lemma" if name in {"cycle_a", "cycle_b", "side", "weak"}
            else "theorem", name=name, origin="target_paper", context=base.ref,
            statement=sources[span.path][span.start:span.end])
    weak = ProjectItem(id="binary-carrier", kind="representation", name="Carrier with binary operation",
                      origin="human_authored", statement="V is a carrier with addition V x V -> V; no laws.")
    strong = ProjectItem(id="real-vector-space", kind="representation", name="Real vector space",
                        origin="human_authored", statement="A real scalar action and the vector space laws.")
    compact = ProjectItem(id="compact-replacement", kind="representation", name="Compact replacement",
                         origin="generated_local")
    externals = {key: ProjectItem(id="external-" + key, kind="external_result", name=key,
        origin="background_paper", context=base.ref, statement=items[claim].statement)
        for key, claim in (("finite", "citation_good"), ("closed", "citation_missing"))}
    mains = tuple(item.ref for name, item in items.items() if name not in {"side", "cycle_b"})
    scope = Scope(id="manuscript-scope", must_prove=mains)
    dependencies = [(items[a], items[b]) for a, b in
                    (("cycle_a", "cycle_b"), ("cycle_b", "cycle_a"), ("strong", "weak"))]
    dependencies += [(items["weak"], weak), (items["citation_good"], externals["finite"]),
                     (items["citation_missing"], externals["closed"])]
    store.append((*items.values(), weak, strong, compact, *externals.values(), scope,
        *(Relation(id=a.id + "-needs-" + b.id, kind="depends_on", source=a.ref, target=b.ref)
          for a, b in dependencies),
        Relation(id="compact-wlog", kind="transported_from", source=items["wlog"].ref,
                 target=compact.ref, mappings=(("X", "a compact replacement of X"),))),
        expected_revision=store.read().revision)
    claims = []
    for name, (opening, span) in spans.items():
        claims.append(ManuscriptClaim(environment=opening, statement=span, item=items[name].ref,
            proof_context=hidden.ref if name == "drift" else None,
            observed_bindings=shadowed.bindings if name == "shadow" else (),
            required_representations=(weak.ref,) if name == "weak" else (strong.ref,) if name == "strong" else ()))
    uses = tuple(CitationUse(span=c.key_span,
        use_site=items["citation_good" if c.key == "finite" else "citation_missing"].ref,
        required_claim=externals[c.key].ref) for c in inventory(sources).citations)

    def scripted_prose_review(review):
        findings = (CritiqueFinding(kind="check_informal_step",
            reason="Scripted reading: boundedness does not imply convergence; a(n)=(-1)^n is a counterexample."),
        ) if review.subject.id == "unsupported" else ()
        return ReviewPass(subject=review.subject.ref, scope=review.scope.ref,
                          revision=review.snapshot.revision, findings=findings)

    def scripted_citation_reading(snapshot, work):
        key = "finite" if work.item == externals["finite"].ref else "closed"
        _, span = source_spans[key]
        artifact = ArtifactRef(uri="fixture:background.tex", digest=span.digest,
                               locator=f"{span.start}:{span.end}")
        reading = ArtifactRef(uri="fixture:scripted-reading:" + key,
            digest=sha256((key + ":fixture-v1").encode()).hexdigest())
        refs = tuple(EvidenceRef(kind=kind, artifact=value, subject=work.item, producer="scripted-fixture")
                     for kind, value in (("literature", artifact), ("faithfulness", reading)))
        children = () if key == "finite" else (Obligation(id="missing-closedness", item=work.item,
            context=work.context, scope=work.scope, kind="discharge_citation_hypotheses",
            reason="The manuscript has not established that the subset is closed."),)
        contract = CitationContract(id="contract-" + key, use_site=work.item, required_claim=work.item,
            paper_id="synthetic-background", paper_version="fixture-v1", source_statement=artifact,
            source_hypotheses=() if not children else ("the subset is closed",),
            hypothesis_mapping=() if not children else (HypothesisMapping(
                hypothesis="the subset is closed", obligation=children[0].ref),),
            conclusion=snapshot.get(work.item).statement, evidence=refs)
        return ResolverResult(records=(contract,), children=children,
                              detail="Authored semantic reading; no external model or paper fetch.")

    evidence, decisions = {}, {}
    policy = LedgerPolicy(read_evidence=evidence.get, read_decision=decisions.get)
    flow = RefereeWorkflow(store, critique=CritiqueOperations(adversarial=scripted_prose_review),
                           resolve_citation=scripted_citation_reading, policy=policy)
    return ManuscriptCase(flow, RefereeRequest(sources=sources, scope=scope.ref,
        main_results=mains, claims=tuple(claims), citations=uses), evidence, decisions)


def ids(references):
    return {ref.id for ref in references}


def test_manuscript_reports_all_ten_cases_with_exact_coverage(manuscript_case):
    """Catches omitted graph defects, conflated representation uses and false coverage."""
    case = manuscript_case
    report = case.flow.run(case.request)
    structural = {ref.id: findings for ref, findings in report.structural_findings}
    assert set(structural) == {"cycle_a", "cycle_b", "drift", "shadow", "wlog", "strong"}
    for name in ("cycle_a", "cycle_b"):
        assert any(f.kind == "check_informal_step" and "circular" in f.reason.lower()
                   and "cycle_a" in f.reason and "cycle_b" in f.reason for f in structural[name])
    assert {f.kind for f in structural["drift"]} == {"resolve_declaration"}
    assert "context" in structural["drift"][0].reason
    assert {f.kind for f in structural["shadow"]} == {"resolve_declaration"}
    assert "notation" in structural["shadow"][0].reason
    assert {f.kind for f in structural["wlog"]} == {"justify_transport"}
    assert {f.kind for f in structural["strong"]} == {"refine_representation"}
    critiques = {c.subject.id: c for c in report.critiques}
    assert not critiques["weak"].obligations
    assert not critiques["correct"].obligations
    prose, = critiques["unsupported"].obligations
    assert prose.kind == "check_informal_step" and "(-1)^n" in prose.reason
    assert case.store.read().get(prose.ref) == prose
    assert ids(report.selected) == {"correct", "citation_good", "citation_missing", "cycle_a", "cycle_b",
                                    "unsupported", "drift", "shadow", "wlog", "weak", "strong"}
    assert ids(report.unselected_claims) == {"side"}
    assert "side" not in ids(report.critical_path)
    assert not report.unmapped_claims and not report.unmapped_citations and not report.unreviewed_dependencies
    assert not report.inventory.unsupported
    assert not report.verified and not report.probed
    assert report.unresolved_claims == report.selected
    assert all(c.layers_run == ("adversarial",) and c.layers_skipped == ("kernel", "formalization")
               for c in report.critiques)
    assert not report.recursive_coverage_complete and report.citation_depth == 0
    assert "Semantic reading completeness is unverified" in report.summary
    assert "11 selected claims: 0 verified, 0 probed, 11 unresolved" in report.summary


def test_correct_receipts_missing_hypothesis_and_revocation_survive_restart(manuscript_case):
    """Catches citation/claim status being mistaken for authenticated exact evidence."""
    case = manuscript_case
    first = case.flow.run(case.request)
    good, missing = first.citations
    assert not good.checked and not missing.checked
    source = case.request.sources[good.use.span.path]
    assert source[good.use.span.start:good.use.span.end] == "finite"
    for audit in (good, missing):
        contract, = audit.contracts
        assert contract.paper_id == "synthetic-background" and contract.paper_version == "fixture-v1"
        background = (FIXTURES / "background.tex").read_text(encoding="utf-8")
        assert contract.source_statement.digest == sha256(background.encode()).hexdigest()
        begin, end = map(int, contract.source_statement.locator.split(":"))
        assert background[begin:end] == ("Every finite topological space is compact." if audit == good
                                       else "Every closed subset of a compact space is compact.")
    missing_work = case.store.read().get(missing.obligation)
    missing_contract, = missing.contracts
    with pytest.raises(ValueError, match="hypothesis discharge is unresolved"):
        case.accept(missing_work, missing_contract.evidence, citation=missing_contract.ref)
    assert "missing-closedness" in ids(missing.outstanding)
    assert case.store.read().head("missing-closedness").status == "open"
    good_contract, = good.contracts
    case.accept(case.store.read().get(good.obligation), good_contract.evidence, citation=good_contract.ref)
    correct = case.store.read().head("correct")
    work = Obligation(id="correct-proof", item=correct.ref, context=correct.context,
        scope=case.store.read().get(case.request.scope), kind="prove")
    case.store.append((work,), expected_revision=case.store.read().revision)
    receipt = EvidenceRef(kind="formal", subject=correct.ref, producer="synthetic-kernel-owner",
                          artifact=ArtifactRef(uri="fixture:proof", digest="a" * 64))
    case.accept(work, (receipt,))
    restarted = RefereeWorkflow(LedgerStore(case.store.project), critique=case.flow.critique,
        resolve_citation=case.flow.resolve_citation, policy=case.flow.policy)
    revision = case.store.read().revision
    report = restarted.run(case.request)
    assert ids(report.verified) == {"correct"}
    assert "correct" not in ids(report.unresolved_claims)
    assert report.citations[0].checked and not report.citations[1].checked
    assert report.citations[0].contracts == (good_contract,)
    assert case.store.read().revision == revision  # repeat review deduplicates durable work
    case.evidence.clear()
    revoked = restarted.run(case.request)
    assert not revoked.verified and not any(c.checked for c in revoked.citations)
    assert "correct" in ids(revoked.unresolved_claims)


def test_absent_semantic_reader_does_not_turn_structural_findings_into_review_coverage(manuscript_case):
    """Catches structural adapters claiming that an adversarial/model review ran."""
    case = manuscript_case
    case.flow.critique = CritiqueOperations()
    report = case.flow.run(case.request)
    assert all(c.layers_run == () and c.layers_skipped == ("kernel", "formalization", "adversarial")
               for c in report.critiques)
    assert not any(c.findings for c in report.critiques)
    assert {ref.id for ref, _ in report.structural_findings} >= {"cycle_a", "cycle_b"}
    assert not any(o.item.id == "unsupported" for o in case.store.read().current(Obligation))


def test_self_dependency_is_reported_without_misclassifying_unused_side_claim(manuscript_case):
    """A singleton SCC still represents a cycle when its claim depends on itself."""
    case = manuscript_case
    subject = case.store.read().head("correct")
    case.store.append((Relation(id="self-proof", kind="depends_on", source=subject.ref, target=subject.ref),),
                      expected_revision=case.store.read().revision)
    report = case.flow.run(case.request)
    findings = dict(report.structural_findings)[subject.ref]
    assert any(f.kind == "check_informal_step" and "circular" in f.reason.lower() for f in findings)
    assert ids(report.unselected_claims) == {"side"}
