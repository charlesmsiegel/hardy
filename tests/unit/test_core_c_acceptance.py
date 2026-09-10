"""Hermetic C0/C1/C2/C4 compositions; scripted owners are not live Lean evidence."""
from hashlib import sha256
from pathlib import Path

from hardy.workflows.acquisition.classifier import GapClassifier
from hardy.workflows.acquisition.contracts import GapDecision, ResolverResult, SearchRecord
from hardy.workflows.acquisition.definitions import (
    DefinitionMaterialization,
    DefinitionResolver,
    LocalDefinition,
)
from hardy.workflows.acquisition.resolver import RecursiveResolver
from hardy.workflows.ledger.contracts import (
    ArtifactRef,
    CitationContract,
    EvidenceRef,
    Obligation,
    ProjectItem,
    Relation,
    Scope,
)
from hardy.workflows.ledger.policy import AcceptanceDecision, AuthenticatedEvidence, LedgerPolicy
from hardy.workflows.ledger.store import LedgerStore
from hardy.workflows.representation import RepresentationModel

MODEL = RepresentationModel(provider="fixture", model="scripted", configuration=())


class CapabilityOwners:
    """Test-only scripted judgments over actual bytes; each use rereads its artifact."""

    def __init__(self, root):
        self.root = root
        self.evidence = {}
        self.decisions = {}
        self.events = []
        self.policy = LedgerPolicy(read_evidence=self.read_evidence, read_decision=self.decisions.get)

    def record(self, work, name, content, kind, outcome):
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / name
        path.write_bytes(content.encode())
        reference = EvidenceRef(kind=kind, subject=work.item, producer="scripted-capability-owner",
            artifact=ArtifactRef(uri=str(path), digest=sha256(path.read_bytes()).hexdigest()))
        self.evidence[reference] = AuthenticatedEvidence(reference, work.scope.ref, work.context, outcome)
        return reference

    def read_evidence(self, reference):
        owned = self.evidence.get(reference)
        path = Path(reference.artifact.uri)
        if owned is None or not path.is_file() or sha256(path.read_bytes()).hexdigest() != reference.artifact.digest:
            return None
        return owned

    def decide(self, snapshot, proposal):
        work = snapshot.get(proposal.obligation)
        receipt = ArtifactRef(uri=f"fixture:decision:{proposal.id}", digest=proposal.digest)
        self.decisions[receipt] = AcceptanceDecision(proposal.ref, work.ref, work.item,
            work.scope.ref, work.context, self.policy.digest)
        return receipt

    def define(self, query, candidate):
        self.events.append("definition")
        formal = self.record(query.obligation, "Carrier.lean", candidate.source, "formal", "elaborated")
        reading = self.record(query.obligation, "definition-review.json",
            '{"fixture_verdict":"faithful","source_digest":"' + formal.artifact.digest + '"}',
            "faithfulness", "faithful")
        return DefinitionMaterialization(artifacts=(formal.artifact, reading.artifact), evidence=(formal, reading))

    def prove(self, snapshot, work, gap):
        self.events.append("proof")
        reference = self.record(work, "Identity.lean",
            "import Carrier\n\ntheorem identity : Carrier = Carrier := rfl\n", "formal", "kernel_proof")
        return ResolverResult(evidence=(reference,), detail="Scripted kernel result for the fixture")


def definition_pipeline(store, owners):
    classifier = GapClassifier(
        search_local=lambda snapshot, work: SearchRecord(source="local", query=snapshot.get(work.item).name),
        search_mathlib=lambda snapshot, work: SearchRecord(source="mathlib", query=snapshot.get(work.item).name),
        decide=lambda query: GapDecision(
            kind="cheap_local_definition" if query.item.kind == "definition" else "cheap_local_proof",
            reason="The fixture needs a concrete carrier before its reflexivity proof"), model=MODEL)
    definition = DefinitionResolver(model=MODEL,
        search_local=lambda query: SearchRecord(source="local", query=query.item.name),
        search_mathlib=lambda query: SearchRecord(source="mathlib", query=query.item.name),
        select_mapping=lambda options: None,
        create_local=lambda options: LocalDefinition(name="Carrier", lean_type="Type", body="Nat",
                                                      reason="A concrete natural-number carrier suffices"),
        propose_opaque=lambda options: None, materialize=owners.define)
    return RecursiveResolver(store, classifier=classifier, policy=owners.policy,
        resolvers={("cheap_local_definition", "define"): definition.resolve,
                   ("cheap_local_proof", "prove"): owners.prove}, decide=owners.decide)


