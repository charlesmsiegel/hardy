"""Authenticate live frozen statements, retained verifier artifacts and save effects."""
from __future__ import annotations

import json
from collections.abc import Callable
from hashlib import sha256
from uuid import uuid4

from hardy.agents.parsing import json_object
from hardy.formal import audit
from hardy.formal.contracts import freeze_claim
from hardy.formal.verifier import VerificationResult
from hardy.foundation.files import read_bytes
from hardy.foundation.journal import record_digest
from hardy.workflows.interactive.live_records import (
    FrozenFormalEvidence,
    LedgerEffects,
    ModelCallReceipt,
)
from hardy.workflows.interactive.semantic_owner import SemanticOwner
from hardy.workflows.interactive.semantic_versions import relevant_versions
from hardy.workflows.ledger.contracts import (
    ArtifactRef,
    EvidenceRef,
    ObligationKind,
    ProjectItem,
    Relation,
    Scope,
)
from hardy.workflows.ledger.policy import AuthenticatedEvidence
from hardy.workflows.ledger.state import LedgerSnapshot
from hardy.workflows.ledger.store import LedgerStore

PRODUCER = "hardy.live-formal"
URI = "hardy-live-formal:"
MODEL_URI = "hardy-live-model:"
EFFECT_URI = "hardy-controlled-effects:"
ARTIFACT_URI = "workflow-artifact:"


