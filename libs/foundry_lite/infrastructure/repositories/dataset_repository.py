"""SQLAlchemy repository adapter for dataset repository persistence."""

from __future__ import annotations

from typing import Any, cast

from sqlalchemy import and_, insert, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from foundry_lite.application.ports import DatasetAlreadyExistsError, DatasetRow
from foundry_lite.infrastructure import schema as db


class SqlAlchemyDatasetRepository:
    """SQLAlchemy implementation of dataset registry metadata reads/writes."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def create_dataset(
        self,
        *,
        transaction: Any,
        dataset_id: str,
        tenant_id: str,
        namespace: str,
        name: str,
        description: str | None,
        storage_kind: str,
        storage_uri: str,
        owner_team: str | None,
        classification: str | None,
        primary_key: list[str],
        partition_spec: list[str],
        sort_order: list[str],
        target_file_size_bytes: int | None,
        created_at: str,
        updated_at: str,
    ) -> None:
        try:
            transaction.execute(
                insert(db.datasets).values(
                    id=dataset_id,
                    tenant_id=tenant_id,
                    namespace=namespace,
                    name=name,
                    description=description,
                    storage_kind=storage_kind,
                    storage_uri=storage_uri,
                    owner_team=owner_team,
                    classification=classification,
                    status="active",
                    primary_key=primary_key,
                    partition_spec=partition_spec,
                    sort_order=sort_order,
                    target_file_size_bytes=target_file_size_bytes,
                    created_at=created_at,
                    updated_at=updated_at,
                )
            )
        except IntegrityError as exc:
            raise DatasetAlreadyExistsError from exc

    def find_active_dataset(self, *, tenant_id: str, namespace: str, name: str) -> DatasetRow | None:
        with self.engine.begin() as conn:
            return self.active_dataset_by_ref(
                transaction=conn,
                tenant_id=tenant_id,
                namespace=namespace,
                name=name,
            )

    def active_dataset_by_ref(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        namespace: str,
        name: str,
    ) -> DatasetRow | None:
        row = (
            transaction.execute(
                select(db.datasets).where(
                    and_(
                        db.datasets.c.tenant_id == tenant_id,
                        db.datasets.c.namespace == namespace,
                        db.datasets.c.name == name,
                        db.datasets.c.status == "active",
                    )
                )
            )
            .mappings()
            .first()
        )
        return cast(DatasetRow, dict(row)) if row else None

    def dataset_by_id(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        dataset_id: str,
    ) -> DatasetRow | None:
        row = (
            transaction.execute(
                select(db.datasets).where(
                    and_(
                        db.datasets.c.tenant_id == tenant_id,
                        db.datasets.c.id == dataset_id,
                    )
                )
            )
            .mappings()
            .first()
        )
        return cast(DatasetRow, dict(row)) if row else None

    def list_active_datasets(self, *, tenant_id: str) -> list[DatasetRow]:
        with self.engine.begin() as conn:
            rows = (
                conn.execute(
                    select(db.datasets)
                    .where(
                        and_(
                            db.datasets.c.tenant_id == tenant_id,
                            db.datasets.c.status == "active",
                        )
                    )
                    .order_by(db.datasets.c.namespace, db.datasets.c.name)
                )
                .mappings()
                .all()
            )
            return [cast(DatasetRow, dict(row)) for row in rows]
