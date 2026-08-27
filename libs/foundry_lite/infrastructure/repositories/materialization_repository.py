"""SQLAlchemy repository adapter for materialization repository persistence."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

from sqlalchemy import and_, desc, func, insert, or_, select
from sqlalchemy.engine import Engine

from foundry_lite.application.ports.materialization_repository import (
    ActionLogSourceRow,
    ActionRunWatermark,
    MaterializationRecord,
    MaterializationRow,
    MaterializationRunRecord,
    ObjectRecordVersionRow,
)
from foundry_lite.application.ports.transaction_context import StatusTransition
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.repositories.status_cas import cas_status_update


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

    def latest_succeeded_materialization_run(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        materialization_id: str,
    ) -> dict[str, Any] | None:
        row = (
            transaction.execute(
                select(db.materialization_runs)
                .where(
                    and_(
                        db.materialization_runs.c.tenant_id == tenant_id,
                        db.materialization_runs.c.materialization_id == materialization_id,
                        db.materialization_runs.c.status == "succeeded",
                        db.materialization_runs.c.target_dataset_version_id.is_not(None),
                    )
                )
                .order_by(desc(db.materialization_runs.c.completed_at), desc(db.materialization_runs.c.id))
                .limit(1)
            )
            .mappings()
            .first()
        )
        return dict(row) if row is not None else None

    def update_materialization_run_terminal(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        materialization_run_id: str,
        transition: StatusTransition,
        target_dataset_version_id: str | None,
        row_count: int | None,
        error: Mapping[str, object] | None,
        completed_at: str,
    ) -> bool:
        return cas_status_update(
            transaction,
            db.materialization_runs,
            tenant_id=tenant_id,
            row_id=materialization_run_id,
            transition=transition,
            values={
                "target_dataset_version_id": target_dataset_version_id,
                "row_count": row_count,
                "error": error,
                "completed_at": completed_at,
            },
        )

    def latest_action_run_watermark(self, *, transaction: Any, tenant_id: str) -> ActionRunWatermark:
        row = (
            transaction.execute(
                select(db.action_runs.c.completed_at, db.action_runs.c.id)
                .where(and_(db.action_runs.c.tenant_id == tenant_id, db.action_runs.c.completed_at.is_not(None)))
                .order_by(db.action_runs.c.completed_at.desc(), db.action_runs.c.id.desc())
                .limit(1)
            )
            .mappings()
            .first()
        )
        if row is None:
            return {"completed_at": None, "action_run_id": None}
        return {"completed_at": str(row["completed_at"]), "action_run_id": str(row["id"])}

    def action_log_rows_at_watermark(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        completed_at_lte: str | None,
        action_run_id_lte: str | None,
    ) -> list[ActionLogSourceRow]:
        if completed_at_lte is None or action_run_id_lte is None:
            return []
        query = (
            select(
                db.action_runs.c.id.label("action_run_id"),
                db.action_runs.c.actor_user_id,
                db.action_runs.c.action_type_api_name,
                db.action_runs.c.target_object_type_api_name,
                db.action_runs.c.target_object_id,
                db.action_runs.c.status,
                db.action_runs.c.parameters,
                db.action_runs.c.created_at,
                db.object_edits.c.patch.label("edit_patch"),
            )
            .select_from(
                db.action_runs.outerjoin(
                    db.object_edits,
                    and_(
                        db.object_edits.c.tenant_id == db.action_runs.c.tenant_id,
                        db.object_edits.c.action_run_id == db.action_runs.c.id,
                    ),
                )
            )
            .where(
                and_(
                    db.action_runs.c.tenant_id == tenant_id,
                    db.action_runs.c.status == "succeeded",
                    db.action_runs.c.completed_at.is_not(None),
                    or_(
                        db.action_runs.c.completed_at < completed_at_lte,
                        and_(
                            db.action_runs.c.completed_at == completed_at_lte,
                            db.action_runs.c.id <= action_run_id_lte,
                        ),
                    ),
                )
            )
            .order_by(db.action_runs.c.completed_at, db.action_runs.c.id)
        )
        rows = transaction.execute(query).mappings().all()
        return [cast(ActionLogSourceRow, dict(row)) for row in rows]

    def latest_object_record_watermark(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        object_type_api_name: str,
        index_version: str,
    ) -> int | None:
        value = transaction.execute(
            select(func.max(db.object_records.c.object_change_sequence)).where(
                and_(
                    db.object_records.c.tenant_id == tenant_id,
                    db.object_records.c.object_type_api_name == object_type_api_name,
                    db.object_records.c.index_version == index_version,
                    db.object_records.c.is_active == True,  # noqa: E712
                )
            )
        ).scalar()
        return int(value) if value is not None else None

    def active_object_index_version(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        object_type_api_name: str,
    ) -> str:
        pointer = self._active_index_pointer(transaction, tenant_id, object_type_api_name)
        if pointer is not None:
            return pointer
        return self._legacy_active_index_version(transaction, tenant_id, object_type_api_name)

    def _active_index_pointer(
        self,
        transaction: Any,
        tenant_id: str,
        object_type_api_name: str,
    ) -> str | None:
        row = (
            transaction.execute(
                select(db.object_index_versions.c.active_index_version)
                .select_from(
                    db.object_index_versions.join(
                        db.object_types,
                        and_(
                            db.object_types.c.tenant_id == db.object_index_versions.c.tenant_id,
                            db.object_types.c.id == db.object_index_versions.c.object_type_id,
                        ),
                    )
                )
                .where(
                    and_(
                        db.object_index_versions.c.tenant_id == tenant_id,
                        db.object_types.c.api_name == object_type_api_name,
                    )
                )
            )
            .mappings()
            .first()
        )
        return str(row["active_index_version"]) if row else None

    def _legacy_active_index_version(self, transaction: Any, tenant_id: str, object_type_api_name: str) -> str:
        row = (
            transaction.execute(
                select(db.object_records.c.index_version)
                .where(
                    and_(
                        db.object_records.c.tenant_id == tenant_id,
                        db.object_records.c.object_type_api_name == object_type_api_name,
                        db.object_records.c.is_active == True,  # noqa: E712
                    )
                )
                .order_by(db.object_records.c.updated_at.desc(), db.object_records.c.index_version)
                .limit(1)
            )
            .mappings()
            .first()
        )
        return str(row["index_version"]) if row else "active"

    def object_records_at_watermark(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        object_type_api_name: str,
        index_version: str,
        max_object_change_sequence: int,
    ) -> list[ObjectRecordVersionRow]:
        latest = (
            select(
                db.object_record_versions.c.object_type_id,
                db.object_record_versions.c.object_id,
                db.object_record_versions.c.index_version,
                func.max(db.object_record_versions.c.object_change_sequence).label("max_sequence"),
            )
            .where(
                and_(
                    db.object_record_versions.c.tenant_id == tenant_id,
                    db.object_record_versions.c.object_type_api_name == object_type_api_name,
                    db.object_record_versions.c.index_version == index_version,
                    db.object_record_versions.c.object_change_sequence <= max_object_change_sequence,
                )
            )
            .group_by(
                db.object_record_versions.c.object_type_id,
                db.object_record_versions.c.object_id,
                db.object_record_versions.c.index_version,
            )
            .subquery()
        )
        rows = transaction.execute(_object_record_versions_at_watermark_statement(tenant_id, latest)).mappings().all()
        return [cast(ObjectRecordVersionRow, dict(row)) for row in rows]


def _object_record_versions_at_watermark_statement(tenant_id: str, latest: Any) -> Any:
    return (
        select(db.object_record_versions)
        .join(
            latest,
            and_(
                db.object_record_versions.c.object_type_id == latest.c.object_type_id,
                db.object_record_versions.c.object_id == latest.c.object_id,
                db.object_record_versions.c.index_version == latest.c.index_version,
                db.object_record_versions.c.object_change_sequence == latest.c.max_sequence,
            ),
        )
        .where(
            and_(
                db.object_record_versions.c.tenant_id == tenant_id,
                db.object_record_versions.c.is_active == True,  # noqa: E712
                db.object_record_versions.c.deleted == False,  # noqa: E712
            )
        )
        .order_by(db.object_record_versions.c.object_id)
    )
