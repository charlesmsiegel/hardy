"""Exact selections for durable mathematical result and referee exports."""
from __future__ import annotations

from pydantic import field_validator

from hardy.foundation.values import FrozenModel
from hardy.workflows.layout import validate_slug
from hardy.workflows.ledger.contracts import Digest, EvidenceRef, Text, VersionRef


class ResultExportRequest(FrozenModel):
    target: VersionRef
    output: Text
    original: VersionRef | None = None
    repaired: VersionRef | None = None
    scope: VersionRef | None = None
    formal: EvidenceRef | None = None

    @field_validator("output")
    @classmethod
    def valid_output(cls, value: str) -> str:
        return validate_slug(value)


class RefereeExportRequest(FrozenModel):
    receipt_id: Text
    receipt_digest: Digest
    output: Text

    @field_validator("output")
    @classmethod
    def valid_output(cls, value: str) -> str:
        return validate_slug(value)
