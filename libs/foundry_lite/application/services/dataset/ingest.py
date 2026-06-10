from __future__ import annotations

from pathlib import Path
from typing import Any

from foundry_lite.application.ports.dataset_transaction_repository import SyncRunRecord
from foundry_lite.application.primitives import (
    CommitResult,
    _new_id,
    _now,
)
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.upload_limits import require_csv_size_limit
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import (
    ConflictDetected,
    InvariantViolation,
    NotFound,
    ValidationFailed,
)


class DatasetIngestService(CoreService):
    required_dependencies = ("engine", "compute_adapter", "dataset_transaction_repository")

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
            tx_id = self.dataset_transaction_service._open_dataset_transaction(conn, ctx, dataset, "SNAPSHOT")
            run_id = _new_id("sync_run")
            self.dataset_transaction_repository.insert_sync_run(
                transaction=conn,
                record=SyncRunRecord(
                    sync_run_id=run_id,
                    tenant_id=ctx.tenant_id,
                    sync_name=sync_name or f"upload:{dataset_ref}",
                    source_type="file.csv",
                    output_dataset_id=dataset["id"],
                    transaction_id=tx_id,
                    committed_version_id=None,
                    status="EXTRACTING",
                    error=None,
                    created_at=_now(),
                    completed_at=None,
                ),
            )

        staged = self.dataset_transaction_service._staging_file(dataset, tx_id, "part-00000.parquet")
        try:
            self._csv_to_parquet(source_path, staged)
            with self.engine.begin() as conn:
                result = self.dataset_transaction_service._finalize_open_transaction(
                    conn,
                    ctx,
                    dataset=dataset,
                    transaction_id=tx_id,
                    staged_parquet=staged,
                    run_id=run_id,
                    audit_action="csv_upload_commit",
                    outbox_event_type="dataset.version.committed",
                )
                self.dataset_transaction_repository.update_sync_run_terminal(
                    transaction=conn,
                    sync_run_id=run_id,
                    status="COMMITTED",
                    committed_version_id=result.version_id,
                    completed_at=_now(),
                )
                return result
        except Exception as exc:
            self.dataset_transaction_service._abort_transaction_after_error(ctx, tx_id, run_id, exc, "sync")
            if isinstance(exc, ValidationFailed | NotFound | ConflictDetected | InvariantViolation):
                raise
            raise ValidationFailed("csv upload failed", details={"error": str(exc)}) from exc

    def _csv_to_parquet(self, source_path: Path, target_path: Path) -> None:
        self.compute_adapter.csv_to_parquet(source_path, target_path)

    def _rows_to_parquet(self, rows: list[dict[str, Any]], target_path: Path, fieldnames: list[str]) -> None:
        self.compute_adapter.rows_to_parquet(rows, target_path, fieldnames)
