from __future__ import annotations

from pathlib import Path
from typing import Any

from foundry_lite.application.ports import (
    DatasetFileRecord,
    DatasetRunKind,
    DatasetTransactionRecord,
    DatasetVersionRecord,
)
from foundry_lite.application.primitives import (
    CommitResult,
    _dataset_ref,
    _new_id,
    _now,
)
from foundry_lite.application.services.base import CoreService
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import (
    ConflictDetected,
    NotFound,
    ValidationFailed,
)


class DatasetTransactionService(CoreService):
    def _open_dataset_transaction(
        self,
        conn: Any,
        ctx: RequestContext,
        dataset: dict[str, Any],
        tx_type: str,
        *,
        branch: str = "main",
    ) -> str:
        if tx_type not in {"SNAPSHOT", "APPEND"}:
            raise ValidationFailed("v1 supports only SNAPSHOT and APPEND transactions")
        tx_id = _new_id("dstx")
        latest = self._latest_version_by_dataset_id(conn, dataset["id"], allow_missing=True)
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

    def _staging_file(self, dataset: dict[str, Any], transaction_id: str, file_name: str) -> Path:
        return self.dataset_storage.staging_file(
            tenant_id=dataset["tenant_id"],
            dataset_id=dataset["id"],
            transaction_id=transaction_id,
            file_name=file_name,
        )

    def _version_file_path(self, version: dict[str, Any]) -> Path:
        return self.dataset_storage.first_data_file_path(version["manifest_uri"])

    def _load_manifest(self, manifest_uri: str) -> dict[str, Any]:
        return self.dataset_storage.load_manifest(manifest_uri)

    def _finalize_open_transaction(
        self,
        conn: Any,
        ctx: RequestContext,
        *,
        dataset: dict[str, Any],
        transaction_id: str,
        staged_parquet: Path,
        run_id: str,
        audit_action: str,
        outbox_event_type: str,
        extra_checks: list[dict[str, Any]] | None = None,
    ) -> CommitResult:
        tx = self._require_open_transaction(conn, transaction_id)
        stats = self._inspect_parquet(staged_parquet, dataset["primary_key"])
        failures = self._run_dataset_checks(
            conn,
            ctx,
            dataset,
            stats.parquet_path,
            stats.row_count,
            run_id,
            transaction_id,
            extra_checks=extra_checks or [],
        )
        compatibility_error = self._schema_compatibility_error(conn, dataset, stats.schema_json)
        if compatibility_error:
            failures.append(compatibility_error)
        if failures:
            self.dataset_transaction_repository.abort_transaction(
                transaction=conn,
                transaction_id=transaction_id,
                metadata={"validationFailures": failures},
            )
            self._audit(
                conn,
                ctx,
                event_type="dataset.transaction.aborted",
                resource_type="dataset_transaction",
                resource_id=transaction_id,
                action="abort",
                after_ref={"failures": failures},
            )
            raise ValidationFailed("dataset checks failed", details={"failures": failures})

        schema_version = self._ensure_schema(conn, dataset, stats.schema_json, stats.schema_hash)
        version_number = self._next_dataset_version_number(conn, dataset["id"])
        version_id = _new_id("dsv")
        stored_commit = self.dataset_storage.commit_staged_file(
            tenant_id=ctx.tenant_id,
            dataset_id=dataset["id"],
            branch=tx["branch"],
            version_id=version_id,
            dataset_ref=_dataset_ref(dataset),
            schema_hash=stats.schema_hash,
            staged_file=staged_parquet,
            row_count=stats.row_count,
            created_at=_now(),
        )
        self.dataset_transaction_repository.insert_version(
            transaction=conn,
            record=DatasetVersionRecord(
                version_id=version_id,
                tenant_id=ctx.tenant_id,
                dataset_id=dataset["id"],
                branch=tx["branch"],
                version_number=version_number,
                transaction_id=transaction_id,
                schema_version=schema_version,
                manifest_uri=stored_commit.manifest_uri,
                row_count=stats.row_count,
                byte_size=stored_commit.byte_size,
                status="active",
                superseded_by_version_id=None,
                created_at=_now(),
            ),
        )
        self.dataset_transaction_repository.insert_file(
            transaction=conn,
            record=DatasetFileRecord(
                file_id=_new_id("dsf"),
                tenant_id=ctx.tenant_id,
                dataset_version_id=version_id,
                uri=stored_commit.data_file_uri,
                file_format="parquet",
                row_count=stats.row_count,
                byte_size=stored_commit.byte_size,
                content_hash=stored_commit.content_hash,
                partition_values={},
            ),
        )
        self.dataset_transaction_repository.commit_transaction(
            transaction=conn,
            transaction_id=transaction_id,
            committed_version_id=version_id,
            schema_version=schema_version,
            committed_at=_now(),
        )
        self._outbox(
            conn,
            ctx,
            outbox_event_type,
            "dataset_version",
            version_id,
            {
                "datasetId": dataset["id"],
                "datasetRef": _dataset_ref(dataset),
                "versionId": version_id,
                "branch": tx["branch"],
            },
            idempotency_key=version_id,
            correlation_id=run_id,
        )
        self._audit(
            conn,
            ctx,
            event_type="dataset.version.committed",
            resource_type="dataset_version",
            resource_id=version_id,
            action=audit_action,
            after_ref={"dataset_ref": _dataset_ref(dataset), "version_number": version_number},
            correlation_id=run_id,
        )
        return CommitResult(
            dataset_id=dataset["id"],
            dataset_ref=_dataset_ref(dataset),
            transaction_id=transaction_id,
            version_id=version_id,
            version_number=version_number,
            row_count=stats.row_count,
            manifest_uri=stored_commit.manifest_uri,
            schema_hash=stats.schema_hash,
        )

    def _require_open_transaction(self, conn: Any, transaction_id: str) -> dict[str, Any]:
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
    ) -> None:
        self.dataset_transaction_repository.abort_open_transaction_and_fail_run(
            transaction_id=transaction_id,
            run_id=run_id,
            run_kind=run_kind,
            error=self._error_payload(exc),
            completed_at=_now(),
        )
