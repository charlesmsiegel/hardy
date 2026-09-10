"""D0 composes four real acquisition routes through Research and ledger policy.

Paper/source/trajectory IO, A2/A3 policy, C0/C1/C2/C4/C5, FinalVerifier, and B2
are real. Approval/model judgments and Lean process responses are deterministic
substitutes; admission probes explicitly report unavailable. These tests establish
orchestration and evidence accounting, not a live proof or successful probe.
"""
import json
import tarfile
from dataclasses import replace
from datetime import UTC, datetime
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest
from test_acquisition_literature import Operations
from test_core_c_acceptance import CapabilityOwners

from hardy.formal.contracts import DeclaredAssumption, EnvironmentIdentity, FormalizationProposal
from hardy.formal.verifier import FinalVerifier
from hardy.foundation.process import ProcessResult
from hardy.literature.client import ArxivClient
from hardy.literature.library import PaperLibrary
from hardy.literature.metadata import ArxivError, parse_id
from hardy.literature.statements import survey
from hardy.workflows.acquisition.classifier import GapClassifier
from hardy.workflows.acquisition.contracts import (
    ClassifiedGap,
    GapDecision,
    ResolverResult,
    SearchMatch,
    SearchRecord,
)
from hardy.workflows.acquisition.definitions import (
    DefinitionMapping,
    DefinitionMaterialization,
    DefinitionResolver,
    LocalDefinition,
)
from hardy.workflows.acquisition.literature import (
    LiteratureComparison,
    LiteratureResolver,
    LiteratureSelection,
)
from hardy.workflows.acquisition.resolver import RecursiveResolver
from hardy.workflows.admission import AdmissionPolicy, ProbeOperations
from hardy.workflows.contracts import ProofSubmission, RunLimits
from hardy.workflows.formalization import StandaloneFormalizationInput, prepare_candidate
from hardy.workflows.ledger.contracts import (
    ArtifactRef,
    CitationContract,
    Obligation,
    ProjectItem,
    Relation,
    Scope,
)
from hardy.workflows.ledger.policy import AuthenticatedEvidence, LedgerPolicy, ScopeChangeDecision
from hardy.workflows.ledger.state import LedgerSnapshot
from hardy.workflows.ledger.store import LedgerStore
from hardy.workflows.representation import RepresentationModel
from hardy.workflows.research import ResearchPlan, ResearchRequest, ResearchWorkflow
from hardy.workflows.storage import RunStore
from hardy.workflows.strategies.contracts import ProofTask, run_strategy
from hardy.workflows.strategies.iterative import IterativeStrategy

NOW = datetime(2026, 9, 10, tzinfo=UTC)
PAPER = "2609.00001v1"
SOURCE = "For every natural number n, n + 0 = n."
MODEL = RepresentationModel(provider="fixture", model="four-route-script", configuration=())
ENV = EnvironmentIdentity(lean_version="fixture-4", lean_commit="fixture-lean",
                          mathlib_revision="fixture-mathlib", lake_manifest_sha256="a" * 64)


def paper_library(tmp_path):
    library = PaperLibrary(tmp_path / "papers")
    feed = f'''<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
      <entry><id>http://arxiv.org/abs/{PAPER}</id><title>Synthetic addition theorem</title>
      <summary>Deterministic D0 fixture</summary><author><name>Fixture Author</name></author>
      <published>2026-09-01T00:00:00Z</published><updated>2026-09-01T00:00:00Z</updated>
      <arxiv:primary_category term="math.NT"/><category term="math.NT"/></entry></feed>'''.encode()
    ArxivClient(library, transport=lambda *_: feed, clock=lambda: 1000000.0, sleep=lambda _: None).fetch(PAPER)
    body = (r"\documentclass{article}\begin{document}\begin{theorem}\label{add-zero}" + SOURCE
            + r"\end{theorem}\end{document}").encode()
    buffer = BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        info = tarfile.TarInfo("main.tex")
        info.size = len(body)
        archive.addfile(info, BytesIO(body))
    library.admit_source(parse_id(PAPER), buffer.getvalue(), source_url=f"https://arxiv.org/src/{PAPER}",
                         fetched_at=NOW.isoformat())
    return library


