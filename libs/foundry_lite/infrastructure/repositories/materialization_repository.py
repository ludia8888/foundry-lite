from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

from sqlalchemy import and_, func, insert, select, update
from sqlalchemy.engine import Engine

from foundry_lite.application.ports.materialization_repository import (
    MaterializationRecord,
    MaterializationRow,
    MaterializationRunRecord,
)
from foundry_lite.infrastructure import schema as db


class SqlAlchemyMaterializationRepository:
    """SQLAlchemy implementation of materialization registry, runs, and watermarks."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def materialization_by_api_name(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        api_name: str,
    ) -> MaterializationRow | None:
        row = (
            transaction.execute(
                select(db.materializations).where(
                    and_(
                        db.materializations.c.tenant_id == tenant_id,
                        db.materializations.c.api_name == api_name,
                    )
                )
            )
            .mappings()
            .first()
        )
        return cast(MaterializationRow, dict(row)) if row else None

    def insert_materialization(self, *, transaction: Any, record: MaterializationRecord) -> None:
        transaction.execute(
            insert(db.materializations).values(
                id=record.materialization_id,
                tenant_id=record.tenant_id,
                api_name=record.api_name,
                materialization_type=record.materialization_type,
                source_ref=record.source_ref,
                target_ref=record.target_ref,
                trigger_config=record.trigger_config,
                enabled=record.enabled,
            )
        )

    def materialization_by_id(self, *, transaction: Any, materialization_id: str) -> MaterializationRow | None:
        row = (
            transaction.execute(select(db.materializations).where(db.materializations.c.id == materialization_id))
            .mappings()
            .first()
        )
        return cast(MaterializationRow, dict(row)) if row else None

    def insert_materialization_run(self, *, transaction: Any, record: MaterializationRunRecord) -> None:
        transaction.execute(
            insert(db.materialization_runs).values(
                id=record.materialization_run_id,
                tenant_id=record.tenant_id,
                materialization_id=record.materialization_id,
                api_name=record.api_name,
                status=record.status,
                source_cursor=record.source_cursor,
                object_store_watermark=record.object_store_watermark,
                consistency_level=record.consistency_level,
                target_dataset_version_id=record.target_dataset_version_id,
                row_count=record.row_count,
                error=record.error,
                created_at=record.created_at,
                completed_at=record.completed_at,
            )
        )

    def update_materialization_run_terminal(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        materialization_run_id: str,
        status: str,
        target_dataset_version_id: str | None,
        row_count: int | None,
        error: Mapping[str, object] | None,
        completed_at: str,
    ) -> None:
        transaction.execute(
            update(db.materialization_runs)
            .where(
                and_(
                    db.materialization_runs.c.tenant_id == tenant_id,
                    db.materialization_runs.c.id == materialization_run_id,
                )
            )
            .values(
                status=status,
                target_dataset_version_id=target_dataset_version_id,
                row_count=row_count,
                error=error,
                completed_at=completed_at,
            )
        )

    def latest_action_run_watermark(self, *, transaction: Any) -> str | None:
        return transaction.execute(select(func.max(db.action_runs.c.created_at))).scalar()

    def latest_object_record_watermark(self, *, transaction: Any) -> str | None:
        return transaction.execute(select(func.max(db.object_records.c.updated_at))).scalar()