def test_definition_acquisition_precedes_parent_proof_survives_restart_and_loses_revoked_authority(tmp_path):
    store = LedgerStore(tmp_path / "project")
    carrier = ProjectItem(id="carrier", kind="definition", name="Carrier", origin="human_authored",
                          statement="The natural-number carrier.")
    goal = ProjectItem(id="identity", kind="theorem", name="Carrier reflexivity", origin="human_authored",
                      statement="The carrier equals itself.")
    scope = Scope(id="scope")
    acquire = Obligation(id="acquire-carrier", kind="acquire_prerequisite", item=carrier.ref, scope=scope)
    prove = Obligation(id="prove-identity", kind="prove", item=goal.ref, scope=scope)
    dependency = Relation(id="identity-uses-carrier", kind="depends_on", source=goal.ref, target=carrier.ref)
    store.append((carrier, goal, scope, acquire, prove, dependency), expected_revision=0)
    owners = CapabilityOwners(tmp_path / "capabilities")
    result = definition_pipeline(store, owners).resolve(prove.ref)
    assert result.resolved, result.reasons
    assert owners.events == ["definition", "proof"]
    snapshot = store.read()
    assert snapshot.head(acquire.id).status == "resolved"
    definitions = [work for work in snapshot.current(Obligation) if work.kind == "define"]
    assert len(definitions) == 1
    assert definitions[0].status == "resolved"
    assert owners.policy.premise_allowed(snapshot, carrier.ref, scope=scope, context=None)
    assert owners.policy.premise_allowed(snapshot, goal.ref, scope=scope, context=None)
    assert any(dict(item.semantics).get("method") == "local_definition"
               for item in snapshot.current(ProjectItem))
    assert snapshot.head("scope").allowed_background == ()
    assert snapshot.head("scope").allowed_interfaces == ()

    reopened = LedgerStore(tmp_path / "project")
    restarted = definition_pipeline(reopened, owners)
    assert restarted.resolve(prove.ref, max_attempts=0).resolved
    assert owners.events == ["definition", "proof"]
    # A source artifact moving revokes both its typed definition and the proof
    # depending on it, even though the append-only history still says resolved.
    (owners.root / "Carrier.lean").write_text("def Carrier := False\n", encoding="utf-8")
    assert not owners.policy.premise_allowed(reopened.read(), carrier.ref, scope=scope, context=None)
    assert not owners.policy.premise_allowed(reopened.read(), goal.ref, scope=scope, context=None)
    result = restarted.resolve(prove.ref, max_attempts=0)
    assert not result.resolved
    assert result.outstanding
    assert reopened.read().head(prove.id).status == "resolved"
    assert owners.events == ["definition", "proof"]


def test_literature_resolver_through_recursive_dispatch_persists_open_citation_and_admission(tmp_path):
    from test_acquisition_literature import fixture, resolver

    library, initial, work, _gap = fixture(tmp_path)
    literature, operations = resolver(tmp_path, library)
    store = LedgerStore(tmp_path / "project")
    store.append(initial.records, expected_revision=0, activate=initial.active_context)
    classifier = GapClassifier(
        search_local=lambda snapshot, child: SearchRecord(source="local", query=child.id),
        search_mathlib=lambda snapshot, child: SearchRecord(source="mathlib", query=child.id),
        decide=lambda query: GapDecision(kind="literature" if query.obligation.id == work.id else "unresolved",
                                        reason="Scripted classification preserves open child obligations"), model=MODEL)
    recursive = RecursiveResolver(store, classifier=classifier, policy=LedgerPolicy(),
        resolvers={"literature": literature.resolve}, decide=lambda snapshot, proposal: None)
    result = recursive.resolve(work.ref)
    assert not result.resolved
    persisted = LedgerStore(tmp_path / "project").read()
    citation, = persisted.current(CitationContract)
    assert (citation.paper_id, citation.paper_version) == ("2401.00002", "v2")
    assert citation.required_claim == work.item
    assert citation.status == "open"
    children = [child for child in persisted.current(Obligation) if child.id != work.id]
    assert {child.kind.value for child in children} == {"discharge_citation_hypotheses", "resolve_ambiguity"}
    assert all(child.status == "open" for child in children)
    assert len(operations.admissions) == 1
    assert any("frozen-claim" in dict(item.semantics) for item in persisted.current(ProjectItem))
    assert not recursive.policy.premise_allowed(persisted, work.item, scope=work.scope, context=work.context)
    assert persisted.head(work.scope.id).allowed_background == ()