def prepared(text, name, proposition, *, imports=("Mathlib",)):
    proposal = FormalizationProposal(restatement=text, theorem_name=name, proposition=proposition,
        binders="", domains=(), quantifiers=(), assumptions=(), interpretation_choices=())
    return prepare_candidate(StandaloneFormalizationInput(text=text), proposal,
        ENV.model_copy(update={"imports": imports}), NOW,
        lean=SimpleNamespace(check_proof=lambda *_: SimpleNamespace(success=True)))


class FourRouteOwners(CapabilityOwners):
    def __init__(self, root, library):
        super().__init__(root)
        self.library = library
        self.approvals = {}
        self.policy = LedgerPolicy(read_evidence=self.read_evidence, read_decision=self.decisions.get,
                                   read_scope_change=lambda old, new: self.approvals.get(new.ref))
        self.verifications = {}

    def read_evidence(self, reference):
        if reference.kind == "literature":
            value = self.evidence.get(reference)
            try:
                text = survey(self.library.source_texts(parse_id(PAPER))).statements[0].text
            except (ArxivError, ValueError, OSError):
                return None
            return value if value is not None and sha256(text.encode()).hexdigest() == reference.artifact.digest else None
        return super().read_evidence(reference)

    def define(self, query, candidate):
        self.events.append("mathlib" if isinstance(candidate, DefinitionMapping) else "local-definition")
        name = "Carrier.lean" if isinstance(candidate, DefinitionMapping) else "FixtureDefinitions.lean"
        source = "import Mathlib\nabbrev Carrier := Nat\n" if isinstance(candidate, DefinitionMapping) else candidate.source
        formal = self.record(query.obligation, name, source, "formal", "elaborated")
        reading = self.record(query.obligation, name + ".review.json", formal.artifact.digest, "faithfulness", "faithful")
        return DefinitionMaterialization(artifacts=(formal.artifact, reading.artifact), evidence=(formal, reading))

    def prove(self, snapshot, work, gap, *, source=None):
        is_main = work.item.id == "Main"
        self.events.append("main-proof" if is_main else "local-proof")
        subject = snapshot.get(work.item)
        name, proposition = ("Main", "two + 0 = 2") if is_main else ("two_eq_two", "two = 2")
        imports = ("Mathlib", "FixtureDefinitions", "FixtureLemma") if is_main else ("Mathlib", "FixtureDefinitions")
        candidate = prepared(subject.statement, name, proposition, imports=imports)
        allowed = (DeclaredAssumption(name="background_add_zero", statement="forall n : Nat, n + 0 = n",
            source=f"arxiv:{PAPER}#add-zero", justification="Explicit fixture approval of the exact source"),) if is_main else ()
        task = ProofTask(claim=candidate.claim, declared_assumptions=allowed,
                         limits=RunLimits(active_seconds=10, proof_seconds=5, official_checks=1))
        run = RunStore.create(self.root / "runs", name, now=NOW, run_id=UUID(int=2 if is_main else 3))

        def lean_process(spec):
            reconstructed = Path(spec.argv[-1]).read_text(encoding="utf-8")
            assert f"theorem {name}" in reconstructed
            message = f"{name} depends on axioms: [background_add_zero]" if is_main else f"{name} depends on axioms: []"
            return ProcessResult(argv=spec.argv, cwd=spec.cwd, returncode=0,
                stdout=json.dumps({"severity": "information", "data": message}), stderr="",
                timed_out=False, output_overflow=False, duration_ms=1)

        verifier = FinalVerifier(lake=self.root / "fixture-lake", lean_project=self.root,
                                 environment=task.claim.environment, limits=task.limits, runner=lean_process)
        body = "by\n  calc two + 0 = two := background_add_zero two\n       _ = 2 := two_eq_two" if is_main else "by rfl"
        strategy = IterativeStrategy(propose=lambda _: ProofSubmission(proof_body=body, informal_proof="The recorded equalities compose."),
            verify=lambda task, submission: verifier.verify(task.claim, submission.proof_body, run, task.declared_assumptions),
            transition=lambda _: None, check_cancelled=lambda: None, active_elapsed=lambda: 0, monotonic=lambda: 0)
        outcome = run_strategy(strategy, task)
        assert outcome.evidence is not None
        self.verifications[work.item.id] = strategy.last_verification
        content = (run.path / "lean" / "Main.lean").read_text(encoding="utf-8")
        ref = self.record(work, "Main.lean" if is_main else "FixtureLemma.lean", content, "formal", "kernel_proof")
        used = (source.ref,) if "background_add_zero" in outcome.evidence.axioms else ()
        self.evidence[ref] = replace(self.evidence[ref], used_assumptions=used)
        return ResolverResult(evidence=(ref,), detail="Real FinalVerifier over a scripted Lean process response")


