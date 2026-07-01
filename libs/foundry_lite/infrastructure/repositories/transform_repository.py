"""SQLAlchemy repository adapter for transform repository persistence."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, cast

from sqlalchemy import and_, desc, insert, select, update
from sqlalchemy.engine import Engine

from foundry_lite.application.ports.transaction_context import StatusTransition
from foundry_lite.application.ports.transform_repository import (
    TransformCheck,
    TransformRecord,
    TransformRow,
    TransformRunRecord,
    TransformRunRow,
)
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.repositories.status_cas import cas_status_update


class SqlAlchemyTransformRepository:
    """SQLAlchemy implementation of transform registry and transform run persistence."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def transform_by_api_name(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        api_name: str,
    ) -> TransformRow | None:
        row = (
            transaction.execute(
                select(db.transforms).where(
                    and_(
                        db.transforms.c.tenant_id == tenant_id,
                        db.transforms.c.api_name == api_name,
                    )
                )
            )
            .mappings()
            .first()
        )
        return cast(TransformRow, dict(row)) if row else None

    def insert_transform(self, *, transaction: Any, record: TransformRecord) -> None:
        transaction.execute(
            insert(db.transforms).values(
                id=record.transform_id,
                tenant_id=record.tenant_id,
                api_name=record.api_name,
                language=record.language,
                entrypoint=record.entrypoint,
                mode=record.mode,
                inputs=record.inputs,
                output_dataset_ref=record.output_dataset_ref,
                checks=record.checks,
            )
        )

    def update_transform_definition(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        transform_id: str,
        language: str,
        entrypoint: str,
        mode: str,
        inputs: dict[str, str],
        output_dataset_ref: str,
        checks: Sequence[TransformCheck],
    ) -> None:
        transaction.execute(
            update(db.transforms)
            .where(and_(db.transforms.c.tenant_id == tenant_id, db.transforms.c.id == transform_id))
            .values(
                language=language,
                entrypoint=entrypoint,
                mode=mode,
                inputs=inputs,
                output_dataset_ref=output_dataset_ref,
                checks=[dict(check) for check in checks],
            )
        )

    def transform_by_id(self, *, transaction: Any, transform_id: str) -> TransformRow | None:
        row = transaction.execute(select(db.transforms).where(db.transforms.c.id == transform_id)).mappings().first()
        return cast(TransformRow, dict(row)) if row else None

    def list_transforms(self, *, transaction: Any, tenant_id: str) -> list[TransformRow]:
        rows = transaction.execute(
            select(db.transforms).where(db.transforms.c.tenant_id == tenant_id).order_by(db.transforms.c.api_name)
        ).mappings()
        return [cast(TransformRow, dict(row)) for row in rows]

    def transform_run_by_id(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        transform_run_id: str,
    ) -> TransformRunRow | None:
        row = (
            transaction.execute(
                select(db.transform_runs).where(
                    and_(db.transform_runs.c.tenant_id == tenant_id, db.transform_runs.c.id == transform_run_id)
                )
            )
            .mappings()
            .first()
        )
        return cast(TransformRunRow, dict(row)) if row else None

    def latest_successful_transform_run(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        transform_id: str,
    ) -> TransformRunRow | None:
        row = (
            transaction.execute(
                select(db.transform_runs)
                .where(
                    and_(
                        db.transform_runs.c.tenant_id == tenant_id,
                        db.transform_runs.c.transform_id == transform_id,
                        db.transform_runs.c.status == "SUCCESS",
                    )
                )
                .order_by(desc(db.transform_runs.c.completed_at), desc(db.transform_runs.c.created_at))
                .limit(1)
            )
            .mappings()
            .first()
        )
        return cast(TransformRunRow, dict(row)) if row else None

    def running_transform_run(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        transform_id: str,
    ) -> TransformRunRow | None:
        row = (
            transaction.execute(
                select(db.transform_runs)
                .where(
                    and_(
                        db.transform_runs.c.tenant_id == tenant_id,
                        db.transform_runs.c.transform_id == transform_id,
                        db.transform_runs.c.status == "RUNNING",
                    )
                )
                .order_by(desc(db.transform_runs.c.created_at))
                .limit(1)
            )
            .mappings()
            .first()
        )
        return cast(TransformRunRow, dict(row)) if row else None

    def insert_transform_run(self, *, transaction: Any, record: TransformRunRecord) -> None:
        transaction.execute(
            insert(db.transform_runs).values(
                id=record.transform_run_id,
                tenant_id=record.tenant_id,
                transform_id=record.transform_id,
                status=record.status,
                input_versions=record.input_versions,
                definition_snapshot=record.definition_snapshot,
                output_version_id=record.output_version_id,
                transaction_id=record.transaction_id,
                error=record.error,
                created_at=record.created_at,
                completed_at=record.completed_at,
            )
        )

    def update_transform_run_terminal(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        transform_run_id: str,
        transition: StatusTransition,
        output_version_id: str | None,
        error: Mapping[str, object] | None,
        completed_at: str,
    ) -> bool:
        return cas_status_update(
            transaction,
            db.transform_runs,
            tenant_id=tenant_id,
            row_id=transform_run_id,
            transition=transition,
            values={
                "output_version_id": output_version_id,
                "error": error,
                "completed_at": completed_at,
            },
        )
