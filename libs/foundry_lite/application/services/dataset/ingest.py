from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn

from foundry_lite.application.ports import DatasetRow, TransactionContext
from foundry_lite.application.ports.connector_adapter import (
    ConnectorAdapter,
    ConnectorSnapshotRequest,
    RestSourceConfig,
)
from foundry_lite.application.ports.dataset_transaction_repository import SyncRunRecord
from foundry_lite.application.primitives import (
    CommitResult,
    _new_id,
    _now,
)
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.dataset.protocols import (
    DatasetRegistryLookup,
    DatasetRuntimeBoundary,
    DatasetTransactionManager,
)
from foundry_lite.application.upload_limits import require_csv_size_limit
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import (
    ConflictDetected,
    InvariantViolation,
    NotFound,
    PermissionDenied,
    ValidationFailed,
)


@dataclass(frozen=True)
class UploadSyncPlan:
    """Identifiers opened before converting one CSV upload."""

    transaction_id: str
    run_id: str


class DatasetIngestService(CoreService):
    required_dependencies = ("engine", "compute_adapter", "connector_adapter", "dataset_transaction_repository")
    required_collaborators = (
        "dataset_registry_service",
        "dataset_transaction_service",
        "runtime_service",
    )
    connector_adapter: ConnectorAdapter
    dataset_registry_service: DatasetRegistryLookup
    dataset_transaction_service: DatasetTransactionManager
    runtime_service: DatasetRuntimeBoundary

    def upload_csv(
        self,
        dataset_ref: str,
        csv_path: str | Path,
        *,
        ctx: RequestContext | None = None,
        sync_name: str | None = None,
    ) -> CommitResult:
        ctx = ctx or RequestContext()
        self.runtime_service._require_or_audit(ctx, "dataset:write", "dataset", dataset_ref)
        dataset = self.dataset_registry_service.get_dataset(dataset_ref, ctx=ctx)
        source_path = Path(csv_path).resolve()
        if not source_path.exists():
            raise NotFound("csv file not found", details={"path": str(source_path)})
        require_csv_size_limit(source_path)

        with self.engine.begin() as conn:
            plan = self._start_upload_sync_run(conn, ctx, dataset, dataset_ref, sync_name)

        staged = self.dataset_transaction_service._staging_file(dataset, plan.transaction_id, "part-00000.parquet")
        try:
            self._csv_to_parquet(source_path, staged)
            with self.engine.begin() as conn:
                result = self.dataset_transaction_service._finalize_open_transaction(
                    conn,
                    ctx,
                    dataset=dataset,
                    transaction_id=plan.transaction_id,
                    staged_parquet=staged,
                    run_id=plan.run_id,
                    audit_action="csv_upload_commit",
                    outbox_event_type="dataset.version.committed",
                )
                self._mark_sync_run_committed(conn, ctx, plan.run_id, result)
                return result
        except Exception as exc:
            self._abort_upload_after_error(ctx, plan.transaction_id, plan.run_id, exc)

    def sync_connector_snapshot(
        self,
        dataset_ref: str,
        *,
        connector_name: str,
        resource_name: str,
        ctx: RequestContext | None = None,
        sync_name: str | None = None,
        cursor: Mapping[str, object] | None = None,
        rest: RestSourceConfig | None = None,
    ) -> CommitResult:
        ctx = ctx or RequestContext()
        self.runtime_service._require_or_audit(ctx, "dataset:write", "dataset", dataset_ref)
        dataset = self.dataset_registry_service.get_dataset(dataset_ref, ctx=ctx)
        with self.engine.begin() as conn:
            plan = self._start_connector_sync_run(conn, ctx, dataset, connector_name, resource_name, sync_name)
        staged = self.dataset_transaction_service._staging_file(dataset, plan.transaction_id, "part-00000.parquet")
        try:
            snapshot = self.connector_adapter.snapshot(
                ConnectorSnapshotRequest(connector_name, resource_name, ctx.tenant_id, ctx.request_id, cursor, rest)
            )
            rows = list(snapshot.rows)
            self._rows_to_parquet(rows, staged, _connector_fieldnames(snapshot.schema, rows))
            with self.engine.begin() as conn:
                result = self.dataset_transaction_service._finalize_open_transaction(
                    conn,
                    ctx,
                    dataset=dataset,
                    transaction_id=plan.transaction_id,
                    staged_parquet=staged,
                    run_id=plan.run_id,
                    audit_action="connector_snapshot_commit",
                    outbox_event_type="dataset.version.committed",
                )
                self._mark_sync_run_committed(conn, ctx, plan.run_id, result)
                return result
        except Exception as exc:
            self._abort_connector_after_error(ctx, plan.transaction_id, plan.run_id, exc)

    def ingest_webhook_event(
        self,
        dataset_ref: str,
        *,
        connector_name: str,
        resource_name: str,
        payload: Mapping[str, object],
        raw_body: bytes,
        signature: str,
        secret: str,
        ctx: RequestContext | None = None,
    ) -> CommitResult:
        ctx = ctx or RequestContext()
        if not _webhook_signature_valid(raw_body, signature, secret):
            self._audit_webhook_signature_denied(ctx, dataset_ref, connector_name, resource_name)
            raise PermissionDenied("invalid webhook signature", details={"connector": connector_name})
        self.runtime_service._require_or_audit(ctx, "dataset:write", "dataset", dataset_ref)
        dataset = self.dataset_registry_service.get_dataset(dataset_ref, ctx=ctx)
        with self.engine.begin() as conn:
            plan = self._start_connector_sync_run(
                conn,
                ctx,
                dataset,
                connector_name,
                resource_name,
                f"webhook:{connector_name}:{resource_name}",
                tx_type="APPEND",
                source_type=f"webhook.{connector_name}",
            )
        staged = self.dataset_transaction_service._staging_file(dataset, plan.transaction_id, "part-00000.parquet")
        return self._commit_webhook_event(ctx, dataset, plan, staged, connector_name, resource_name, payload)

    def _start_upload_sync_run(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        dataset: DatasetRow,
        dataset_ref: str,
        sync_name: str | None,
    ) -> UploadSyncPlan:
        """Open the upload transaction and persist the EXTRACTING sync run."""
        transaction_id = self.dataset_transaction_service._open_dataset_transaction(conn, ctx, dataset, "SNAPSHOT")
        run_id = _new_id("sync_run")
        self.dataset_transaction_repository.insert_sync_run(
            transaction=conn,
            record=SyncRunRecord(
                sync_run_id=run_id,
                tenant_id=ctx.tenant_id,
                sync_name=sync_name or f"upload:{dataset_ref}",
                source_type="file.csv",
                output_dataset_id=str(dataset["id"]),
                transaction_id=transaction_id,
                committed_version_id=None,
                status="EXTRACTING",
                error=None,
                created_at=_now(),
                completed_at=None,
            ),
        )
        return UploadSyncPlan(transaction_id=transaction_id, run_id=run_id)

    def _start_connector_sync_run(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        dataset: DatasetRow,
        connector_name: str,
        resource_name: str,
        sync_name: str | None,
        *,
        tx_type: str = "SNAPSHOT",
        source_type: str | None = None,
    ) -> UploadSyncPlan:
        transaction_id = self.dataset_transaction_service._open_dataset_transaction(conn, ctx, dataset, tx_type)
        run_id = _new_id("sync_run")
        self.dataset_transaction_repository.insert_sync_run(
            transaction=conn,
            record=SyncRunRecord(
                sync_run_id=run_id,
                tenant_id=ctx.tenant_id,
                sync_name=sync_name or f"connector:{connector_name}:{resource_name}",
                source_type=source_type or f"connector.{connector_name}",
                output_dataset_id=str(dataset["id"]),
                transaction_id=transaction_id,
                committed_version_id=None,
                status="EXTRACTING",
                error=None,
                created_at=_now(),
                completed_at=None,
            ),
        )
        return UploadSyncPlan(transaction_id=transaction_id, run_id=run_id)

    def _csv_to_parquet(self, source_path: Path, target_path: Path) -> None:
        self.compute_adapter.csv_to_parquet(source_path, target_path)

    def _abort_upload_after_error(
        self,
        ctx: RequestContext,
        transaction_id: str,
        run_id: str,
        exc: Exception,
    ) -> NoReturn:
        self.dataset_transaction_service._abort_transaction_after_error(
            ctx,
            transaction_id,
            run_id,
            exc,
            "sync",
            adapter="compute_adapter.csv_to_parquet",
        )
        if isinstance(exc, ValidationFailed | NotFound | ConflictDetected | InvariantViolation):
            raise exc
        raise ValidationFailed("csv upload failed", details={"error": str(exc)}) from exc

    def _abort_connector_after_error(
        self,
        ctx: RequestContext,
        transaction_id: str,
        run_id: str,
        exc: Exception,
    ) -> NoReturn:
        self.dataset_transaction_service._abort_transaction_after_error(
            ctx,
            transaction_id,
            run_id,
            exc,
            "sync",
            adapter="connector_adapter.snapshot",
        )
        if isinstance(exc, ValidationFailed | NotFound | ConflictDetected | InvariantViolation):
            raise exc
        raise ValidationFailed("connector snapshot sync failed", details={"error": str(exc)}) from exc

    def _commit_webhook_event(
        self,
        ctx: RequestContext,
        dataset: DatasetRow,
        plan: UploadSyncPlan,
        staged: Path,
        connector_name: str,
        resource_name: str,
        payload: Mapping[str, object],
    ) -> CommitResult:
        try:
            row = _webhook_event_row(connector_name, resource_name, payload)
            self._rows_to_parquet([row], staged, list(row))
            with self.engine.begin() as conn:
                result = self.dataset_transaction_service._finalize_open_transaction(
                    conn,
                    ctx,
                    dataset=dataset,
                    transaction_id=plan.transaction_id,
                    staged_parquet=staged,
                    run_id=plan.run_id,
                    audit_action="webhook_append_commit",
                    outbox_event_type="dataset.version.committed",
                )
                self._mark_sync_run_committed(conn, ctx, plan.run_id, result)
                return result
        except Exception as exc:
            self._abort_connector_after_error(ctx, plan.transaction_id, plan.run_id, exc)

    def _audit_webhook_signature_denied(
        self,
        ctx: RequestContext,
        dataset_ref: str,
        connector_name: str,
        resource_name: str,
    ) -> None:
        with self.engine.begin() as conn:
            self.runtime_service._audit(
                conn,
                ctx,
                event_type="permission.denied",
                resource_type="webhook",
                resource_id=f"{connector_name}:{resource_name}",
                action="webhook:ingest",
                decision="deny",
                before_ref={"dataset_ref": dataset_ref},
            )

    def _mark_sync_run_committed(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        run_id: str,
        result: CommitResult,
    ) -> None:
        """Close the sync run with the committed dataset version under the tenant."""
        self.dataset_transaction_repository.update_sync_run_terminal(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            sync_run_id=run_id,
            status="COMMITTED",
            committed_version_id=result.version_id,
            completed_at=_now(),
        )

    def _rows_to_parquet(self, rows: Sequence[Mapping[str, object]], target_path: Path, fieldnames: list[str]) -> None:
        self.compute_adapter.rows_to_parquet(rows, target_path, fieldnames)


def _connector_fieldnames(schema: Mapping[str, object], rows: Sequence[Mapping[str, object]]) -> list[str]:
    columns = schema.get("columns")
    if isinstance(columns, Sequence) and not isinstance(columns, str):
        names = [column for column in columns if isinstance(column, str)]
        if len(names) == len(columns) and names:
            return names
    if rows:
        return [str(name) for name in rows[0]]
    raise ValidationFailed("connector snapshot returned no schema columns")


def _webhook_signature_valid(raw_body: bytes, signature: str, secret: str) -> bool:
    if not secret:
        raise ValidationFailed("webhook secret is not configured")
    expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature, f"sha256={expected}")


def _webhook_event_row(
    connector_name: str,
    resource_name: str,
    payload: Mapping[str, object],
) -> Mapping[str, object]:
    return {
        "event_id": _new_id("webhook"),
        "connector": connector_name,
        "resource": resource_name,
        "received_at": f"ts:{_now()}",
        "payload_json": json.dumps(dict(payload), sort_keys=True, separators=(",", ":"), default=str),
    }
