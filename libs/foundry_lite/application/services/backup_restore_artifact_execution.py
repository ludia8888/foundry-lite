"""Application service helpers for backup restore artifact execution workflows."""

from __future__ import annotations

from collections.abc import Sequence

from foundry_lite.application.ports import (
    AdapterError,
    BackupRestoreArtifact,
    BackupRestoreArtifactRestoredVersion,
    BackupRestoreArtifactRestoreReport,
    BackupRestorePreflightReport,
    DatasetRepository,
    DatasetRow,
    DatasetStorageAdapter,
    DatasetTransactionRecord,
    DatasetTransactionRepository,
    DatasetVersionRepository,
    DatasetVersionRow,
    RuntimeRepository,
    TransactionContext,
)
from foundry_lite.application.ports.transaction_context import TransactionManager
from foundry_lite.application.primitives import _new_id, _now
from foundry_lite.application.services.backup_restore_artifact_execution_helpers import (
    CleanupSpec,
    RestoreCandidate,
    RestoreSourceFiles,
    already_current_result,
    blocking_restore_findings,
    commit_restore_transaction,
    dataset_version_record,
    execution_event_type,
    execution_report,
    existing_execution_report,
    require_same_artifact,
    required_source_version,
    restore_candidates,
    restored_event_payload,
    restored_result,
    staged_files,
    validate_source_files,
)
from foundry_lite.application.services.backup_restore_artifact_restore import (
    BackupRestoreArtifactRestoreService,
    _artifact_restore_findings,
)
from foundry_lite.application.services.backup_restore_preflight_service import BackupRestorePreflightService
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.dataset.protocols import DatasetRuntimeBoundary
from foundry_lite.application.services.dataset.transaction_models import DatasetCommitArtifacts
from foundry_lite.application.services.dataset.transaction_payloads import (
    build_dataset_file_records,
    commit_staged_dataset_files,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected


class BackupRestoreArtifactExecutionService(CoreService):
    """Execute a verified artifact restore by committing new dataset heads."""

    required_dependencies = (
        "engine",
        "dataset_repository",
        "dataset_storage",
        "dataset_transaction_repository",
        "dataset_version_repository",
        "runtime_repository",
    )
    required_collaborators = (
        "backup_restore_artifact_restore_service",
        "backup_restore_preflight_service",
        "runtime_service",
    )
    backup_restore_artifact_restore_service: BackupRestoreArtifactRestoreService
    backup_restore_preflight_service: BackupRestorePreflightService
    engine: TransactionManager
    dataset_repository: DatasetRepository
    dataset_storage: DatasetStorageAdapter
    dataset_transaction_repository: DatasetTransactionRepository
    dataset_version_repository: DatasetVersionRepository
    runtime_repository: RuntimeRepository
    runtime_service: DatasetRuntimeBoundary

    def execute_backup_artifact_restore(
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
        artifact = self.backup_restore_artifact_restore_service.read_restore_artifact(ctx, artifact_ref, artifact_hash)
        receipt = artifact["receipt"]
        preflight = artifact["preflightReport"]
        resolved_restore_id = restore_id or f"restore-{preflight['backupId']}"
        self.backup_restore_artifact_restore_service.require_artifact_tenant(ctx, receipt)
        existing = existing_execution_report(self.runtime_repository, ctx, resolved_restore_id)
        if existing is not None:
            return require_same_artifact(existing, receipt)
        self.backup_restore_artifact_restore_service.audit_artifact_restore_requested(ctx, receipt, resolved_restore_id)
        return self._execute_verified_artifact_restore(
            ctx,
            artifact,
            resolved_restore_id,
            validation_id,
            should_run_post_restore_validation,
        )

    def _execute_verified_artifact_restore(
        self,
        ctx: RequestContext,
        artifact: BackupRestoreArtifact,
        restore_id: str,
        validation_id: str | None,
        should_run_post_restore_validation: bool,
    ) -> BackupRestoreArtifactRestoreReport:
        current = self.backup_restore_preflight_service.restore_preflight_report(
            ctx=ctx,
            backup_id=f"{artifact['preflightReport']['backupId']}:current",
        )
        findings = _artifact_restore_findings(artifact["preflightReport"], current)
        blocking = blocking_restore_findings(findings)
        if blocking:
            report = execution_report(ctx, artifact, restore_id, blocking, None, None, [])
            self._audit_execution_report(ctx, report)
            return report
        mode = self.backup_restore_artifact_restore_service.restore_mode_from_artifact(
            ctx,
            restore_id,
            artifact["preflightReport"],
        )
        restored = self._restore_dataset_heads(ctx, artifact, current, restore_id)
        post_restore = self.backup_restore_preflight_service.restore_preflight_report(
            ctx=ctx,
            backup_id=f"{artifact['preflightReport']['backupId']}:restored",
        )
        validation = self.backup_restore_artifact_restore_service.post_restore_from_artifact(
            ctx,
            mode,
            post_restore,
            validation_id,
            should_run_post_restore_validation,
        )
        report = execution_report(ctx, artifact, restore_id, [], mode, validation, restored)
        self._audit_execution_report(ctx, report)
        return report

    def _restore_dataset_heads(
        self,
        ctx: RequestContext,
        artifact: BackupRestoreArtifact,
        current: BackupRestorePreflightReport,
        restore_id: str,
    ) -> list[BackupRestoreArtifactRestoredVersion]:
        candidates = restore_candidates(artifact["preflightReport"], current, self._dataset_map(ctx))
        cleanup_specs: list[CleanupSpec] = []
        try:
            with self.engine.begin() as conn:
                return [
                    self._restore_candidate(conn, ctx, artifact, candidate, restore_id, cleanup_specs)
                    for candidate in candidates
                ]
        except Exception:
            self._cleanup_restored_storage(ctx, cleanup_specs)
            raise

    def _restore_candidate(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        artifact: BackupRestoreArtifact,
        candidate: RestoreCandidate,
        restore_id: str,
        cleanup_specs: list[CleanupSpec],
    ) -> BackupRestoreArtifactRestoredVersion:
        source = required_source_version(self.dataset_version_repository, conn, ctx, candidate)
        current = self._current_head_for_restore(conn, ctx, candidate.dataset, source)
        if current["id"] == source["id"]:
            return already_current_result(candidate, source)
        source_files = self._validated_source_files(source)
        return self._commit_restored_version(
            conn,
            ctx,
            artifact,
            candidate,
            source,
            current,
            source_files,
            restore_id,
            cleanup_specs,
        )

    def _current_head_for_restore(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        dataset: DatasetRow,
        source: DatasetVersionRow,
    ) -> DatasetVersionRow:
        self.dataset_transaction_repository.lock_dataset_for_version_allocation(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            dataset_id=dataset["id"],
        )
        current = self.dataset_version_repository.latest_version_by_dataset_id(
            transaction=conn,
            dataset_id=dataset["id"],
        )
        if current is None or current["tenant_id"] != ctx.tenant_id:
            raise ConflictDetected(
                "restore target dataset has no current committed version",
                details={"dataset_id": dataset["id"]},
            )
        if current["branch"] != source["branch"]:
            raise ConflictDetected(
                "restore target branch changed during restore",
                details={"dataset_id": dataset["id"]},
            )
        return current

    def _commit_restored_version(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        artifact: BackupRestoreArtifact,
        candidate: RestoreCandidate,
        source: DatasetVersionRow,
        current: DatasetVersionRow,
        source_files: RestoreSourceFiles,
        restore_id: str,
        cleanup_specs: list[CleanupSpec],
    ) -> BackupRestoreArtifactRestoredVersion:
        tx_id = _new_id("dstx")
        version_id = _new_id("dsv")
        version_number = self.dataset_version_repository.next_version_number(
            transaction=conn,
            dataset_id=candidate.dataset["id"],
        )
        cleanup_specs.append(CleanupSpec(candidate.dataset["id"], source["branch"], version_id))
        self._open_restore_transaction(conn, ctx, candidate.dataset, source["branch"], tx_id, current["id"])
        commit = self._copy_source_files(ctx, candidate.dataset, source, source_files, version_id, version_number)
        self._persist_restored_version(conn, ctx, artifact, candidate, source, current, tx_id, commit, restore_id)
        return restored_result(candidate, source, current, tx_id, commit)

    def _open_restore_transaction(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        dataset: DatasetRow,
        branch: str,
        tx_id: str,
        current_version_id: str,
    ) -> None:
        self.dataset_transaction_repository.create_open_transaction(
            transaction=conn,
            record=DatasetTransactionRecord(
                transaction_id=tx_id,
                tenant_id=ctx.tenant_id,
                dataset_id=dataset["id"],
                branch=branch,
                tx_type="SNAPSHOT",
                status="OPEN",
                base_version_id=current_version_id,
                committed_version_id=None,
                schema_version=None,
                created_by=ctx.actor_user_id,
                created_at=_now(),
                committed_at=None,
                metadata={"backupRestore": {"state": "restoring"}},
            ),
        )

    def _copy_source_files(
        self,
        ctx: RequestContext,
        dataset: DatasetRow,
        source: DatasetVersionRow,
        source_files: RestoreSourceFiles,
        version_id: str,
        version_number: int,
    ) -> DatasetCommitArtifacts:
        stored = commit_staged_dataset_files(
            self.dataset_storage,
            tenant_id=ctx.tenant_id,
            dataset_id=dataset["id"],
            branch=source["branch"],
            version_id=version_id,
            dataset_ref=f"{dataset['namespace']}.{dataset['name']}",
            schema_hash=str(source_files.manifest["schema_hash"]),
            staged_files=staged_files(source_files),
            row_count=source["row_count"],
            created_at=_now(),
            sort_order=dataset["sort_order"],
        )
        return DatasetCommitArtifacts(version_id, version_number, source["schema_version"], stored)

    def _persist_restored_version(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        artifact: BackupRestoreArtifact,
        candidate: RestoreCandidate,
        source: DatasetVersionRow,
        current: DatasetVersionRow,
        tx_id: str,
        commit: DatasetCommitArtifacts,
        restore_id: str,
    ) -> None:
        self.dataset_transaction_repository.insert_version(
            transaction=conn,
            record=dataset_version_record(ctx, candidate.dataset["id"], source, tx_id, commit),
        )
        for record in build_dataset_file_records(ctx, commit):
            self.dataset_transaction_repository.insert_file(transaction=conn, record=record)
        commit_restore_transaction(
            self.dataset_transaction_repository,
            conn,
            ctx,
            tx_id,
            source,
            current,
            artifact,
            commit.version_id,
        )
        self._emit_restored_version_evidence(conn, ctx, artifact, candidate, source, current, tx_id, commit, restore_id)

    def _emit_restored_version_evidence(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        artifact: BackupRestoreArtifact,
        candidate: RestoreCandidate,
        source: DatasetVersionRow,
        current: DatasetVersionRow,
        tx_id: str,
        commit: DatasetCommitArtifacts,
        restore_id: str,
    ) -> None:
        payload = restored_event_payload(artifact, candidate, source, current, tx_id, commit)
        self.runtime_service._outbox(
            conn,
            ctx,
            "dataset.version.restored",
            "dataset_version",
            commit.version_id,
            payload,
            idempotency_key=commit.version_id,
            correlation_id=restore_id,
        )
        self.runtime_service._audit(
            conn,
            ctx,
            event_type="backup_restore.dataset_version_restored",
            resource_type="dataset_version",
            resource_id=commit.version_id,
            action="operations:retry",
            after_ref=payload,
            correlation_id=restore_id,
        )

    def _validated_source_files(self, source: DatasetVersionRow) -> RestoreSourceFiles:
        try:
            manifest = self.dataset_storage.load_manifest(source["manifest_uri"])
            files = tuple(manifest.get("files") or [])
            data_paths = tuple(self.dataset_storage.data_file_paths(source["manifest_uri"]))
            validate_source_files(source, files, data_paths)
            return RestoreSourceFiles(manifest, data_paths, files)
        except (AdapterError, FileNotFoundError, OSError, ValueError, KeyError, TypeError) as exc:
            raise ConflictDetected(
                "backup artifact source version storage is not restorable",
                details={"version_id": source["id"], "manifest_uri": source["manifest_uri"], "reason": str(exc)},
            ) from exc

    def _cleanup_restored_storage(self, ctx: RequestContext, cleanup_specs: Sequence[CleanupSpec]) -> None:
        for spec in cleanup_specs:
            self.dataset_storage.delete_committed_version(
                tenant_id=ctx.tenant_id,
                dataset_id=spec.dataset_id,
                branch=spec.branch,
                version_id=spec.version_id,
            )

    def _dataset_map(self, ctx: RequestContext) -> dict[str, DatasetRow]:
        return {
            dataset["id"]: dataset for dataset in self.dataset_repository.list_active_datasets(tenant_id=ctx.tenant_id)
        }

    def _audit_execution_report(self, ctx: RequestContext, report: BackupRestoreArtifactRestoreReport) -> None:
        with self.engine.begin() as conn:
            self.runtime_service._audit(
                conn,
                ctx,
                event_type=execution_event_type(report),
                resource_type="backup_restore",
                resource_id=report["restoreId"],
                action="operations:retry",
                decision="allow" if report["status"] == "validated" else "deny",
                after_ref=report,
                correlation_id=ctx.request_id,
            )
