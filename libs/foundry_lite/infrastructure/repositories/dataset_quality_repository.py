from __future__ import annotations

from typing import Any

from sqlalchemy import and_, func, insert, select
from sqlalchemy.engine import Engine

from foundry_lite.application.ports.dataset_quality_repository import (
    DatasetCheckRecord,
    DatasetCheckResultRecord,
    DatasetSchemaRecord,
)
from foundry_lite.infrastructure import schema as db


class SqlAlchemyDatasetQualityRepository:
    """SQLAlchemy implementation of dataset schema registry and check result persistence."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def schema_by_hash(
        self,
        *,
        transaction: Any,
        dataset_id: str,
        schema_hash: str,
    ) -> dict[str, Any] | None:
        row = (
            transaction.execute(
                select(db.dataset_schemas).where(
                    and_(
                        db.dataset_schemas.c.dataset_id == dataset_id,
                        db.dataset_schemas.c.schema_hash == schema_hash,
                    )
                )
            )
            .mappings()
            .first()
        )
        return dict(row) if row else None

    def latest_schema_version(self, *, transaction: Any, dataset_id: str) -> int | None:
        value = transaction.execute(
            select(func.max(db.dataset_schemas.c.version)).where(db.dataset_schemas.c.dataset_id == dataset_id)
        ).scalar()
        return int(value) if value is not None else None

    def insert_schema(self, *, transaction: Any, record: DatasetSchemaRecord) -> None:
        transaction.execute(
            insert(db.dataset_schemas).values(
                id=record.schema_id,
                dataset_id=record.dataset_id,
                version=record.version,
                schema_json=record.schema_json,
                schema_hash=record.schema_hash,
                created_at=record.created_at,
            )
        )

    def check_by_name(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        dataset_id: str,
        name: str,
    ) -> dict[str, Any] | None:
        row = (
            transaction.execute(
                select(db.dataset_checks).where(
                    and_(
                        db.dataset_checks.c.tenant_id == tenant_id,
                        db.dataset_checks.c.dataset_id == dataset_id,
                        db.dataset_checks.c.name == name,
                    )
                )
            )
            .mappings()
            .first()
        )
        return dict(row) if row else None

    def insert_check(self, *, transaction: Any, record: DatasetCheckRecord) -> None:
        transaction.execute(
            insert(db.dataset_checks).values(
                id=record.check_id,
                tenant_id=record.tenant_id,
                dataset_id=record.dataset_id,
                name=record.name,
                check_type=record.check_type,
                config=record.config,
                severity=record.severity,
                enabled=record.enabled,
            )
        )

    def insert_check_result(self, *, transaction: Any, record: DatasetCheckResultRecord) -> None:
        transaction.execute(
            insert(db.dataset_check_results).values(
                id=record.check_result_id,
                tenant_id=record.tenant_id,
                check_id=record.check_id,
                run_id=record.run_id,
                transaction_id=record.transaction_id,
                status=record.status,
                details=record.details,
                created_at=record.created_at,
            )
        )
