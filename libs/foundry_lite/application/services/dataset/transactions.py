from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from foundry_lite.application.primitives import (
    CommitResult,
    _dataset_ref,
    _file_hash,
    _new_id,
    _now,
)
from foundry_lite.application.services.base import CoreServiceMixin
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import (
    ConflictDetected,
    NotFound,
    ValidationFailed,
)
from foundry_lite.infrastructure import schema as db
from sqlalchemy import and_, insert, update
from sqlalchemy.engine import Connection


class DatasetTransactionMixin(CoreServiceMixin):
    def _open_dataset_transaction(
        self,
        conn: Connection,
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
        conn.execute(
            insert(db.dataset_transactions).values(
                id=tx_id,
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
            )
        )
        return tx_id

    def _staging_file(self, dataset: dict[str, Any], transaction_id: str, file_name: str) -> Path:
        path = (
            self.storage_root
            / dataset["tenant_id"]
            / "datasets"
            / dataset["id"]
            / "_staging"
            / transaction_id
            / file_name
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def _version_file_path(self, version: dict[str, Any]) -> Path:
        manifest = self._load_manifest(version["manifest_uri"])
        return Path(manifest["files"][0]["uri"])

    def _load_manifest(self, manifest_uri: str) -> dict[str, Any]:
        return json.loads(Path(manifest_uri).read_text(encoding="utf-8"))

    def _finalize_open_transaction(
        self,
        conn: Connection,
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
            conn.execute(
                update(db.dataset_transactions)
                .where(db.dataset_transactions.c.id == transaction_id)
                .values(status="ABORTED", metadata={"validationFailures": failures})
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
        version_dir = (
            self.storage_root / ctx.tenant_id / "datasets" / dataset["id"] / "branch=main" / f"version={version_id}"
        )
        version_dir.mkdir(parents=True, exist_ok=True)
        final_parquet = version_dir / "part-00000.parquet"
        shutil.copy2(staged_parquet, final_parquet)
        manifest_path = version_dir / "manifest.json"
        manifest = {
            "version_id": version_id,
            "dataset": _dataset_ref(dataset),
            "branch": tx["branch"],
            "schema_hash": stats.schema_hash,
            "files": [
                {
                    "uri": str(final_parquet),
                    "format": "parquet",
                    "row_count": stats.row_count,
                    "byte_size": final_parquet.stat().st_size,
                    "content_hash": _file_hash(final_parquet),
                }
            ],
            "created_at": _now(),
        }
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
        byte_size = final_parquet.stat().st_size
        content_hash = _file_hash(final_parquet)
        conn.execute(
            insert(db.dataset_versions).values(
                id=version_id,
                tenant_id=ctx.tenant_id,
                dataset_id=dataset["id"],
                branch=tx["branch"],
                version_number=version_number,
                transaction_id=transaction_id,
                schema_version=schema_version,
                manifest_uri=str(manifest_path),
                row_count=stats.row_count,
                byte_size=byte_size,
                status="active",
                superseded_by_version_id=None,
                created_at=_now(),
            )
        )
        conn.execute(
            insert(db.dataset_files).values(
                id=_new_id("dsf"),
                tenant_id=ctx.tenant_id,
                dataset_version_id=version_id,
                uri=str(final_parquet),
                format="parquet",
                row_count=stats.row_count,
                byte_size=byte_size,
                content_hash=content_hash,
                partition_values={},
            )
        )
        conn.execute(
            update(db.dataset_transactions)
            .where(db.dataset_transactions.c.id == transaction_id)
            .values(
                status="COMMITTED",
                committed_version_id=version_id,
                schema_version=schema_version,
                committed_at=_now(),
            )
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
            manifest_uri=str(manifest_path),
            schema_hash=stats.schema_hash,
        )

    def _require_open_transaction(self, conn: Connection, transaction_id: str) -> dict[str, Any]:
        tx = self._select_by_id(conn, db.dataset_transactions, transaction_id)
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
        run_table: Any,
    ) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                update(db.dataset_transactions)
                .where(
                    and_(
                        db.dataset_transactions.c.id == transaction_id,
                        db.dataset_transactions.c.status == "OPEN",
                    )
                )
                .values(status="ABORTED", metadata={"error": self._error_payload(exc)})
            )
            conn.execute(
                update(run_table)
                .where(run_table.c.id == run_id)
                .values(status="FAILED", error=self._error_payload(exc), completed_at=_now())
            )
