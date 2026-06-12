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
                    created_at=created_at,
                    updated_at=updated_at,
                )
            )
        except IntegrityError as exc:
            raise DatasetAlreadyExistsError from exc

    def find_active_dataset(self, *, tenant_id: str, namespace: str, name: str) -> DatasetRow | None:
        with self.engine.begin() as conn:
            row = (
                conn.execute(
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