def four_route_project(tmp_path):
    library = paper_library(tmp_path)
    store = LedgerStore(tmp_path / "project")
    main = ProjectItem(id="Main", name="Main", kind="theorem", origin="target_paper",
                       statement="The locally defined two plus zero equals two.")
    needed = ProjectItem(id="needed-source", name="Addition theorem", kind="external_result", origin="background_paper", statement=SOURCE)
    original_scope = Scope(id="scope", must_prove=(main.ref,))
    store.append((main, needed, original_scope), expected_revision=0)
    owners = FourRouteOwners(tmp_path / "capabilities", library)
    reading = Operations(tmp_path)
    reading.prepare = lambda request: prepared(request.text, "background_add_zero", "forall n : Nat, n + 0 = n")
    literature = LiteratureResolver(library=library, model=MODEL,
        search=lambda _: (LiteratureSelection(paper_id=PAPER, statement_ref="add-zero"),),
        compare=lambda query, candidate: LiteratureComparison(required_statement=query.subject.statement,
            required_hypotheses=(), hypotheses=(), conclusion=SOURCE, conclusion_matches=True,
            conclusion_reason="The complete universally quantified sentence is used, with no ambient hypotheses"),
        prepare=reading.prepare, review=reading.review, request_admission=reading.admit)
    probe = Obligation(id="source-proposal", kind="check_citation", item=needed.ref, scope=original_scope)
    initial = LedgerSnapshot((*store.read().records, probe), revision=store.read().revision)
    gap = ClassifiedGap(obligation=probe, kind="literature", reason="Obtain exact source before approval", searches=(), model=MODEL)
    proposal = literature.resolve(initial, probe, gap)
    source, = (r for r in proposal.records if isinstance(r, ProjectItem) and r.kind == "external_result")
    assert not proposal.evidence and proposal.children
    assert len(reading.admissions) == 1
    request, source_evidence, candidate = reading.admissions[0]
    admission = AdmissionPolicy()
    assert request.subject == source.ref and source_evidence.subject == source.ref
    assert admission.request_refusal(request) is None
    assert admission.source_refusal(source_evidence) is None

    def unavailable_probe(_):
        return SimpleNamespace(ok=False, diagnostics=(), output="Fixture has no live admission prover")

    check = admission.check_paper(candidate.claim.proposal.theorem_name,
        candidate.claim.proposal.theorem_name, candidate.claim.proposal.proposition, "statement",
        ProbeOperations(elaborate=unavailable_probe, refute=unavailable_probe))
    assert check.refusal is None and "could not" in check.checked
    check_note = ProjectItem(id="source-admission-check", name="Scripted approval after A3 checks",
        kind="research_note", origin="human_authored",
        statement=check.checked, semantics=(("source", source.ref.model_dump_json()),
                                           ("approval", "Fixture approves this exact source despite unavailable probes")))
    store.append((source, check_note), expected_revision=store.read().revision)
    scope = original_scope.model_copy(update={"allowed_background": (source.ref,)})
    owners.approvals[scope.ref] = ScopeChangeDecision(original_scope.ref, scope.ref, owners.policy.digest)
    store.append((scope,), expected_revision=store.read().revision, validate=owners.policy.validate)

    nat = ProjectItem(id="nat", name="Natural numbers", kind="standard_object", origin="mathlib", statement="The natural-number carrier.")
    two = ProjectItem(id="two", name="two", kind="definition", origin="generated_local", statement="The natural number two.")
    lemma = ProjectItem(id="two-lemma", name="Two equals two", kind="lemma", origin="generated_local", statement="The locally defined two equals two.")
    prerequisites = tuple(Obligation(id=f"acquire-{i.id}", kind="acquire_prerequisite", item=i.ref, scope=scope)
                          for i in (nat, two, lemma))
    citation_work = Obligation(id="check-source", kind="check_citation", item=needed.ref, scope=scope)
    match = SearchMatch(name="Nat", description="Lean's natural-number type",
                        artifact=ArtifactRef(uri="lean:Nat", digest="b" * 64))

    def mathlib_search(snapshot, work):
        return SearchRecord(source="mathlib", query=snapshot.get(work.item).name,
                            hits=(match,) if work.item == nat.ref else ())

    def route(query):
        if query.item.id == "nat":
            return GapDecision(kind="mathlib", selected=match.artifact, reason="Exact searched Nat mapping")
        return GapDecision(kind={"two": "cheap_local_definition", "two-lemma": "cheap_local_proof"}.get(query.item.id, "literature"),
                           reason="Scripted semantic decision; even an attempted target literature route is protected")

    classifier = GapClassifier(search_local=lambda s, w: SearchRecord(source="local", query=w.id),
                               search_mathlib=mathlib_search, decide=route, model=MODEL)
    definition = DefinitionResolver(model=MODEL,
        search_local=lambda q: SearchRecord(source="local", query=q.item.name),
        search_mathlib=lambda q: mathlib_search(q.snapshot, q.obligation),
        select_mapping=lambda options: DefinitionMapping(source="mathlib", hit=match, reason="Exact Nat carrier") if options.mathlib.hits else None,
        create_local=lambda _: LocalDefinition(name="two", lean_type="Nat", body="2", reason="Concrete numeral definition"),
        propose_opaque=lambda _: pytest.fail("No opaque fallback belongs in this fixture"), materialize=owners.define)

    def cited(snapshot, work, gap):
        owners.events.append("literature")
        result = literature.resolve(snapshot, work, gap)
        citation, = (r for r in result.records if isinstance(r, CitationContract))
        assert citation.source_hypotheses == () and citation.source_statement.digest == sha256(SOURCE.encode()).hexdigest()
        assert not result.children  # Exact global source was explicitly approved before the run.
        for ref in citation.evidence:
            outcome = "source_read" if ref.kind == "literature" else "faithful"
            owners.evidence[ref] = AuthenticatedEvidence(ref, scope.ref, None, outcome, citation=citation.ref)
            assert owners.read_evidence(ref) is not None  # Reread actual owner bytes.
        literature.read_evidence = owners.read_evidence
        return literature.resolve(snapshot, work, gap)

    recursive = RecursiveResolver(store, classifier=classifier, policy=owners.policy,
        resolvers={("mathlib", "define"): definition.resolve, ("cheap_local_definition", "define"): definition.resolve,
                   ("cheap_local_proof", "prove"): owners.prove, ("literature", "check_citation"): cited}, decide=owners.decide)
    records = (nat, two, lemma, *(Relation(id=f"main-uses-{i.id}", kind="depends_on", source=main.ref, target=i.ref)
                                  for i in (nat, two, lemma, source)),
        Relation(id="lemma-uses-two", kind="depends_on", source=lemma.ref, target=two.ref),
        Relation(id="main-citation", kind="blocked_by", source=main.ref, target=citation_work.ref))

    def formalization(snapshot, work, candidate):
        formal = owners.record(work, "main-statement.lean", candidate.claim.model_dump_json(), "formal", "elaborated")
        faithful = owners.record(work, "main-reading.json", candidate.claim.content_hash, "faithfulness", "faithful")
        return ResolverResult(evidence=(formal, faithful))

    flow = ResearchWorkflow(store, resolver=recursive,
        identify=lambda *_: ResearchPlan(records=records, prerequisites=(*prerequisites, citation_work)),
        prepare=lambda request: prepared(request.text, "Main", "two + 0 = 2", imports=("Mathlib", "FixtureDefinitions", "FixtureLemma")),
        record_formalization=formalization, prove=lambda s, w, g: owners.prove(s, w, g, source=source))
    request = ResearchRequest(id="research-main", target=main.ref, scope=scope.ref)
    return SimpleNamespace(store=store, owners=owners, flow=flow, request=request, main=main, scope=scope,
                           source=source, needed=needed, library=library, reading=reading)


