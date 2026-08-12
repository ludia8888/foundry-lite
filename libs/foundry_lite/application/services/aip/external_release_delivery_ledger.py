"""Transactional ledger mechanics shared by external release providers."""

from __future__ import annotations

from typing import cast

from foundry_lite.application.ports.release_delivery_repository import (
    ClaimReleaseDeliveryCommand,
    PrepareReleaseDeliveryCommand,
    ReleaseDeliveryIdempotencyConflict,
    ReleaseDeliveryOperation,
    ReleaseDeliveryRecord,
    ReleaseDeliveryRepository,
    ReleaseDeliveryTerminalConflict,
    ReleaseDeliveryTerminalStatus,
)
from foundry_lite.application.ports.transaction_context import TransactionContext, TransactionManager
from foundry_lite.application.services.aip.external_release_delivery_payloads import latest_rows
from foundry_lite.application.services.aip.external_release_delivery_support import (
    complete_command,
    reconcile_command,
    utc_now,
)
from foundry_lite.application.services.runtime_evidence_boundary import RuntimeEvidenceBoundary
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected


class ExternalReleaseDeliveryLedger:
    """Persist fenced delivery transitions with audit and outbox evidence."""

    def __init__(
        self,
        engine: TransactionManager,
        repository: ReleaseDeliveryRepository,
        runtime_service: RuntimeEvidenceBoundary,
    ) -> None:
        self._engine = engine
        self._repository = repository
        self._runtime_service = runtime_service

    def prepare(self, ctx: RequestContext, command: PrepareReleaseDeliveryCommand) -> ReleaseDeliveryRecord:
        try:
            with self._engine.begin() as transaction:
                row, is_created = self._repository.prepare(transaction=transaction, command=command)
                if is_created:
                    self._record_transition(transaction, ctx, row)
                return row
        except ReleaseDeliveryIdempotencyConflict as exc:
            raise ConflictDetected("external release idempotency key was reused for another request") from exc

    def claim(self, ctx: RequestContext, row: ReleaseDeliveryRecord) -> ReleaseDeliveryRecord | None:
        now = utc_now()
        command = ClaimReleaseDeliveryCommand(
            ctx.tenant_id,
            row.delivery_id,
            "prepared",
            row.execution_attempt,
            now,
            now,
        )
        with self._engine.begin() as transaction:
            claimed = self._repository.claim_dispatch(transaction=transaction, command=command)
            if claimed is not None:
                self._record_transition(transaction, ctx, claimed)
            return claimed

    def complete(
        self,
        ctx: RequestContext,
        row: ReleaseDeliveryRecord,
        status: ReleaseDeliveryTerminalStatus,
        result_ref: dict[str, object] | None,
        error_ref: dict[str, object] | None,
        provider_resource_id: str | None,
    ) -> ReleaseDeliveryRecord | None:
        command = complete_command(row, status, result_ref, error_ref, provider_resource_id)
        try:
            with self._engine.begin() as transaction:
                outcome = self._repository.complete(transaction=transaction, command=command)
                if outcome is None:
                    return None
                completed, is_transitioned = outcome
                if is_transitioned:
                    self._record_transition(transaction, ctx, completed)
                return completed
        except ReleaseDeliveryTerminalConflict as exc:
            raise ConflictDetected("external release terminal receipt conflicts with its durable replay") from exc

    def settle_lookup(
        self,
        ctx: RequestContext,
        row: ReleaseDeliveryRecord,
        status: str,
        result_ref: dict[str, object] | None,
        error_ref: dict[str, object] | None,
        provider_resource_id: str | None,
    ) -> ReleaseDeliveryRecord | None:
        if row.status == "dispatching":
            return self.complete(ctx, row, _terminal_status(status), result_ref, error_ref, provider_resource_id)
        command = reconcile_command(row, status, result_ref, error_ref, provider_resource_id)
        try:
            with self._engine.begin() as transaction:
                outcome = self._repository.reconcile(transaction=transaction, command=command)
                if outcome is None:
                    return None
                completed, is_transitioned = outcome
                if is_transitioned:
                    self._record_transition(transaction, ctx, completed)
                return completed
        except ReleaseDeliveryTerminalConflict as exc:
            raise ConflictDetected("external release reconciliation conflicts with its durable receipt") from exc

    def list_for_proposal(
        self,
        ctx: RequestContext,
        proposal_id: str,
    ) -> tuple[ReleaseDeliveryRecord, ...]:
        with self._engine.begin() as transaction:
            return self._repository.list_for_proposal(
                transaction=transaction,
                tenant_id=ctx.tenant_id,
                proposal_id=proposal_id,
                limit=100,
            )

    def get(self, ctx: RequestContext, delivery_id: str) -> ReleaseDeliveryRecord | None:
        """Read one exact tenant-scoped delivery used as a lineage parent."""

        with self._engine.begin() as transaction:
            return self._repository.get(
                transaction=transaction,
                tenant_id=ctx.tenant_id,
                delivery_id=delivery_id,
            )

    def latest(
        self,
        ctx: RequestContext,
        proposal_id: str,
        operation: ReleaseDeliveryOperation,
    ) -> ReleaseDeliveryRecord | None:
        rows = latest_rows(self.list_for_proposal(ctx, proposal_id), operation)
        return rows[0] if rows else None

    def find_by_idempotency(
        self,
        ctx: RequestContext,
        provider: str,
        operation: ReleaseDeliveryOperation,
        idempotency_key: str,
    ) -> ReleaseDeliveryRecord | None:
        """Read a prior intent before any provider discovery or mutation."""

        with self._engine.begin() as transaction:
            return self._repository.find_by_idempotency(
                transaction=transaction,
                tenant_id=ctx.tenant_id,
                provider=provider,
                operation=operation,
                idempotency_key=idempotency_key,
            )

    def _record_transition(
        self,
        transaction: TransactionContext,
        ctx: RequestContext,
        row: ReleaseDeliveryRecord,
    ) -> None:
        event_type = f"governed_release.delivery.{row.status}"
        after = _transition_evidence(row)
        self._runtime_service._audit(
            transaction,
            ctx,
            event_type=event_type,
            resource_type="governed_release_delivery",
            resource_id=row.delivery_id,
            action="governed_release:deliver",
            after_ref=after,
            correlation_id=row.ai_run_id,
        )
        self._runtime_service._outbox(
            transaction,
            ctx,
            event_type,
            "governed_release_delivery",
            row.delivery_id,
            after,
            idempotency_key=f"{event_type}:{row.delivery_id}:{row.execution_attempt}",
            correlation_id=row.ai_run_id,
        )


def _transition_evidence(row: ReleaseDeliveryRecord) -> dict[str, object]:
    return {
        "deliveryId": row.delivery_id,
        "applicationId": row.application_id,
        "proposalId": row.proposal_id,
        "releaseKind": row.release_kind,
        "workflowRunId": row.workflow_run_id,
        "parentDeliveryId": row.parent_delivery_id,
        "provider": row.provider,
        "operation": row.operation,
        "environment": row.environment,
        "status": row.status,
        "providerOperationId": row.provider_operation_id,
        "providerResourceId": row.provider_resource_id,
        "executionAttempt": row.execution_attempt,
    }


def _terminal_status(status: str) -> ReleaseDeliveryTerminalStatus:
    if status not in {"landed", "absent", "ambiguous", "failed"}:
        raise ValueError("invalid release delivery terminal status")
    return cast(ReleaseDeliveryTerminalStatus, status)


__all__ = ["ExternalReleaseDeliveryLedger"]
