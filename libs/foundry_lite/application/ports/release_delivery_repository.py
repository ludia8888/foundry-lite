"""Persistence contract for externally delivered Governed Release operations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from foundry_lite.application.ports.transaction_context import TransactionContext

ReleaseDeliveryOperation = Literal[
    "source_publish",
    "source_merge",
    "application_deploy",
    "application_rollback",
]
ReleaseDeliveryKind = Literal["ontology", "pipeline"]
ReleaseDeliveryStatus = Literal["prepared", "dispatching", "landed", "absent", "ambiguous", "failed"]
ReleaseDeliveryTerminalStatus = Literal["landed", "absent", "ambiguous", "failed"]
ReleaseDeliveryReconciledStatus = Literal["landed", "absent", "failed"]
ReleaseDeliveryJson = dict[str, object]


class ReleaseDeliveryIntegrityError(RuntimeError):
    """Raised when persistence cannot resolve an ignored delivery insert."""


class ReleaseDeliveryIdempotencyConflict(RuntimeError):
    """Raised when one delivery key is reused for a different request."""


class ReleaseDeliveryTerminalConflict(RuntimeError):
    """Raised when a terminal delivery is replayed with a different outcome."""


@dataclass(frozen=True, slots=True)
class ReleaseDeliveryRecord:
    """Durable intent and sanitized external-provider outcome."""

    delivery_id: str
    tenant_id: str
    application_id: str
    proposal_id: str
    release_kind: ReleaseDeliveryKind
    workflow_run_id: str
    parent_delivery_id: str | None
    provider: str
    operation: ReleaseDeliveryOperation
    status: ReleaseDeliveryStatus
    target_ref: ReleaseDeliveryJson
    candidate_ref: ReleaseDeliveryJson | None
    environment: str
    idempotency_key: str
    request_fingerprint: str
    provider_operation_id: str | None
    provider_resource_id: str | None
    prior_resource_id: str | None
    result_ref: ReleaseDeliveryJson | None
    error_ref: ReleaseDeliveryJson | None
    ai_run_id: str
    binding_hash: str
    execution_attempt: int
    request_id: str
    created_by: str
    created_at: str
    updated_at: str
    dispatch_started_at: str | None
    completed_at: str | None


@dataclass(frozen=True, slots=True)
class PrepareReleaseDeliveryCommand:
    """Write-ahead intent that must commit before provider HTTP begins."""

    delivery_id: str
    tenant_id: str
    application_id: str
    proposal_id: str
    release_kind: ReleaseDeliveryKind
    workflow_run_id: str
    parent_delivery_id: str | None
    provider: str
    operation: ReleaseDeliveryOperation
    target_ref: ReleaseDeliveryJson
    candidate_ref: ReleaseDeliveryJson | None
    environment: str
    idempotency_key: str
    request_fingerprint: str
    prior_resource_id: str | None
    ai_run_id: str
    binding_hash: str
    request_id: str
    created_by: str
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class ClaimReleaseDeliveryCommand:
    """CAS one prepared intent into a fenced dispatch attempt."""

    tenant_id: str
    delivery_id: str
    expected_status: Literal["prepared"]
    expected_attempt: int
    dispatch_started_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class CompleteReleaseDeliveryCommand:
    """CAS a dispatch into one sanitized terminal provider outcome."""

    tenant_id: str
    delivery_id: str
    expected_status: Literal["dispatching"]
    expected_attempt: int
    terminal_status: ReleaseDeliveryTerminalStatus
    provider_operation_id: str | None
    provider_resource_id: str | None
    prior_resource_id: str | None
    result_ref: ReleaseDeliveryJson | None
    error_ref: ReleaseDeliveryJson | None
    completed_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class ReconcileReleaseDeliveryCommand:
    """Resolve an ambiguous provider outcome through read-only remote lookup."""

    tenant_id: str
    delivery_id: str
    expected_status: Literal["ambiguous"]
    expected_attempt: int
    terminal_status: ReleaseDeliveryReconciledStatus
    provider_operation_id: str | None
    provider_resource_id: str | None
    prior_resource_id: str | None
    result_ref: ReleaseDeliveryJson | None
    error_ref: ReleaseDeliveryJson | None
    completed_at: str
    updated_at: str


ReleaseDeliveryMutationResult = tuple[ReleaseDeliveryRecord, bool]


class ReleaseDeliveryRepository(Protocol):
    """Tenant-scoped write-ahead ledger for external release delivery."""

    def prepare(
        self,
        *,
        transaction: TransactionContext,
        command: PrepareReleaseDeliveryCommand,
    ) -> ReleaseDeliveryMutationResult:
        """Return ``(record, is_created)`` for a new or exact replayed intent."""
        ...

    def get(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        delivery_id: str,
    ) -> ReleaseDeliveryRecord | None: ...

    def find_by_idempotency(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        provider: str,
        operation: ReleaseDeliveryOperation,
        idempotency_key: str,
    ) -> ReleaseDeliveryRecord | None: ...

    def claim_dispatch(
        self,
        *,
        transaction: TransactionContext,
        command: ClaimReleaseDeliveryCommand,
    ) -> ReleaseDeliveryRecord | None:
        """Return the sole winning dispatch claim, or ``None`` on CAS loss."""
        ...

    def complete(
        self,
        *,
        transaction: TransactionContext,
        command: CompleteReleaseDeliveryCommand,
    ) -> ReleaseDeliveryMutationResult | None:
        """Return ``(record, is_transitioned)`` or ``None`` on a stale CAS."""
        ...

    def reconcile(
        self,
        *,
        transaction: TransactionContext,
        command: ReconcileReleaseDeliveryCommand,
    ) -> ReleaseDeliveryMutationResult | None:
        """CAS an ambiguous lookup outcome to landed, absent, or known failed."""
        ...

    def list_for_proposal(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        proposal_id: str,
        limit: int,
    ) -> tuple[ReleaseDeliveryRecord, ...]: ...

    def list_for_workflow(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        application_id: str,
        workflow_run_id: str,
        limit: int,
    ) -> tuple[ReleaseDeliveryRecord, ...]:
        """List one server-owned workflow chain in deterministic creation order."""
        ...

    def list_workflow_roots(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        application_id: str,
        release_kind: ReleaseDeliveryKind,
        limit: int,
    ) -> tuple[ReleaseDeliveryRecord, ...]:
        """List recent landed source-publication roots for one application and kind."""
        ...

    def list_by_statuses(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        statuses: tuple[ReleaseDeliveryStatus, ...],
        limit: int,
    ) -> tuple[ReleaseDeliveryRecord, ...]:
        """List oldest matching rows for bounded reconciliation sweeps."""
        ...


__all__ = [
    "ClaimReleaseDeliveryCommand",
    "CompleteReleaseDeliveryCommand",
    "PrepareReleaseDeliveryCommand",
    "ReconcileReleaseDeliveryCommand",
    "ReleaseDeliveryIdempotencyConflict",
    "ReleaseDeliveryIntegrityError",
    "ReleaseDeliveryJson",
    "ReleaseDeliveryKind",
    "ReleaseDeliveryMutationResult",
    "ReleaseDeliveryOperation",
    "ReleaseDeliveryRecord",
    "ReleaseDeliveryReconciledStatus",
    "ReleaseDeliveryRepository",
    "ReleaseDeliveryStatus",
    "ReleaseDeliveryTerminalConflict",
    "ReleaseDeliveryTerminalStatus",
]
