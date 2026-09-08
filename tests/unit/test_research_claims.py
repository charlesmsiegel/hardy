from __future__ import annotations

from pathlib import Path

import pytest

from hardy.workflows.interactive.claims import ClaimLedger, ClaimRef, ClaimService, ClaimStatus
from hardy.workflows.interactive.record import SessionRecord


def service(path: Path) -> ClaimService:
    record = SessionRecord(path)
    record.load()
    return ClaimService(record.state, record._save_state, record._record)


def test_claim_ids_and_revisions_survive_restart(tmp_path):
    claims = service(tmp_path)
    first = claims.create("Every finite field has prime-power order.", title="Finite fields")
    revised = claims.revise(first.claim_id, "Every finite field has order p^n for a prime p.")

    reopened = service(tmp_path)
    assert reopened.get("C1", 1).informal_statement == "Every finite field has prime-power order."
    assert reopened.get("C1", 1).status is ClaimStatus.OPEN
    assert reopened.get("C1", 2) == revised
    assert reopened.create("A second claim").claim_id == "C2"


def test_dependencies_bind_exact_revisions_and_do_not_follow_current(tmp_path):
    claims = service(tmp_path)
    target = claims.create("Main theorem")
    lemma = claims.create("First reduction")
    claims.add_dependency(ClaimRef(claim_id="C1", revision=1), ClaimRef(claim_id="C2", revision=1))
    claims.revise("C2", "Changed reduction conclusion")

    assert claims.get("C1", 1).dependencies == (ClaimRef(claim_id="C2", revision=1),)
    assert "C2@r1" in claims.render_frontier()
    assert claims.get("C2", 2).evidence is None
    assert target.revision == lemma.revision == 1


def test_nonexistent_and_self_dependencies_fail_closed(tmp_path):
    claims = service(tmp_path)
    claims.create("Main theorem")
    with pytest.raises(ValueError, match="unknown claim"):
        claims.add_dependency(
            ClaimRef(claim_id="C1", revision=1), ClaimRef(claim_id="C9", revision=1)
        )
    with pytest.raises(ValueError, match="own claim"):
        claims.add_dependency(
            ClaimRef(claim_id="C1", revision=1), ClaimRef(claim_id="C1", revision=1)
        )


def test_legacy_session_without_claim_key_loads_as_empty(tmp_path):
    record = SessionRecord(tmp_path)
    record.load()
    record._save_state()
    assert service(tmp_path).ledger().claims == ()


def test_malformed_references_and_reused_allocator_fail_closed(tmp_path):
    with pytest.raises(ValueError, match="claim_id"):
        ClaimRef(claim_id="not-a-claim", revision=1)
    claims = service(tmp_path)
    claims.create("First")
    stored = claims.ledger().model_dump()
    stored["next_id"] = 1
    with pytest.raises(ValueError, match="next_id"):
        ClaimLedger.model_validate(stored)