class LiveEvidence:
    def __init__(self, owners: SemanticOwner, *, source_inputs: Callable, translation_reviews: Callable):
        self.owners = owners
        self.source_inputs = source_inputs
        self.translation_reviews = translation_reviews
        self.environment: Callable[[], str] | None = None

    @staticmethod
    def reference(record: FrozenFormalEvidence, *, faithful: bool = False) -> EvidenceRef:
        return EvidenceRef(kind="faithfulness" if faithful else "formal", subject=record.subject,
            producer=PRODUCER, artifact=ArtifactRef(uri=URI + record.id, digest=record_digest(record),
                locator="faithful" if faithful else record.outcome))

    def artifact(self, reference: ArtifactRef) -> bytes:
        if not reference.uri.startswith(ARTIFACT_URI):
            raise ValueError("not a retained workflow artifact")
        data = read_bytes(self.owners.problem, reference.uri.removeprefix(ARTIFACT_URI))
        if sha256(data).hexdigest() != reference.digest:
            raise ValueError("retained workflow artifact digest changed")
        return data

    def human_review(self, subject, formalization, scope):
        legacy = {value.id for value in self.translation_reviews() if value.subject == subject
                  and value.translation == formalization and value.scope == scope}
        reviews = [value for value in self.owners.journal.read().records if value.id in legacy or (
            getattr(value, "purpose", None) == "correspondence" and getattr(value, "actor", None) != "model"
            and value.subject == subject and value.scope == scope
            and (formalization in value.inputs or value.verdict != "approved"))]
        return (reviews[-1], reviews[-1].id in legacy) if reviews else (None, False)

    def record(self, value: FrozenFormalEvidence) -> EvidenceRef:
        self.owners._append(value)
        return self.reference(value)

    def read(self, snapshot: LedgerSnapshot, reference: EvidenceRef) -> AuthenticatedEvidence | None:
        if reference.producer != PRODUCER:
            return None
        record = self.owners._find(FrozenFormalEvidence, URI, reference.artifact)
        if record is None or record.subject != reference.subject:
            return None
        try:
            item, formal, scope = snapshot.get(record.subject), snapshot.get(record.formalization), snapshot.get(record.scope)
            relation = snapshot.get(record.relation)
            if (not isinstance(item, ProjectItem) or not isinstance(formal, ProjectItem) or not isinstance(scope, Scope)
                    or item.statement != record.claim.original_text or item.context != record.context
                    or not isinstance(relation, Relation) or relation.kind != "formalizes"
                    or relation.source != formal.ref or relation.target != item.ref
                    or relevant_versions(self.owners.problem, snapshot, item.ref,
                        (formal.ref, scope.ref, relation.ref)) != record.inputs
                    or self.environment is None or self.environment() != record.environment):
                return None
            frozen = freeze_claim(record.claim.original_text, record.claim.proposal, record.claim.environment,
                record.claim.approved_at, semantic_context=record.claim.semantic_context)
            if frozen != record.claim:
                return None
            source = self.artifact(record.source)
            for _module, retained in record.retained_inputs:
                self.artifact(retained)
            for retained in record.environment_artifacts:
                self.artifact(retained)
            result = json.loads(self.artifact(record.result))
            if result.get("source_sha256") != sha256(source).hexdigest():
                return None
            if reference.artifact.locator == "faithful":
                if reference.kind.value != "faithfulness" or record.reading is None:
                    return None
                # A human dispute/approval supersedes the model reading for this
                # exact proposal. Elaborating a candidate never grants semantics.
                latest, legacy = self.human_review(item.ref, formal.ref, scope.ref)
                if latest is not None:
                    authentic = (self.owners._read_review(self.owners.review_reference(latest), snapshot=snapshot)
                        if legacy else self.owners.judgments.read(snapshot, self.owners.judgments.reference(latest)))
                    if authentic is None:
                        return None
                else:
                    reading = self.owners._find(ModelCallReceipt, MODEL_URI, record.reading.artifact)
                    if (reading is None or reading.status != "completed" or reading.teardown_pending or reading.tools
                            or reading.stage != "formalization.faithfulness"
                            or json.loads(reading.user_prompt)["input"]["claim"] != record.claim.model_dump(mode="json")
                            or record.claim.proposal.interpretation_choices):
                        return None
                    answer = json.loads(json_object(reading.response) or reading.response)
                    from hardy.workflows.contracts import FaithfulnessReview
                    if not FaithfulnessReview.model_validate(answer).agrees:
                        return None
                return AuthenticatedEvidence(reference, record.scope, record.context, "faithful",
                    obligation_kinds=(ObligationKind.FORMALIZE, ObligationKind.REPAIR))
            if reference.kind.value != "formal" or reference.artifact.locator != record.outcome:
                return None
            if record.outcome == "elaborated":
                if result.get("success") is not True:
                    return None
            else:
                checked = VerificationResult.model_validate(result)
                if (not checked.verified or checked.evidence is None or checked.evidence.claim_sha256 != record.claim.content_hash
                        or checked.evidence.toolchain != record.claim.environment or checked.axioms != record.axioms
                        or checked.evidence.source_sha256 != record.source.digest
                        or len(record.declared) != len(record.assumption_refs)):
                    return None
                names = {value.name: ref for value, ref in zip(record.declared, record.assumption_refs, strict=True)}
                verdict = audit.classify((audit.AxiomReport(declaration=record.claim.proposal.theorem_name, axioms=record.axioms),), list(names))
                if verdict.status not in {"clean", "modulo"}:
                    return None
                if tuple(names[name] for name in checked.assumed) != record.used_assumptions:
                    return None
                if not set(record.assumption_refs).issubset(scope.allowed_background + scope.allowed_interfaces):
                    return None
                if record.saved is None or snapshot.head(record.saved.id).ref != record.saved:
                    return None
                sources, signatures = self.owners._build_snapshot() if self.owners._build_snapshot else ({}, {})
                module = record.source.locator or ""
                if signatures.get(module) != record.signature or self.source_inputs(module, sources) != record.saved_inputs:
                    return None
            return AuthenticatedEvidence(reference, record.scope, record.context, record.outcome,
                used_assumptions=record.used_assumptions)
        except (ValueError, OSError, KeyError, TypeError):
            return None

    def effects(self, before: LedgerSnapshot, after: LedgerSnapshot, *, committed: tuple) -> ArtifactRef:
        if after.records[:len(before.records)] != before.records:
            raise ValueError("controlled save replaced ledger history")
        added = after.records[len(before.records):]
        if tuple(value.ref for value in added) != committed:
            raise ValueError("unrecognized ledger edit during controlled save")
        record = LedgerEffects(id=uuid4().hex, before_revision=before.revision, after_revision=after.revision,
            before=tuple(value.ref for value in before.records), added=tuple(value.ref for value in added))
        self.owners._append(record)
        return ArtifactRef(uri=EFFECT_URI + record.id, digest=record_digest(record))

    def read_effects(self, before: LedgerSnapshot, reference: ArtifactRef) -> LedgerSnapshot:
        record = self.owners._find(LedgerEffects, EFFECT_URI, reference)
        after = LedgerStore(self.owners.problem).read()
        if (record is None or record.before_revision != before.revision or record.after_revision != after.revision
                or record.before != tuple(value.ref for value in before.records)
                or tuple(value.ref for value in after.records) != (*record.before, *record.added)):
            raise ValueError("controlled save effects do not match exact ledger history")
        return after
