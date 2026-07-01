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
    TransactionContext,
)
from foundry_lite.application.ports.transaction_context import TransactionManager
from foundry_lite.application.primitives import _now
from foundry_lite.application.services.backup_restore_validation import (
    post_restore_validation_report,
    restore_mode_report,
)
from foundry_lite.application.services.dataset.protocols import DatasetRuntimeBoundary
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected, NotFound

ARTIFACT_RESTORE_REQUESTED_EVENT = "backup_restore.artifact_restore_requested"
ARTIFACT_RESTORE_VALIDATED_EVENT = "backup_restore.artifact_restore_validated"
ARTIFACT_RESTORE_FAILED_EVENT = "backup_restore.artifact_restore_failed"


class BackupRestoreArtifactRestoreMixin:
    """Verified backup artifact restore-mode entrypoint."""

    engine: TransactionManager
    backup_artifact_store: BackupArtifactStore
    runtime_service: DatasetRuntimeBoundary

    def restore_preflight_report(
        self,
        *,
        ctx: RequestContext | None = None,
        backup_id: str | None = None,
    ) -> BackupRestorePreflightReport:
        raise NotImplementedError

    def _existing_restore_mode_report(self, ctx: RequestContext, restore_id: str) -> BackupRestoreModeReport | None:
        raise NotImplementedError

    def _audit_restore_mode(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        report: BackupRestoreModeReport,
    ) -> None:
        raise NotImplementedError

    def _audit_post_restore_validation(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        report: BackupRestorePostRestoreValidationReport,
    ) -> None:
        raise NotImplementedError

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
        artifact = self._read_restore_artifact(ctx, artifact_ref, artifact_hash)
        receipt = artifact["receipt"]
        preflight = artifact["preflightReport"]
        resolved_restore_id = restore_id or f"restore-{preflight['backupId']}"
        self._require_artifact_tenant(ctx, receipt)
        self._audit_artifact_restore_requested(ctx, receipt, resolved_restore_id)
        current = self.restore_preflight_report(ctx=ctx, backup_id=f"{preflight['backupId']}:current")
        findings = _artifact_restore_findings(preflight, current)
        if findings:
            report = _artifact_restore_report(ctx, artifact, resolved_restore_id, findings, None, None)
            self._audit_artifact_restore_report(ctx, report)
            return report
        mode = self._restore_mode_from_artifact(ctx, resolved_restore_id, preflight)
        validation = self._post_restore_from_artifact(
            ctx,
            mode,
            preflight,
            validation_id,
            should_run_post_restore_validation,
        )
        report = _artifact_restore_report(ctx, artifact, resolved_restore_id, [], mode, validation)
        self._audit_artifact_restore_report(ctx, report)
        return report

    def _read_restore_artifact(
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

    def _restore_mode_from_artifact(
        self,
        ctx: RequestContext,
        restore_id: str,
        preflight: BackupRestorePreflightReport,
    ) -> BackupRestoreModeReport:
        existing = self._existing_restore_mode_report(ctx, restore_id)
        if existing is not None:
            return existing
        mode = restore_mode_report(ctx, restore_id, preflight)
        with self.engine.begin() as conn:
            self._audit_restore_mode(conn, ctx, mode)
        return mode

    def _post_restore_from_artifact(
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
            self._audit_post_restore_validation(conn, ctx, validation)
        return validation

    def _audit_artifact_restore_requested(
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
                after_ref={"reason": reason, "error": str(exc)},
                correlation_id=ctx.request_id,
            )

    def _require_artifact_tenant(self, ctx: RequestContext, receipt: BackupRestoreArtifactReceipt) -> None:
        if receipt["tenantId"] == ctx.tenant_id:
            return
        raise ConflictDetected(
            "backup artifact tenant does not match request context",
            details={"artifact_tenant_id": receipt["tenantId"], "request_tenant_id": ctx.tenant_id},
        )


def _artifact_restore_findings(
    artifact_preflight: BackupRestorePreflightReport,
    current_preflight: BackupRestorePreflightReport,
) -> list[RuntimeJsonObject]:
    findings: list[RuntimeJsonObject] = []
    if artifact_preflight["status"] != "ready":
        findings.append({"check": "artifact_preflight", "status": artifact_preflight["status"]})
    if current_preflight["status"] != "ready":
        findings.append({"check": "current_preflight", "status": current_preflight["status"]})
    findings.extend(_artifact_version_findings(artifact_preflight, current_preflight))
    return findings


def _artifact_version_findings(
    artifact_preflight: BackupRestorePreflightReport,
    current_preflight: BackupRestorePreflightReport,
) -> list[RuntimeJsonObject]:
    current_by_version = {version["versionId"]: version for version in current_preflight["datasetVersions"]}
    latest_by_dataset = _latest_versions_by_dataset(current_preflight["datasetVersions"])
    findings: list[RuntimeJsonObject] = []
    for artifact_version in artifact_preflight["datasetVersions"]:
        current_version = current_by_version.get(artifact_version["versionId"])
        findings.extend(_version_mismatch_findings(artifact_version, current_version))
        latest = latest_by_dataset.get((artifact_version["datasetId"], artifact_version["branch"]))
        if latest is not None and latest["versionId"] != artifact_version["versionId"]:
            findings.append(_serving_head_finding(artifact_version, latest))
    return findings


def _latest_versions_by_dataset(
    versions: Sequence[Mapping[str, object]],
) -> dict[tuple[str, str], Mapping[str, object]]:
    latest: dict[tuple[str, str], Mapping[str, object]] = {}
    for version in versions:
        key = (str(version["datasetId"]), str(version["branch"]))
        current = latest.get(key)
        if current is None or _version_number(version) > _version_number(current):
            latest[key] = version
    return latest


def _version_mismatch_findings(
    artifact_version: Mapping[str, object],
    current_version: Mapping[str, object] | None,
) -> list[RuntimeJsonObject]:
    if current_version is None:
        return [
            {
                "check": "artifact_dataset_version_present",
                "status": "missing",
                "versionId": artifact_version["versionId"],
            }
        ]
    mismatches = _mismatched_version_fields(artifact_version, current_version)
    if not mismatches:
        return []
    return [{"check": "artifact_dataset_version_integrity", "status": "mismatch", "fields": mismatches}]


def _mismatched_version_fields(
    artifact_version: Mapping[str, object],
    current_version: Mapping[str, object],
) -> list[str]:
    fields = ("datasetId", "manifestUri", "rowCount", "byteSize", "manifestContentHashes", "status")
    return [field for field in fields if artifact_version.get(field) != current_version.get(field)]


def _artifact_restore_event_type(report: BackupRestoreArtifactRestoreReport) -> str:
    if report["status"] == "validated":
        return ARTIFACT_RESTORE_VALIDATED_EVENT
    return ARTIFACT_RESTORE_FAILED_EVENT


def _version_number(version: Mapping[str, object]) -> int:
    value = version["versionNumber"]
    return int(value) if isinstance(value, int | str) else 0


def _serving_head_finding(
    artifact_version: Mapping[str, object],
    latest: Mapping[str, object],
) -> RuntimeJsonObject:
    return {
        "check": "serving_head_matches_artifact",
        "status": "blocked",
        "datasetId": artifact_version["datasetId"],
        "artifactVersionId": artifact_version["versionId"],
        "currentHeadVersionId": latest["versionId"],
    }


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
