"""Source-to-claim interpretation: proposals, ambiguity, evidenced admission, and staleness.

A `SourceClaimLink` says that an exact source node and span express an exact
shared claim, with the notation and context mapping that reading needed. A
proposal writes only the link journal; it creates no shared claim and grants
no reuse. Admission needs an agreeing faithfulness verdict or an explicit
human approval, and only then does a proposed new claim enter the shared
ledger and the claim item gain the source span as an artifact. Several
proposals for one span coexist as ambiguity until one is admitted; the others
remain history. When a newer preferred tree changes the node a link named,
the link becomes review-needed, never silently deleted and never declared
false.
"""
from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Literal

from hardy.foundation.journal import Journal, JournalSnapshot, StaleRevision
from hardy.foundation.values import FrozenModel
from hardy.literature.sources.contracts import Digest, SourceSpan, StableId, Text
from hardy.literature.sources.library import ManagedLibrary
from hardy.workflows.contracts import FaithfulnessVerdict
from hardy.workflows.ledger.contracts import ArtifactRef, ProjectItem, VersionRef

from .ledger import SharedClaims

RETRIES = 5


class LinkRelation(str, Enum):
    EXPRESSES = "expresses"
    SPECIALIZES = "specializes"
    GENERALIZES = "generalizes"
    REFORMULATES = "reformulates"
    OTHER = "other"


class LinkStatus(str, Enum):
    PROPOSED = "proposed"
    AMBIGUOUS = "ambiguous"
    ADMITTED = "admitted"
    REJECTED = "rejected"
    REVIEW_NEEDED = "review_needed"


class HumanApproval(FrozenModel):
    actor: Text
    reason: Text
    at: str


class SourceClaimLink(FrozenModel):
    id: StableId
    artifact_sha256: Digest
    tree: StableId
    node: StableId
    node_version: Digest
    span: SourceSpan
    claim: VersionRef | None = None
    proposed_claim: ProjectItem | None = None
    candidates: tuple[VersionRef, ...] = ()
    relation: LinkRelation = LinkRelation.EXPRESSES
    notation_mapping: tuple[tuple[Text, Text], ...] = ()
    context_mapping: tuple[tuple[Text, Text], ...] = ()
    faithfulness: FaithfulnessVerdict | None = None
    approval: HumanApproval | None = None
    status: LinkStatus
    interpreter: Text
    history: tuple[Text, ...] = ()
    at: str


class InterpretationProposal(FrozenModel):
    claim_candidates: tuple[VersionRef, ...] = ()
    new_claim: ProjectItem | None = None
    relation: LinkRelation = LinkRelation.EXPRESSES
    notation_mapping: tuple[tuple[Text, Text], ...] = ()
    context_mapping: tuple[tuple[Text, Text], ...] = ()
    interpreter: Text
    confidence: float | None = None


class LinkError(ValueError):
    """A link transition the policy does not allow."""


def _stamp() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def source_artifact_ref(link: SourceClaimLink) -> ArtifactRef:
    """The exact source material a claim rests on, as a ledger artifact reference."""
    return ArtifactRef(uri=f"hardy-source:{link.artifact_sha256}/{link.tree}/{link.node}", digest=link.span.content_sha256, locator=link.span.id)


