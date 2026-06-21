from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path

from foundry_lite.application.ports import (
    DatasetCheckConfig,
    DatasetCheckResult,
    DatasetManifest,
    DatasetRow,
    DatasetRunKind,
    DatasetSchemaReference,
    DatasetTransactionMetadata,
    DatasetTransactionRecord,
    DatasetTransactionRow,
    DatasetVersionConflictError,
    DatasetVersionRecord,
    DatasetVersionRow,
    TransactionContext,
)
from foundry_lite.application.primitives import (
    CommitResult,
    StagedFileStats,
    _new_id,
    _now,
)
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.dataset.protocols import (
    DatasetQualityBoundary,
    DatasetRuntimeBoundary,
    DatasetVersionLookup,
)
from foundry_lite.application.services.dataset.schema_evolution import DatasetSchemaEvolutionResult
from foundry_lite.application.services.dataset.storage_consistency import (
    cleanup_staging_transaction,
    committed_manifest,
    committed_version_file_path,
)
from foundry_lite.application.services.dataset.transaction_models import (
    DatasetCommitArtifacts,
    DatasetCommitTarget,
    DatasetFinalizationCheck,
    DatasetFinalizationRequest,
)
from foundry_lite.application.services.dataset.transaction_payloads import (
    block_commit_failure,
    build_commit_result,
    build_dataset_file_record,
    checked_manifest_hash,
    commit_open_dataset_transaction,
    current_candidate_manifest_hash,
    dataset_commit_after_ref,
    dataset_commit_persistence_error,
    dataset_version_conflict_error,
    ensure_snapshot_base_current,
    finalized_transaction_metadata,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected, DatasetCommitBlocked, NotFound, ValidationFailed

DatasetCommitMetadataHook = Callable[[TransactionContext, CommitResult], None]


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
        return committed_version_file_path(self.dataset_storage, version)

    def _load_manifest(self, manifest_uri: str) -> DatasetManifest:
        return committed_manifest(self.dataset_storage, manifest_uri)

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
        transaction_metadata: DatasetTransactionMetadata | None = None,
        after_persist: DatasetCommitMetadataHook | None = None,
    ) -> CommitResult:
        request = DatasetFinalizationRequest(
            target=DatasetCommitTarget.from_row(dataset),
            transaction_id=transaction_id,
            staged_parquet=staged_parquet,
            run_id=run_id,
            audit_action=audit_action,
            outbox_event_type=outbox_event_type,
            extra_checks=list(extra_checks or []),
            transaction_metadata=dict(transaction_metadata or {}),
        )
        self.dataset_transaction_repository.lock_dataset_for_version_allocation(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            dataset_id=request.target.dataset_id,
        )
        validation = self._validate_finalization_request(conn, ctx, request)
        commit = self._store_finalized_dataset_commit(conn, ctx, request, validation)
        result = build_commit_result(request, validation, commit)
        self._persist_commit_metadata(conn, ctx, request, validation, commit, result, after_persist)
        return result

    def _validate_finalization_request(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        request: DatasetFinalizationRequest,
    ) -> DatasetFinalizationCheck:
        tx = self._require_open_transaction(conn, request.transaction_id)
        ensure_snapshot_base_current(conn=conn, tx=tx, dataset_version_service=self.dataset_version_service)
        validation = self._validate_candidate_once(conn, ctx, request, branch=str(tx["branch"]))
        if current_candidate_manifest_hash(request.staged_parquet, validation) == validation.checked_manifest_hash:
            return validation
        validation = self._validate_candidate_once(conn, ctx, request, branch=str(tx["branch"]))
        if current_candidate_manifest_hash(request.staged_parquet, validation) == validation.checked_manifest_hash:
            return validation
        self._abort_finalization_with_failures(
            conn,
            ctx,
            request.transaction_id,
            [{"check": "candidate_manifest_hash", "status": "failed", "reason": "changed during validation"}],
        )
        raise AssertionError("unreachable")

    def _validate_candidate_once(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        request: DatasetFinalizationRequest,
        *,
        branch: str,
    ) -> DatasetFinalizationCheck:
        stats = self.dataset_quality_service._inspect_parquet(request.staged_parquet, request.target.primary_key)
        checked_candidate_hash = checked_manifest_hash(stats)
        schema_ref = self.dataset_quality_service._ensure_schema_reference(
            conn, request.target.row, stats.schema_json, stats.schema_hash
        )
        evolution = self.dataset_quality_service._schema_evolution_result(
            conn, request.target.row, stats.schema_json, schema_ref
        )
        failures = self._finalization_failures(conn, ctx, request, stats, schema_ref, checked_candidate_hash, evolution)
        if failures:
            self._abort_finalization_with_failures(conn, ctx, request.transaction_id, failures)
        return DatasetFinalizationCheck(
            branch=branch,
            stats=stats,
            checked_manifest_hash=checked_candidate_hash,
            schema_version_id=schema_ref.schema_id,
            schema_version=schema_ref.version,
            schema_evolution_metadata=evolution.metadata,
        )

    def _finalization_failures(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        request: DatasetFinalizationRequest,
        stats: StagedFileStats,
        schema_ref: DatasetSchemaReference,
        checked_manifest_hash: str,
        evolution: DatasetSchemaEvolutionResult,
    ) -> list[DatasetCheckResult]:
        failures = self.dataset_quality_service._run_dataset_checks(
            conn,
            ctx,
            request.target.row,
            stats.parquet_path,
            stats.row_count,
            request.run_id,
            request.transaction_id,
            extra_checks=request.extra_checks,
            checked_manifest_hash=checked_manifest_hash,
            validated_against_schema_version_id=schema_ref.schema_id,
            validated_against_schema_version=schema_ref.version,
        )
        if evolution.failure:
            failures.append(evolution.failure)
        return failures

    def _store_finalized_dataset_commit(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        request: DatasetFinalizationRequest,
        validation: DatasetFinalizationCheck,
    ) -> DatasetCommitArtifacts:
        self._ensure_checked_candidate_unchanged(conn, ctx, request, validation)
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
            schema_version=validation.schema_version,
            stored_commit=stored_commit,
        )

    def _ensure_checked_candidate_unchanged(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        request: DatasetFinalizationRequest,
        validation: DatasetFinalizationCheck,
    ) -> None:
        actual_hash = current_candidate_manifest_hash(request.staged_parquet, validation)
        if actual_hash == validation.checked_manifest_hash:
            return
        self._abort_finalization_with_failures(
            conn,
            ctx,
            request.transaction_id,
            [
                {
                    "check": "candidate_manifest_hash",
                    "status": "failed",
                    "expected": validation.checked_manifest_hash,
                    "actual": actual_hash,
                }
            ],
        )

    def _persist_commit_metadata(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        request: DatasetFinalizationRequest,
        validation: DatasetFinalizationCheck,
        commit: DatasetCommitArtifacts,
        result: CommitResult,
        after_persist: DatasetCommitMetadataHook | None,
    ) -> None:
        try:
            self._persist_finalized_dataset_version(conn, ctx, request, validation, commit)
            self._emit_dataset_commit_events(conn, ctx, request, validation, commit)
            if after_persist is not None:
                after_persist(conn, result)
        except DatasetVersionConflictError as exc:
            cleanup = self._cleanup_committed_version_artifacts(ctx, request, validation, commit)
            raise dataset_version_conflict_error(request, validation, commit, cleanup) from exc
        except Exception as exc:
            cleanup = self._cleanup_committed_version_artifacts(ctx, request, validation, commit)
            raise dataset_commit_persistence_error(request, validation, commit, cleanup) from exc

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
            record=build_dataset_file_record(ctx, validation, commit),
        )
        commit_open_dataset_transaction(
            repository=self.dataset_transaction_repository,
            conn=conn,
            ctx=ctx,
            transaction_id=request.transaction_id,
            version_id=commit.version_id,
            schema_version=commit.schema_version,
            metadata=finalized_transaction_metadata(request, validation),
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
            after_ref=dataset_commit_after_ref(request, validation, commit),
            correlation_id=request.run_id,
        )

    def _abort_finalization_with_failures(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        transaction_id: str,
        failures: Sequence[DatasetCheckResult],
    ) -> None:
        """Abort an open transaction and record validation failures for audit."""
        failure_list = [block_commit_failure(failure) for failure in failures]
        aborted = self.dataset_transaction_repository.abort_transaction(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            transaction_id=transaction_id,
            metadata={"validationFailures": failure_list},
        )
        if not aborted:
            raise ConflictDetected(
                "dataset transaction abort lost its OPEN state",
                details={"transaction_id": transaction_id},
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
        raise DatasetCommitBlocked("dataset checks failed", details={"failures": failure_list})

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
            tx = self.dataset_transaction_repository.transaction_by_id(transaction=conn, transaction_id=transaction_id)
            staging_cleanup = cleanup_staging_transaction(self.dataset_storage, ctx, tx, transaction_id)
            aborted = self.dataset_transaction_repository.abort_open_transaction_and_fail_run(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                transaction_id=transaction_id,
                run_id=run_id,
                run_kind=run_kind,
                error=error_payload,
                completed_at=_now(),
            )
            _require_abort_won(is_aborted=aborted, transaction_id=transaction_id, run_id=run_id)
            self.runtime_service._audit(
                conn,
                ctx,
                event_type="dataset.transaction.aborted",
                resource_type="dataset_transaction",
                resource_id=transaction_id,
                action="abort",
                after_ref={"error": error_payload, "staging_cleanup": staging_cleanup},
                correlation_id=run_id,
            )


def _require_abort_won(is_aborted: bool, transaction_id: str, run_id: str) -> None:
    if is_aborted:
        return
    raise ConflictDetected(
        "dataset transaction abort lost its current state",
        details={"transaction_id": transaction_id, "run_id": run_id},
    )
