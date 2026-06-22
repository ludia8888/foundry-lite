from __future__ import annotations

from typing import Any, cast

from sqlalchemy import and_, desc, func, select
from sqlalchemy.engine import Engine

from foundry_lite.application.ports.dataset_quality_repository import DatasetSchemaRow
from foundry_lite.application.ports.dataset_version_repository import DatasetVersionRow
from foundry_lite.infrastructure import schema as db


class SqlAlchemyDatasetVersionRepository:
    """SQLAlchemy implementation of dataset version and schema reads."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def next_version_number(self, *, transaction: Any, dataset_id: str) -> int:
        latest = (
            transaction.execute(
                select(func.max(db.dataset_versions.c.version_number)).where(
                    db.dataset_versions.c.dataset_id == dataset_id
                )
            ).scalar()
            or 0
        )
        return int(latest) + 1

    def schema_for_version(self, *, dataset_id: str, schema_version: int) -> DatasetSchemaRow | None:
        with self.engine.begin() as conn:
            row = (
                conn.execute(
                    select(db.dataset_schemas).where(
                        and_(
                            db.dataset_schemas.c.dataset_id == dataset_id,
                            db.dataset_schemas.c.version == schema_version,
                        )
                    )
                )
                .mappings()
                .first()
            )
            return cast(DatasetSchemaRow, dict(row)) if row else None

    def latest_version_by_dataset_id(self, *, transaction: Any, dataset_id: str) -> DatasetVersionRow | None:
        row = (
            transaction.execute(
                select(db.dataset_versions)
                .where(db.dataset_versions.c.dataset_id == dataset_id)
                .order_by(desc(db.dataset_versions.c.version_number))
                .limit(1)
            )
            .mappings()
            .first()
        )
        return cast(DatasetVersionRow, dict(row)) if row else None

    def version_by_id(self, *, transaction: Any, version_id: str) -> DatasetVersionRow | None:
        row = (
            transaction.execute(select(db.dataset_versions).where(db.dataset_versions.c.id == version_id))
            .mappings()
            .first()
        )
        return cast(DatasetVersionRow, dict(row)) if row else None

    def version_by_dataset_id_and_id(
        self,
        *,
        transaction: Any,
        dataset_id: str,
        version_id: str,
    ) -> DatasetVersionRow | None:
        row = (
            transaction.execute(
                select(db.dataset_versions).where(
                    and_(
                        db.dataset_versions.c.dataset_id == dataset_id,
                        db.dataset_versions.c.id == version_id,
                    )
                )
            )
            .mappings()
            .first()
        )
        return cast(DatasetVersionRow, dict(row)) if row else None

    def list_versions(self, *, dataset_id: str) -> list[DatasetVersionRow]:
        with self.engine.begin() as conn:
            rows = (
                conn.execute(
                    select(db.dataset_versions)
                    .where(db.dataset_versions.c.dataset_id == dataset_id)
                    .order_by(db.dataset_versions.c.version_number)
                )
                .mappings()
                .all()
            )
            return [cast(DatasetVersionRow, dict(row)) for row in rows]
