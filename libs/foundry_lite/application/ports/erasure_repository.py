from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from foundry_lite.application.ports.transaction_context import TransactionContext

ErasureRequestStatus = str


@dataclass(frozen=True)
class ErasureRequestRecord:
    """Durable, raw-value-free record of a subject erasure request."""

    request_id: str
    tenant_id: str
    subject_kind: str
    subject_hash: str
    subject_token_key_id: str
    requested_by: str
    idempotency_key: str
    reason: str
    status: str
    evidence: dict[str, object]
    requested_at: str
    manifest_id: str | None = None


@dataclass(frozen=True)
class ErasureCertificateRecord:
    """Durable persisted erasure certificate for one executed request."""

    certificate_id: str
    tenant_id: str
    request_id: str
    manifest_id: str
    certificate: dict[str, object]
    certified_at: str


class ErasureRepository(Protocol):
    """DB boundary for durable erasure requests and persisted certificates."""

    def insert_request_or_get_existing(
        self, *, transaction: TransactionContext, record: ErasureRequestRecord
    ) -> ErasureRequestRecord | None:
        """Insert a request, returning the existing winner on idempotency conflict."""
        ...

    def request_by_id(
        self, *, transaction: TransactionContext, tenant_id: str, request_id: str
    ) -> ErasureRequestRecord | None:
        """Return one tenant-scoped erasure request by id."""
        ...

    def mark_request_executed(
        self, *, transaction: TransactionContext, tenant_id: str, request_id: str, manifest_id: str, executed_at: str
    ) -> None:
        """Record that a request's manifest has been executed and certified."""
        ...

    def insert_certificate_or_get_existing(
        self, *, transaction: TransactionContext, record: ErasureCertificateRecord
    ) -> ErasureCertificateRecord | None:
        """Persist a certificate, returning the existing one on (tenant, request) conflict."""
        ...

    def certificate_by_request(
        self, *, transaction: TransactionContext, tenant_id: str, request_id: str
    ) -> ErasureCertificateRecord | None:
        """Return the persisted certificate for one request, if any."""
        ...
