"""Application service helpers for backup restore artifacts workflows."""

from __future__ import annotations

from collections.abc import Sequence
from typing import cast

from foundry_lite.application.ports import BackupArtifactConflictError, BackupArtifactStore
from foundry_lite.application.ports.runtime_repository import RuntimeRepository, RuntimeRow
from foundry_lite.application.ports.transaction_context import TransactionManager
from foundry_lite.application.runtime_repository_backup_restore import (
    BackupRestoreArtifactReceipt,
    BackupRestorePreflightReport,
)
from foundry_lite.application.services.dataset.protocols import DatasetRuntimeBoundary
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected


class BackupRestoreArtifactMixin:
    """Backup artifact creation flow layered on top of restore preflight."""

    engine: TransactionManager
    backup_artifact_store: BackupArtifactStore
    runtime_repository: RuntimeRepository
    runtime_service: DatasetRuntimeBoundary

    def restore_preflight_report(
        self,
        *,
        ctx: RequestContext | None = None,
        backup_id: str | None = None,
    ) -> BackupRestorePreflightReport:
        raise NotImplementedError

    def create_backup_artifact(
        self,
        *,
        ctx: RequestContext | None = None,
        backup_id: str | None = None,
    ) -> BackupRestoreArtifactReceipt:
        ctx = ctx or RequestContext()
        resolved_backup_id = backup_id or f"backup-{ctx.request_id}"
        self.runtime_service._require_or_audit(ctx, "operations:retry", "backup_restore", resolved_backup_id)
        existing = _existing_backup_artifact_receipt(self.runtime_repository, ctx, resolved_backup_id)
        if existing is not None:
            return existing
        report = self.restore_preflight_report(ctx=ctx, backup_id=resolved_backup_id)
        if report["status"] != "ready":
            self._audit_backup_artifact_failed(ctx, report)
            raise ConflictDetected(
                "backup artifact requires a ready restore preflight",
                details={"backup_id": resolved_backup_id, "issue_count": len(report["issues"])},
            )
        try:
            receipt = self.backup_artifact_store.write_backup_artifact(report)
        except BackupArtifactConflictError as exc:
            raise ConflictDetected(
                "backup artifact id already points at different commit-point evidence",
                details={
                    "backup_id": exc.backup_id,
                    "existing_hash": exc.existing_hash,
                    "requested_hash": exc.requested_hash,
                },
            ) from exc
        if not receipt["isIdempotentReplay"]:
            self._audit_backup_artifact_created(ctx, receipt)
        return receipt

    def _audit_backup_artifact_created(
        self,
        ctx: RequestContext,
        receipt: BackupRestoreArtifactReceipt,
    ) -> None:
        with self.engine.begin() as conn:
            self.runtime_service._audit(
                conn,
                ctx,
                event_type="backup_restore.artifact_created",
                resource_type="backup_restore",
                resource_id=receipt["backupId"],
                action="operations:retry",
                after_ref=receipt,
                correlation_id=ctx.request_id,
            )

    def _audit_backup_artifact_failed(
        self,
        ctx: RequestContext,
        report: BackupRestorePreflightReport,
    ) -> None:
        with self.engine.begin() as conn:
            self.runtime_service._audit(
                conn,
                ctx,
                event_type="backup_restore.artifact_failed",
                resource_type="backup_restore",
                resource_id=report["backupId"],
                action="operations:retry",
                decision="deny",
                after_ref={
                    "status": report["status"],
                    "issueCount": len(report["issues"]),
                    "reason": "preflight_not_ready",
                },
                correlation_id=ctx.request_id,
            )


def _existing_backup_artifact_receipt(
    repository: RuntimeRepository,
    ctx: RequestContext,
    backup_id: str,
) -> BackupRestoreArtifactReceipt | None:
    snapshot = repository.list_runs(tenant_id=ctx.tenant_id)
    return _latest_artifact_receipt(snapshot["auditEvents"], backup_id)


def _latest_artifact_receipt(
    audit_events: Sequence[RuntimeRow],
    backup_id: str,
) -> BackupRestoreArtifactReceipt | None:
    for row in audit_events:
        if row.get("event_type") == "backup_restore.artifact_created" and row.get("resource_id") == backup_id:
            receipt = dict(cast(dict[str, object], row.get("after_ref", {})))
            receipt["status"] = "existing"
            receipt["isIdempotentReplay"] = True
            return cast(BackupRestoreArtifactReceipt, receipt)
    return None
