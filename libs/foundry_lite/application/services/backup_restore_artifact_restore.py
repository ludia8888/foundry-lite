"""Application service helpers for backup restore artifact restore workflows."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from foundry_lite.application.ports import (
    BackupArtifactIntegrityError,
    BackupArtifactNotFoundError,
    BackupArtifactStore,
    BackupRestoreArtifact,
    BackupRestoreArtifactReceipt,
    BackupRestoreArtifactRestoreReport,
    BackupRestoreArtifactRestoreStatus,
    BackupRestoreModeReport,
    BackupRestorePostRestoreValidationReport,
    BackupRestorePreflightReport,
    RuntimeJsonObject,
)
from foundry_lite.application.ports.transaction_context import TransactionManager
from foundry_lite.application.primitives import _now
from foundry_lite.application.services.backup_restore_preflight_service import BackupRestorePreflightService
from foundry_lite.application.services.backup_restore_validation import (
    BackupRestoreValidationService,
    post_restore_validation_report,
    restore_mode_report,
)
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.dataset.protocols import DatasetRuntimeBoundary
from foundry_lite.application.services.runtime_error_payloads import scrub_error_text
from foundry_lite.domain.backup_restore.artifact_restore import (
    artifact_restore_findings as _domain_artifact_restore_findings,
)
from foundry_lite.domain.backup_restore.artifact_restore import (
    latest_versions_by_dataset as _domain_latest_versions_by_dataset,
)
from foundry_lite.domain.backup_restore.artifact_restore import (
    require_artifact_tenant as _domain_require_artifact_tenant,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected, NotFound

ARTIFACT_RESTORE_REQUESTED_EVENT = "backup_restore.artifact_restore_requested"
ARTIFACT_RESTORE_VALIDATED_EVENT = "backup_restore.artifact_restore_validated"
ARTIFACT_RESTORE_FAILED_EVENT = "backup_restore.artifact_restore_failed"


class BackupRestoreArtifactRestoreService(CoreService):
    """Verified backup artifact restore-mode entrypoint."""

    required_dependencies = ("engine", "backup_artifact_store")
    required_collaborators = (
        "backup_restore_preflight_service",
        "backup_restore_validation_service",
        "runtime_service",
    )
    backup_restore_preflight_service: BackupRestorePreflightService
    backup_restore_validation_service: BackupRestoreValidationService
    engine: TransactionManager
    backup_artifact_store: BackupArtifactStore
    runtime_service: DatasetRuntimeBoundary

    def restore_from_backup_artifact(
        self,
        *,
        ctx: RequestContext | None = None,
        artifact_ref: str,
        artifact_hash: str | None = None,
        restore_id: str | None = None,
        validation_id: str | None = None,
        should_run_post_restore_validation: bool = True,
    ) -> BackupRestoreArtifactRestoreReport:
        ctx = ctx or RequestContext()
        self.runtime_service._require_or_audit(ctx, "operations:retry", "backup_restore", artifact_ref)
        artifact = self.read_restore_artifact(ctx, artifact_ref, artifact_hash)
        receipt = artifact["receipt"]
        preflight = artifact["preflightReport"]
        resolved_restore_id = restore_id or f"restore-{preflight['backupId']}"
        self.require_artifact_tenant(ctx, receipt)
        self.audit_artifact_restore_requested(ctx, receipt, resolved_restore_id)
        current = self.backup_restore_preflight_service.restore_preflight_report(
            ctx=ctx,
            backup_id=f"{preflight['backupId']}:current",
        )
        findings = _artifact_restore_findings(preflight, current)
        if findings:
            report = _artifact_restore_report(ctx, artifact, resolved_restore_id, findings, None, None)
            self._audit_artifact_restore_report(ctx, report)
            return report
        mode = self.restore_mode_from_artifact(ctx, resolved_restore_id, preflight)
        validation = self.post_restore_from_artifact(
            ctx,
            mode,
            preflight,
            validation_id,
            should_run_post_restore_validation,
        )
        report = _artifact_restore_report(ctx, artifact, resolved_restore_id, [], mode, validation)
        self._audit_artifact_restore_report(ctx, report)
        return report

    def read_restore_artifact(
        self,
        ctx: RequestContext,
        artifact_ref: str,
        artifact_hash: str | None,
    ) -> BackupRestoreArtifact:
        try:
            return self.backup_artifact_store.read_backup_artifact(artifact_ref, expected_hash=artifact_hash)
        except BackupArtifactNotFoundError as exc:
            self._audit_artifact_restore_error(ctx, artifact_ref, "artifact_not_found", exc)
            raise NotFound("backup artifact not found", details={"artifact_ref": artifact_ref}) from exc
        except BackupArtifactIntegrityError as exc:
            self._audit_artifact_restore_error(ctx, artifact_ref, "artifact_integrity_failed", exc)
            raise ConflictDetected(
                "backup artifact integrity check failed",
                details={"artifact_ref": artifact_ref, "reason": exc.reason},
            ) from exc

    def restore_mode_from_artifact(
        self,
        ctx: RequestContext,
        restore_id: str,
        preflight: BackupRestorePreflightReport,
    ) -> BackupRestoreModeReport:
        existing = self.backup_restore_validation_service.existing_restore_mode_report(ctx, restore_id)
        if existing is not None:
            return existing
        mode = restore_mode_report(ctx, restore_id, preflight)
        with self.engine.begin() as conn:
            self.backup_restore_validation_service.audit_restore_mode(conn, ctx, mode)
        return mode

    def post_restore_from_artifact(
        self,
        ctx: RequestContext,
        mode: BackupRestoreModeReport,
        preflight: BackupRestorePreflightReport,
        validation_id: str | None,
        should_run: bool,
    ) -> BackupRestorePostRestoreValidationReport | None:
        if not should_run:
            return None
        validation = post_restore_validation_report(ctx, mode, preflight, validation_id)
        with self.engine.begin() as conn:
            self.backup_restore_validation_service.audit_post_restore_validation(conn, ctx, validation)
        return validation

    def audit_artifact_restore_requested(
        self,
        ctx: RequestContext,
        receipt: BackupRestoreArtifactReceipt,
        restore_id: str,
    ) -> None:
        with self.engine.begin() as conn:
            self.runtime_service._audit(
                conn,
                ctx,
                event_type=ARTIFACT_RESTORE_REQUESTED_EVENT,
                resource_type="backup_restore",
                resource_id=restore_id,
                action="operations:retry",
                after_ref=_artifact_restore_request_payload(receipt, restore_id),
                correlation_id=ctx.request_id,
            )

    def _audit_artifact_restore_report(self, ctx: RequestContext, report: BackupRestoreArtifactRestoreReport) -> None:
        with self.engine.begin() as conn:
            self.runtime_service._audit(
                conn,
                ctx,
                event_type=_artifact_restore_event_type(report),
                resource_type="backup_restore",
                resource_id=report["restoreId"],
                action="operations:retry",
                decision="allow" if report["status"] == "validated" else "deny",
                after_ref=report,
                correlation_id=ctx.request_id,
            )

    def _audit_artifact_restore_error(
        self,
        ctx: RequestContext,
        artifact_ref: str,
        reason: str,
        exc: Exception,
    ) -> None:
        with self.engine.begin() as conn:
            self.runtime_service._audit(
                conn,
                ctx,
                event_type=ARTIFACT_RESTORE_FAILED_EVENT,
                resource_type="backup_restore",
                resource_id=artifact_ref,
                action="operations:retry",
                decision="deny",
                after_ref={"reason": reason, "error": scrub_error_text(str(exc))},
                correlation_id=ctx.request_id,
            )

    def require_artifact_tenant(self, ctx: RequestContext, receipt: BackupRestoreArtifactReceipt) -> None:
        _domain_require_artifact_tenant(ctx.tenant_id, receipt["tenantId"])


def _artifact_restore_findings(
    artifact_preflight: BackupRestorePreflightReport,
    current_preflight: BackupRestorePreflightReport,
) -> list[RuntimeJsonObject]:
    return [dict(finding) for finding in _domain_artifact_restore_findings(artifact_preflight, current_preflight)]


def _latest_versions_by_dataset(
    versions: Sequence[Mapping[str, object]],
) -> dict[tuple[str, str], Mapping[str, object]]:
    return _domain_latest_versions_by_dataset(versions)


def _artifact_restore_event_type(report: BackupRestoreArtifactRestoreReport) -> str:
    if report["status"] == "validated":
        return ARTIFACT_RESTORE_VALIDATED_EVENT
    return ARTIFACT_RESTORE_FAILED_EVENT


def _artifact_restore_report(
    ctx: RequestContext,
    artifact: BackupRestoreArtifact,
    restore_id: str,
    findings: list[RuntimeJsonObject],
    mode: BackupRestoreModeReport | None,
    validation: BackupRestorePostRestoreValidationReport | None,
) -> BackupRestoreArtifactRestoreReport:
    receipt = artifact["receipt"]
    preflight = artifact["preflightReport"]
    validation_status = validation["status"] if validation is not None else "not_run"
    status = _artifact_restore_status(findings, mode, validation)
    return {
        "generatedAt": _now(),
        "tenantId": ctx.tenant_id,
        "backupId": preflight["backupId"],
        "restoreId": restore_id,
        "status": status,
        "artifactRef": receipt["artifactRef"],
        "artifactHash": receipt["artifactHash"],
        "manifestFormat": artifact["manifestFormat"],
        "executionMode": "verified_current_commit_point_restore_mode",
        "isArtifactVerified": True,
        "isHistoricalRepointExecuted": False,
        "preflightStatus": preflight["status"],
        "restoreModeStatus": mode["status"] if mode is not None else "not_started",
        "postRestoreValidationStatus": validation_status,
        "datasetVersionCount": len(preflight["datasetVersions"]),
        "blockingIssueCount": len(findings),
        "findings": findings,
        "restoreMode": mode,
        "postRestoreValidation": validation,
        "restoredDatasetVersions": [],
        "restoreTransactionCount": 0,
        "summary": _artifact_restore_summary(status, findings, mode, validation),
    }


def _artifact_restore_status(
    findings: list[RuntimeJsonObject],
    mode: BackupRestoreModeReport | None,
    validation: BackupRestorePostRestoreValidationReport | None,
) -> BackupRestoreArtifactRestoreStatus:
    if findings or mode is None or mode["status"] == "blocked":
        return "blocked"
    if validation is not None and validation["status"] == "blocked":
        return "blocked"
    return "validated"


def _artifact_restore_summary(
    status: BackupRestoreArtifactRestoreStatus,
    findings: list[RuntimeJsonObject],
    mode: BackupRestoreModeReport | None,
    validation: BackupRestorePostRestoreValidationReport | None,
) -> RuntimeJsonObject:
    return {
        "artifactMatchesCurrentServingHead": len(findings) == 0,
        "restoreModeStarted": mode is not None,
        "postRestoreValidationRan": validation is not None,
        "pointInTimeRepointExecuted": False,
        "nextStep": _artifact_restore_next_step(status, validation),
    }


def _artifact_restore_next_step(
    status: BackupRestoreArtifactRestoreStatus,
    validation: BackupRestorePostRestoreValidationReport | None,
) -> str:
    if status == "validated" and validation is not None and validation["status"] == "passed":
        return "approve_restore_resume"
    if status == "validated":
        return "run_post_restore_validation"
    return "resolve_restore_findings_before_retry"


def _artifact_restore_request_payload(
    receipt: BackupRestoreArtifactReceipt,
    restore_id: str,
) -> RuntimeJsonObject:
    return {
        "restoreId": restore_id,
        "backupId": receipt["backupId"],
        "artifactRef": receipt["artifactRef"],
        "artifactHash": receipt["artifactHash"],
        "manifestFormat": receipt["manifestFormat"],
    }