def restart(project):
    store = LedgerStore(project.store.project)
    original = project.flow.resolver
    recursive = RecursiveResolver(store, classifier=original.classifier, policy=original.policy,
                                   resolvers=original.resolvers, decide=original.decide)
    return ResearchWorkflow(store, resolver=recursive, identify=project.flow.identify,
        prepare=project.flow.prepare, record_formalization=project.flow.record_formalization,
        prove=project.flow.prove)


def test_four_routes_establish_main_modulo_only_the_explicit_source_and_restart(tmp_path):
    project = four_route_project(tmp_path)
    report = project.flow.run(project.request)
    assert report.completed and report.established, report.reasons
    assert report.used_assumptions == (project.source.ref,)
    assert not report.outstanding
    assert sorted(project.owners.events) == ["literature", "local-definition", "local-proof", "main-proof", "mathlib"]
    state = project.store.read()
    kinds = {json.loads(dict(i.semantics)["classification"])["kind"] for i in state.current(ProjectItem)
             if "classification" in dict(i.semantics)}
    assert kinds == {"mathlib", "cheap_local_definition", "cheap_local_proof", "literature", "target_paper"}
    assert state.head("scope").allowed_background == (project.source.ref,)
    assert state.head("scope").allowed_interfaces == ()
    assert not project.owners.policy.premise_allowed(state, project.needed.ref, scope=project.scope, context=None)
    assert project.owners.verifications["Main"].axioms == ("background_add_zero",)
    assert project.owners.verifications["two-lemma"].axioms == ()
    assert len(project.reading.admissions) == 1
    reopened = LedgerStore(project.store.project)
    assert reopened.read() == state
    previous_events = tuple(project.owners.events)
    restarted = restart(project).run(project.request, max_attempts=0)
    assert restarted.established and restarted.completed
    assert restarted.used_assumptions == report.used_assumptions
    assert tuple(project.owners.events) == previous_events