class LinkStore:
    def __init__(self, directory: Path) -> None:
        self._journal = Journal(Path(directory), types={"SourceClaimLink": SourceClaimLink})

    def snapshot(self) -> JournalSnapshot:
        return self._journal.read()

    def heads(self, snapshot: JournalSnapshot | None = None) -> dict[str, SourceClaimLink]:
        found: dict[str, SourceClaimLink] = {}
        for record in (snapshot or self.snapshot()).of(SourceClaimLink):
            found[record.id] = record
        return found

    def get(self, link_id: str) -> SourceClaimLink:
        try:
            return self.heads()[link_id]
        except KeyError:
            raise LinkError(f"unknown link {link_id}") from None

    def history(self, link_id: str) -> tuple[SourceClaimLink, ...]:
        return tuple(r for r in self.snapshot().of(SourceClaimLink) if r.id == link_id)

    def links_for_node(self, artifact_sha256: str, node: str) -> tuple[SourceClaimLink, ...]:
        return tuple(link for link in self.heads().values() if link.artifact_sha256 == artifact_sha256 and link.node == node)

    def links_for_artifact(self, artifact_sha256: str) -> tuple[SourceClaimLink, ...]:
        return tuple(link for link in self.heads().values() if link.artifact_sha256 == artifact_sha256)

    def links_for_claim(self, claim_id: str) -> tuple[SourceClaimLink, ...]:
        return tuple(link for link in self.heads().values() if link.claim is not None and link.claim.id == claim_id)

    def append(self, link: SourceClaimLink, *, expected_revision: int) -> JournalSnapshot:
        return self._journal.append([link], expected_revision=expected_revision, validate=_validate)

    def append_retrying(self, link: SourceClaimLink, *, precondition) -> SourceClaimLink:
        """Append a link whose validity does not depend on the exact head, retrying past races."""
        for _ in range(RETRIES):
            snapshot = self.snapshot()
            precondition(self.heads(snapshot))
            try:
                self.append(link, expected_revision=snapshot.revision)
                return link
            except StaleRevision:
                continue
        raise LinkError("the link journal kept moving; try again")


def _validate(before: JournalSnapshot, after: JournalSnapshot) -> None:
    heads: dict[str, SourceClaimLink] = {}
    for record in before.of(SourceClaimLink):
        heads[record.id] = record
    for record in after.records[len(before.records):]:
        assert isinstance(record, SourceClaimLink)
        if record.status is LinkStatus.ADMITTED:
            if record.claim is None:
                raise LinkError("an admitted link names an exact shared claim")
            if not ((record.faithfulness is not None and record.faithfulness.agreed) or record.approval is not None):
                raise LinkError("admission needs an agreeing faithfulness verdict or an explicit human approval")
            if record.approval is not None and not record.approval.actor.startswith("user:"):
                raise LinkError("a human approval names a user; a model is not an approver")
            if record.proposed_claim is not None:
                raise LinkError("an admitted link carries no unadmitted proposed claim")
        elif record.status in {LinkStatus.PROPOSED, LinkStatus.AMBIGUOUS}:
            if record.claim is None and record.proposed_claim is None and not record.candidates:
                raise LinkError("a proposal names a candidate claim, a proposed new claim, or both")
            if record.status is LinkStatus.PROPOSED and record.claim is not None and record.candidates:
                raise LinkError("a single-candidate proposal names one claim")
        previous = heads.get(record.id)
        if previous is not None:
            if (previous.artifact_sha256, previous.tree, previous.node, previous.span.id) != (record.artifact_sha256, record.tree, record.node, record.span.id):
                raise LinkError("a link's source identity never changes; propose a new link")
            if previous.status is LinkStatus.ADMITTED and record.status in {LinkStatus.PROPOSED, LinkStatus.AMBIGUOUS}:
                raise LinkError("an admitted link does not return to a proposal")
            if previous.status is LinkStatus.ADMITTED and record.status is LinkStatus.ADMITTED and previous.claim != record.claim:
                raise LinkError("an admitted link is not silently retargeted; reject it and propose another")
        heads[record.id] = record


