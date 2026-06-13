from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from foundry_lite.application.ports import (
    DatasetCheckConfig,
    DatasetCheckResult,
    DatasetFileRecord,
    DatasetManifest,
    DatasetRow,
    DatasetRunKind,
    DatasetTransactionRecord,
    DatasetTransactionRow,
    DatasetVersionConflictError,
    DatasetVersionRecord,
    DatasetVersionRow,
    TransactionContext,
)
from foundry_lite.application.primitives import (
    CommitResult,
    _new_id,
    _now,
)
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.dataset.protocols import (
    DatasetQualityBoundary,
    DatasetRuntimeBoundary,
    DatasetVersionLookup,
)
from foundry_lite.application.services.dataset.transaction_models import (
    DatasetCommitArtifacts,
    DatasetCommitTarget,
    DatasetFinalizationCheck,
    DatasetFinalizationRequest,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import (
    ConflictDetected,
    InvariantViolation,
    NotFound,
    ValidationFailed,
)


class DatasetTransactionService(CoreService):
    required_dependencies = ("engine", "dataset_storage", "dataset_transaction_repository")
    required_collaborators = (
        "dataset_quality_service",
        "dataset_version_service",
        "runtime_service",
    )
    dataset_quality_service: DatasetQualityBoundary
    dataset_version_service: DatasetVersionLookup
    runtime_service: DatasetRuntimeBoundary

    def _open_dataset_transaction(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        dataset: DatasetRow,
        tx_type: str,
        *,
        branch: str = "main",
    ) -> str:
        if tx_type not in {"SNAPSHOT", "APPEND"}:
            raise ValidationFailed("v1 supports only SNAPSHOT and APPEND transactions")
        tx_id = _new_id("dstx")
        latest = self.dataset_version_service._latest_version_by_dataset_id(conn, dataset["id"], allow_missing=True)
        self.dataset_transaction_repository.create_open_transaction(
            transaction=conn,
            record=DatasetTransactionRecord(
                transaction_id=tx_id,
                tenant_id=ctx.tenant_id,
                dataset_id=dataset["id"],
                branch=branch,
                tx_type=tx_type,
                status="OPEN",
                base_version_id=latest["id"] if latest else None,
                committed_version_id=None,
                schema_version=None,
                created_by=ctx.actor_user_id,
                created_at=_now(),
                committed_at=None,
                metadata={},
            ),
        )
        return tx_id

    def _staging_file(self, dataset: DatasetRow, transaction_id: str, file_name: str) -> Path:
        return self.dataset_storage.staging_file(
            tenant_id=dataset["tenant_id"],
            dataset_id=dataset["id"],
            transaction_id=transaction_id,
            file_name=file_name,
        )

    def _version_file_path(self, version: DatasetVersionRow) -> Path:
        return self.dataset_storage.first_data_file_path(version["manifest_uri"])

    def _load_manifest(self, manifest_uri: str) -> DatasetManifest:
        return self.dataset_storage.load_manifest(manifest_uri)

    def _finalize_open_transaction(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        *,
        dataset: DatasetRow,
        transaction_id: str,
        staged_parquet: Path,
        run_id: str,
        audit_action: str,
        outbox_event_type: str,
        extra_checks: Sequence[DatasetCheckConfig] | None = None,
    ) -> CommitResult:
        request = DatasetFinalizationRequest(
            target=DatasetCommitTarget.from_row(dataset),
            transaction_id=transaction_id,
            staged_parquet=staged_parquet,
            run_id=run_id,
            audit_action=audit_action,
            outbox_event_type=outbox_event_type,
            extra_checks=list(extra_checks or []),
        )
        validation = self._validate_finalization_request(conn, ctx, request)
        commit = self._store_finalized_dataset_commit(conn, ctx, request, validation)
        self._persist_with_orphan_cleanup(conn, ctx, request, validation, commit)
        self._emit_dataset_commit_events(conn, ctx, request, validation, commit)
        return self._commit_result(request, validation, commit)

    def _validate_finalization_request(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        request: DatasetFinalizationRequest,
    ) -> DatasetFinalizationCheck:
        tx = self._require_open_transaction(conn, request.transaction_id)
        stats = self.dataset_quality_service._inspect_parquet(
            request.staged_parquet,
            request.target.primary_key,
        )
        failures = self.dataset_quality_service._run_dataset_checks(
            conn,
            ctx,
            request.target.row,
            stats.parquet_path,
            stats.row_count,
            request.run_id,
            request.transaction_id,
            extra_checks=request.extra_checks,
        )
        compatibility_error = self.dataset_quality_service._schema_compatibility_error(
            conn,
            request.target.row,
            stats.schema_json,
        )
        if compatibility_error:
            failures.append(compatibility_error)
        if failures:
            self._abort_finalization_with_failures(conn, ctx, request.transaction_id, failures)
        return DatasetFinalizationCheck(branch=str(tx["branch"]), stats=stats)

    def _store_finalized_dataset_commit(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        request: DatasetFinalizationRequest,
        validation: DatasetFinalizationCheck,
    ) -> DatasetCommitArtifacts:
        schema_version = self.dataset_quality_service._ensure_schema(
            conn,
            request.target.row,
            validation.stats.schema_json,
            validation.stats.schema_hash,
        )
        self.dataset_transaction_repository.lock_dataset_for_version_allocation(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            dataset_id=request.target.dataset_id,
        )
        version_number = self.dataset_version_service._next_dataset_version_number(
            conn,
            request.target.dataset_id,
        )
        version_id = _new_id("dsv")
        stored_commit = self.dataset_storage.commit_staged_file(
            tenant_id=ctx.tenant_id,
            dataset_id=request.target.dataset_id,
            branch=validation.branch,
            version_id=version_id,
            dataset_ref=request.target.dataset_ref,
            schema_hash=validation.stats.schema_hash,
            staged_file=request.staged_parquet,
            row_count=validation.stats.row_count,
            created_at=_now(),
        )
        return DatasetCommitArtifacts(
            version_id=version_id,
            version_number=version_number,
            schema_version=schema_version,
            stored_commit=stored_commit,
        )

    def _persist_with_orphan_cleanup(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        request: DatasetFinalizationRequest,
        validation: DatasetFinalizationCheck,
        commit: DatasetCommitArtifacts,
    ) -> None:
        try:
            self._persist_finalized_dataset_version(conn, ctx, request, validation, commit)
        except DatasetVersionConflictError as exc:
            cleanup = self._cleanup_committed_version_artifacts(ctx, request, validation, commit)
            raise self._version_conflict_error(request, validation, commit, cleanup) from exc
        except Exception as exc:
            cleanup = self._cleanup_committed_version_artifacts(ctx, request, validation, commit)
            raise self._commit_persistence_error(request, validation, commit, cleanup) from exc

    def _persist_finalized_dataset_version(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        request: DatasetFinalizationRequest,
        validation: DatasetFinalizationCheck,
        commit: DatasetCommitArtifacts,
    ) -> None:
        self.dataset_transaction_repository.insert_version(
            transaction=conn,
            record=DatasetVersionRecord(
                version_id=commit.version_id,
                tenant_id=ctx.tenant_id,
                dataset_id=request.target.dataset_id,
                branch=validation.branch,
                version_number=commit.version_number,
                transaction_id=request.transaction_id,
                schema_version=commit.schema_version,
                manifest_uri=commit.stored_commit.manifest_uri,
                row_count=validation.stats.row_count,
                byte_size=commit.stored_commit.byte_size,
                status="active",
                superseded_by_version_id=None,
                created_at=_now(),
            ),
        )
        self.dataset_transaction_repository.insert_file(
            transaction=conn,
            record=self._dataset_file_record(ctx, validation, commit),
        )
        self._commit_transaction_row(
            conn,
            ctx,
            request.transaction_id,
            commit.version_id,
            commit.schema_version,
        )

    def _cleanup_committed_version_artifacts(
        self,
        ctx: RequestContext,
        request: DatasetFinalizationRequest,
        validation: DatasetFinalizationCheck,
        commit: DatasetCommitArtifacts,
    ) -> dict[str, object]:
        removed = self.dataset_storage.delete_committed_version(
            tenant_id=ctx.tenant_id,
            dataset_id=request.target.dataset_id,
            branch=validation.branch,
            version_id=commit.version_id,
        )
        return {
            "version_id": commit.version_id,
            "manifest_uri": commit.stored_commit.manifest_uri,
            "removed": removed,
        }

    def _version_conflict_error(
        self,
        request: DatasetFinalizationRequest,
        validation: DatasetFinalizationCheck,
        commit: DatasetCommitArtifacts,
        cleanup: dict[str, object],
    ) -> ConflictDetected:
        return ConflictDetected(
            "dataset version allocation conflict",
            details={
                "dataset_id": request.target.dataset_id,
                "transaction_id": request.transaction_id,
                "branch": validation.branch,
                "version_number": commit.version_number,
                "orphan_cleanup": cleanup,
            },
        )

    def _commit_persistence_error(
        self,
        request: DatasetFinalizationRequest,
        validation: DatasetFinalizationCheck,
        commit: DatasetCommitArtifacts,
        cleanup: dict[str, object],
    ) -> InvariantViolation:
        return InvariantViolation(
            "dataset commit metadata persistence failed",
            details={
                "dataset_id": request.target.dataset_id,
                "transaction_id": request.transaction_id,
                "branch": validation.branch,
                "version_number": commit.version_number,
                "orphan_cleanup": cleanup,
            },
        )

    def _dataset_file_record(
        self,
        ctx: RequestContext,
        validation: DatasetFinalizationCheck,
        commit: DatasetCommitArtifacts,
    ) -> DatasetFileRecord:
        return DatasetFileRecord(
            file_id=_new_id("dsf"),
            tenant_id=ctx.tenant_id,
            dataset_version_id=commit.version_id,
            uri=commit.stored_commit.data_file_uri,
            file_format="parquet",
            row_count=validation.stats.row_count,
            byte_size=commit.stored_commit.byte_size,
            content_hash=commit.stored_commit.content_hash,
            partition_values={},
        )

    def _emit_dataset_commit_events(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        request: DatasetFinalizationRequest,
        validation: DatasetFinalizationCheck,
        commit: DatasetCommitArtifacts,
    ) -> None:
        self.runtime_service._outbox(
            conn,
            ctx,
            request.outbox_event_type,
            "dataset_version",
            commit.version_id,
            {
                "datasetId": request.target.dataset_id,
                "datasetRef": request.target.dataset_ref,
                "versionId": commit.version_id,
                "branch": validation.branch,
            },
            idempotency_key=commit.version_id,
            correlation_id=request.run_id,
        )
        self.runtime_service._audit(
            conn,
            ctx,
            event_type="dataset.version.committed",
            resource_type="dataset_version",
            resource_id=commit.version_id,
            action=request.audit_action,
            after_ref={
                "dataset_ref": request.target.dataset_ref,
                "version_number": commit.version_number,
            },
            correlation_id=request.run_id,
        )

    def _commit_result(
        self,
        request: DatasetFinalizationRequest,
        validation: DatasetFinalizationCheck,
        commit: DatasetCommitArtifacts,
    ) -> CommitResult:
        return CommitResult(
            dataset_id=request.target.dataset_id,
            dataset_ref=request.target.dataset_ref,
            transaction_id=request.transaction_id,
            version_id=commit.version_id,
            version_number=commit.version_number,
            row_count=validation.stats.row_count,
            manifest_uri=commit.stored_commit.manifest_uri,
            schema_hash=validation.stats.schema_hash,
        )

    def _abort_finalization_with_failures(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        transaction_id: str,
        failures: Sequence[DatasetCheckResult],
    ) -> None:
        """Abort an open transaction and record validation failures for audit."""
        failure_list = [dict(failure) for failure in failures]
        self.dataset_transaction_repository.abort_transaction(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            transaction_id=transaction_id,
            metadata={"validationFailures": failure_list},
        )
        self.runtime_service._audit(
            conn,
            ctx,
            event_type="dataset.transaction.aborted",
            resource_type="dataset_transaction",
            resource_id=transaction_id,
            action="abort",
            after_ref={"failures": failure_list},
        )
        raise ValidationFailed("dataset checks failed", details={"failures": failure_list})

    def _commit_transaction_row(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        transaction_id: str,
        version_id: str,
        schema_version: int,
    ) -> None:
        """Mark the transaction committed after the version and files are stored."""
        self.dataset_transaction_repository.commit_transaction(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            transaction_id=transaction_id,
            committed_version_id=version_id,
            schema_version=schema_version,
            committed_at=_now(),
        )

    def _require_open_transaction(self, conn: TransactionContext, transaction_id: str) -> DatasetTransactionRow:
        tx = self.dataset_transaction_repository.transaction_by_id(transaction=conn, transaction_id=transaction_id)
        if tx is None:
            raise NotFound("dataset transaction not found", details={"transaction_id": transaction_id})
        if tx["status"] != "OPEN":
            raise ConflictDetected(
                "dataset transaction is not open",
                details={"transaction_id": transaction_id, "status": tx["status"]},
            )
        return tx

    def _abort_transaction_after_error(
        self,
        ctx: RequestContext,
        transaction_id: str,
        run_id: str,
        exc: Exception,
        run_kind: DatasetRunKind,
        adapter: str | None = None,
    ) -> None:
        error_payload = self.runtime_service._error_payload(
            exc,
            ctx,
            run_id=run_id,
            correlation_id=run_id,
            adapter=adapter,
        )
        with self.engine.begin() as conn:
            aborted = self.dataset_transaction_repository.abort_open_transaction_and_fail_run(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                transaction_id=transaction_id,
                run_id=run_id,
                run_kind=run_kind,
                error=error_payload,
                completed_at=_now(),
            )
            if aborted:
                self.runtime_service._audit(
                    conn,
                    ctx,
                    event_type="dataset.transaction.aborted",
                    resource_type="dataset_transaction",
                    resource_id=transaction_id,
                    action="abort",
                    after_ref={"error": error_payload},
                    correlation_id=run_id,
                )