def test_target_self_assumption_is_refused_even_with_a_scripted_scope_approval(tmp_path):
    project = four_route_project(tmp_path)
    malicious = Scope(id="another-scope", allowed_background=(project.main.ref,))
    project.owners.approvals[malicious.ref] = ScopeChangeDecision(None, malicious.ref, project.owners.policy.digest)
    before = project.store.read()
    with pytest.raises(ValueError, match="must_prove|target"):
        project.store.append((malicious,), expected_revision=before.revision, validate=project.owners.policy.validate)
    assert project.store.read() == before


@pytest.mark.parametrize("artifact", ["Main.lean", "FixtureLemma.lean", "FixtureDefinitions.lean", "paper-source", "source-review"])
def test_corrupt_capability_artifact_revokes_main_after_restart_without_rewriting_history(tmp_path, artifact):
    project = four_route_project(tmp_path)
    assert project.flow.run(project.request).established
    path = (project.library.path_for(parse_id(PAPER)) / "source" / "main.tex" if artifact == "paper-source"
            else project.reading.path / "faithfulness.json" if artifact == "source-review"
            else project.owners.root / artifact)
    path.write_text("Changed artifact bytes", encoding="utf-8")
    restarted = LedgerStore(project.store.project)
    assert restarted.read().head("research-main:target").status == "resolved"
    report = restart(project).run(project.request, max_attempts=0)
    assert not report.established and not report.completed
    assert report.outstanding and not report.used_assumptions
