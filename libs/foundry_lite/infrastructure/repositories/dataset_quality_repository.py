from __future__ import annotations

from typing import Any, cast

from sqlalchemy import and_, func, insert, select
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Engine

from foundry_lite.application.ports.dataset_quality_repository import (
    DatasetCheckRecord,
    DatasetCheckResultRecord,
    DatasetCheckResultRow,
    DatasetCheckRow,
    DatasetSchemaRecord,
    DatasetSchemaRow,
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
    ) -> DatasetSchemaRow | None:
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
        return cast(DatasetSchemaRow, dict(row)) if row else None

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
    ) -> DatasetCheckRow | None:
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
        return cast(DatasetCheckRow, dict(row)) if row else None

    def insert_check(self, *, transaction: Any, record: DatasetCheckRecord) -> None:
        transaction.execute(_dataset_check_insert_or_ignore(transaction, record))

    def insert_check_result(self, *, transaction: Any, record: DatasetCheckResultRecord) -> None:
        transaction.execute(
            insert(db.dataset_check_results).values(
                id=record.check_result_id,
                tenant_id=record.tenant_id,
                check_id=record.check_id,
                run_id=record.run_id,
                transaction_id=record.transaction_id,
                checked_manifest_hash=record.checked_manifest_hash,
                validated_against_schema_version_id=record.validated_against_schema_version_id,
                validated_against_schema_version=record.validated_against_schema_version,
                status=record.status,
                details=record.details,
                created_at=record.created_at,
            )
        )

    def check_results_for_transaction(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        transaction_id: str,
    ) -> list[DatasetCheckResultRow]:
        rows = (
            transaction.execute(
                select(db.dataset_check_results)
                .where(
                    and_(
                        db.dataset_check_results.c.tenant_id == tenant_id,
                        db.dataset_check_results.c.transaction_id == transaction_id,
                    )
                )
                .order_by(db.dataset_check_results.c.created_at, db.dataset_check_results.c.id)
            )
            .mappings()
            .all()
        )
        return [cast(DatasetCheckResultRow, dict(row)) for row in rows]


def _dataset_check_insert_or_ignore(transaction: Any, record: DatasetCheckRecord) -> Any:
    values = _dataset_check_values(record)
    conflict_columns = ["tenant_id", "dataset_id", "name"]
    if transaction.dialect.name == "postgresql":
        return postgres_insert(db.dataset_checks).values(**values).on_conflict_do_nothing(
            index_elements=conflict_columns
        )
    if transaction.dialect.name == "sqlite":
        return sqlite_insert(db.dataset_checks).values(**values).on_conflict_do_nothing(
            index_elements=conflict_columns
        )
    return insert(db.dataset_checks).values(**values)


def _dataset_check_values(record: DatasetCheckRecord) -> dict[str, object]:
    return {
        "id": record.check_id,
        "tenant_id": record.tenant_id,
        "dataset_id": record.dataset_id,
        "name": record.name,
        "check_type": record.check_type,
        "config": record.config,
        "severity": record.severity,
        "enabled": record.enabled,
    }