class ClaimLinker:
    def __init__(self, *, library: ManagedLibrary, claims: SharedClaims, links: LinkStore) -> None:
        self.library = library
        self.claims = claims
        self.links = links

    # --- proposing ----------------------------------------------------------

    def propose(self, sha256: str, tree_id: str, node_id: str, proposal: InterpretationProposal) -> SourceClaimLink:
        tree = self.library.trees.get(sha256, tree_id)
        node = tree.node(node_id)
        span = node.statement_span or node.span
        for candidate in proposal.claim_candidates:
            self.claims.get(candidate)
        if proposal.new_claim is not None and proposal.new_claim.context is not None:
            raise LinkError("a proposed shared claim cannot be context-local")
        ambiguous = len(proposal.claim_candidates) + (1 if proposal.new_claim else 0) > 1
        for _ in range(RETRIES):
            # The id counts the node's links in the snapshot this attempt appends
            # against, so two interpretations proposed at once both survive: the
            # loser recomputes from the head the winner made, not from the one
            # both started from.
            snapshot = self.links.snapshot()
            heads = self.links.heads(snapshot)
            suffix = sum(1 for held in heads.values() if held.artifact_sha256 == sha256 and held.node == node_id) + 1
            link = SourceClaimLink(
                id=f"link-{sha256[:12]}-{node_id[2:]}-{suffix}", artifact_sha256=sha256, tree=tree_id, node=node_id, node_version=node.version,
                span=span, claim=proposal.claim_candidates[0] if len(proposal.claim_candidates) == 1 and proposal.new_claim is None else None,
                proposed_claim=proposal.new_claim, candidates=proposal.claim_candidates if ambiguous else (),
                relation=proposal.relation, notation_mapping=proposal.notation_mapping, context_mapping=proposal.context_mapping,
                status=LinkStatus.AMBIGUOUS if ambiguous else LinkStatus.PROPOSED, interpreter=proposal.interpreter,
                history=(f"proposed by {proposal.interpreter}" + (f" (confidence {proposal.confidence})" if proposal.confidence is not None else ""),),
                at=_stamp(),
            )
            if link.id in heads:
                raise LinkError(f"link {link.id} already exists")
            try:
                self.links.append(link, expected_revision=snapshot.revision)
                return link
            except StaleRevision:
                continue
        raise LinkError("the link journal kept moving; try again")

    # --- admitting ----------------------------------------------------------

    def admit(
        self, link_id: str, *, verdict: FaithfulnessVerdict | None = None, approval: HumanApproval | None = None,
        choose: VersionRef | Literal["proposed"] | None = None,
    ) -> SourceClaimLink:
        if verdict is not None and not verdict.agreed:
            raise LinkError(f"the faithfulness verdict is {verdict.outcome.value}; only an agreeing read admits a link")
        if verdict is None and approval is None:
            raise LinkError("admission needs an agreeing faithfulness verdict or an explicit human approval")
        if approval is not None and not approval.actor.startswith("user:"):
            raise LinkError("a human approval names a user; a model is not an approver")
        created: VersionRef | None = None  # the claim this admission minted, remembered across retries
        for _ in range(RETRIES):
            snapshot = self.links.snapshot()
            link = self.links.heads(snapshot).get(link_id)
            if link is None:
                raise LinkError(f"unknown link {link_id}")
            if link.status is LinkStatus.ADMITTED:
                raise LinkError(f"link {link_id} is already admitted")
            if link.status is LinkStatus.REJECTED:
                raise LinkError(f"link {link_id} was rejected; propose a new link")
            claim_ref, proposed = self._chosen(link, choose)
            if claim_ref is None:
                assert proposed is not None
                if created is not None and created.id == proposed.id:
                    # The ledger write already happened on an earlier pass; only
                    # the link append lost its race. Minting again would refuse
                    # the claim as a duplicate of our own.
                    claim_ref = self.claims.head(created.id).ref
                else:
                    ledger = self.claims.snapshot()
                    if any(i.id == proposed.id for i in ledger.current(ProjectItem)):
                        raise LinkError(f"shared claim {proposed.id} already exists; propose a link to it instead")
                    claim_item = proposed.model_copy(update={"artifacts": tuple(dict.fromkeys((*proposed.artifacts, source_artifact_ref(link))))})
                    self.claims.add_claim(ProjectItem.model_validate(claim_item.model_dump(mode="json")), expected_revision=ledger.revision)
                    created = claim_ref = self.claims.head(proposed.id).ref
            else:
                self._attach_source(link, claim_ref)
                claim_ref = self.claims.head(claim_ref.id).ref
            admitted = link.model_copy(update={
                "claim": claim_ref, "proposed_claim": None, "candidates": link.candidates, "faithfulness": verdict, "approval": approval,
                "status": LinkStatus.ADMITTED, "at": _stamp(),
                "history": (*link.history, "admitted with " + ("faithfulness verdict" if verdict else f"approval by {approval.actor}")),  # type: ignore[union-attr]
            })
            try:
                self.links.append(SourceClaimLink.model_validate(admitted.model_dump(mode="json")), expected_revision=snapshot.revision)
                return admitted
            except StaleRevision:
                continue
        raise LinkError("the link journal kept moving; try again")

    def _chosen(self, link: SourceClaimLink, choose: VersionRef | Literal["proposed"] | None) -> tuple[VersionRef | None, ProjectItem | None]:
        if link.status is LinkStatus.AMBIGUOUS:
            if choose is None:
                raise LinkError("an ambiguous link needs the adjudicated claim named at admission")
            if choose == "proposed":
                if link.proposed_claim is None:
                    raise LinkError("this link proposed no new claim")
                return None, link.proposed_claim
            if choose not in link.candidates:
                raise LinkError("the chosen claim is not one of the link's candidates")
            return choose, None
        if link.claim is not None:
            return link.claim, None
        return None, link.proposed_claim

    def _attach_source(self, link: SourceClaimLink, claim_ref: VersionRef) -> None:
        """Give the claim item the source span as an artifact; a new item revision, same identity."""
        ledger = self.claims.snapshot()
        item = ledger.head(claim_ref.id)
        assert isinstance(item, ProjectItem)
        if item.ref != claim_ref:
            # Attaching a source is itself a revision, so the head may move by
            # provenance alone; what must not have moved is the statement the
            # interpretation was of.
            selected = self.claims.get(claim_ref)
            if selected.model_dump(mode="json", exclude={"artifacts"}) != item.model_dump(mode="json", exclude={"artifacts"}):
                raise LinkError(f"claim {claim_ref.id} was revised since the proposal ({claim_ref.digest[:12]} is no longer its head); "
                                "the interpretation was of the earlier statement, so review it against the revision before admitting")
        artifact = source_artifact_ref(link)
        if artifact in item.artifacts:
            return
        updated = item.model_copy(update={"artifacts": (*item.artifacts, artifact)})
        self.claims.append((ProjectItem.model_validate(updated.model_dump(mode="json")),), expected_revision=ledger.revision)

    def reject(self, link_id: str, *, actor: str, reason: str) -> SourceClaimLink:
        snapshot = self.links.snapshot()
        link = self.links.heads(snapshot).get(link_id)
        if link is None:
            raise LinkError(f"unknown link {link_id}")
        rejected = link.model_copy(update={"status": LinkStatus.REJECTED, "history": (*link.history, f"rejected by {actor}: {reason}"), "at": _stamp()})
        self.links.append(rejected, expected_revision=snapshot.revision)
        return rejected

    # --- staleness ----------------------------------------------------------

    def stale_links(self, sha256: str, new_tree_id: str) -> tuple[SourceClaimLink, ...]:
        """Mark links whose node changed or vanished under a newer tree as review-needed."""
        tree = self.library.trees.get(sha256, new_tree_id)
        marked: list[SourceClaimLink] = []
        for link in self.links.links_for_artifact(sha256):
            if link.status in {LinkStatus.REJECTED, LinkStatus.REVIEW_NEEDED}:
                continue
            try:
                node = tree.node(link.node)
                unchanged = (node.statement_span or node.span).content_sha256 == link.span.content_sha256 and node.kind.value == tree.node(link.node).kind.value
            except KeyError:
                unchanged = False
            if unchanged:
                continue
            for _ in range(RETRIES):
                snapshot = self.links.snapshot()
                current = self.links.heads(snapshot)[link.id]
                if current.status in {LinkStatus.REVIEW_NEEDED, LinkStatus.REJECTED}:
                    break  # already under review, or rejected meanwhile: a rejection is final and is not softened
                flagged = current.model_copy(update={"status": LinkStatus.REVIEW_NEEDED, "at": _stamp(),
                                                     "history": (*current.history, f"node changed under tree {new_tree_id}; the old tree {link.tree} still resolves this link")})
                try:
                    self.links.append(flagged, expected_revision=snapshot.revision)
                    marked.append(flagged)
                    break
                except StaleRevision:
                    continue
        return tuple(marked)
