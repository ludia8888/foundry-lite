"""SQLAlchemy adapter for Pipeline Builder v2 execution evidence."""

from __future__ import annotations

from typing import Any, cast

from sqlalchemy import and_, desc, func, insert, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

import foundry_lite.infrastructure.repositories.pipeline_execution_node_rows as node_rows
from foundry_lite.application.ports.pipeline_execution_repository import (
    PipelineDeploymentRecord,
    PipelineDeploymentRow,
    PipelineNodeAttemptRecord,
    PipelineNodeAttemptRow,
    PipelineNodeRunRecord,
    PipelineNodeRunRow,
    PipelinePreviewRunRecord,
    PipelinePreviewRunRow,
    PipelineRunArtifactRecord,
    PipelineRunArtifactRow,
)
from foundry_lite.application.state_transitions import (
    PIPELINE_PREVIEW_CANCEL_REQUESTED,
    PIPELINE_PREVIEW_RUNNING,
    StatusTransition,
)
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.repositories.status_cas import cas_status_update

_DEPLOYMENT_NUMBER_RETRY_LIMIT = 8


class SqlAlchemyPipelineExecutionRepository:
    """Tenant-scoped preview, node-attempt, and artifact rows."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def insert_preview(self, *, transaction: Any, record: PipelinePreviewRunRecord) -> PipelinePreviewRunRow:
        savepoint = transaction.begin_nested()
        try:
            transaction.execute(
                insert(db.pipeline_preview_runs).values(
                    id=record.preview_run_id,
                    tenant_id=record.tenant_id,
                    pipeline_id=record.pipeline_id,
                    branch_id=record.branch_id,
                    status="QUEUED",
                    graph=record.graph,
                    graph_fingerprint=record.graph_fingerprint,
                    target_node_id=record.target_node_id,
                    limits=record.limits,
                    outputs=[],
                    artifacts=[],
                    idempotency_key=record.idempotency_key,
                    request_fingerprint=record.request_fingerprint,
                    is_commit_forbidden=True,
                    cancel_requested_at=None,
                    error=None,
                    created_by=record.created_by,
                    created_at=record.created_at,
                    started_at=None,
                    completed_at=None,
                )
            )
        except IntegrityError:
            savepoint.rollback()
            existing = self.preview_by_idempotency_key(
                transaction=transaction,
                tenant_id=record.tenant_id,
                idempotency_key=record.idempotency_key,
            )
            if existing is not None:
                return existing
            raise
        savepoint.commit()
        row = self.preview_by_id(
            transaction=transaction,
            tenant_id=record.tenant_id,
            preview_run_id=record.preview_run_id,
        )
        assert row is not None
        return row

    def preview_by_id(self, *, transaction: Any, tenant_id: str, preview_run_id: str) -> PipelinePreviewRunRow | None:
        row = (
            transaction.execute(
                select(db.pipeline_preview_runs).where(
                    and_(
                        db.pipeline_preview_runs.c.tenant_id == tenant_id,
                        db.pipeline_preview_runs.c.id == preview_run_id,
                    )
                )
            )
            .mappings()
            .first()
        )
        return _row(row, PipelinePreviewRunRow)

    def preview_by_idempotency_key(
        self, *, transaction: Any, tenant_id: str, idempotency_key: str
    ) -> PipelinePreviewRunRow | None:
        row = (
            transaction.execute(
                select(db.pipeline_preview_runs).where(
                    and_(
                        db.pipeline_preview_runs.c.tenant_id == tenant_id,
                        db.pipeline_preview_runs.c.idempotency_key == idempotency_key,
                    )
                )
            )
            .mappings()
            .first()
        )
        return _row(row, PipelinePreviewRunRow)

    def claim_preview(
        self, *, transaction: Any, tenant_id: str, preview_run_id: str, started_at: str
    ) -> PipelinePreviewRunRow | None:
        updated = cas_status_update(
            transaction,
            db.pipeline_preview_runs,
            tenant_id=tenant_id,
            row_id=preview_run_id,
            transition=PIPELINE_PREVIEW_RUNNING,
            values={"started_at": started_at},
        )
        if not updated:
            return None
        return self.preview_by_id(transaction=transaction, tenant_id=tenant_id, preview_run_id=preview_run_id)

    def update_preview_terminal(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        preview_run_id: str,
        transition: StatusTransition,
        outputs: list[dict[str, object]],
        artifacts: list[dict[str, object]],
        error: dict[str, object] | None,
        completed_at: str,
    ) -> PipelinePreviewRunRow | None:
        updated = cas_status_update(
            transaction,
            db.pipeline_preview_runs,
            tenant_id=tenant_id,
            row_id=preview_run_id,
            transition=transition,
            values={"outputs": outputs, "artifacts": artifacts, "error": error, "completed_at": completed_at},
        )
        if not updated:
            return None
        return self.preview_by_id(transaction=transaction, tenant_id=tenant_id, preview_run_id=preview_run_id)

    def request_preview_cancel(
        self, *, transaction: Any, tenant_id: str, preview_run_id: str, requested_at: str
    ) -> PipelinePreviewRunRow | None:
        updated = cas_status_update(
            transaction,
            db.pipeline_preview_runs,
            tenant_id=tenant_id,
            row_id=preview_run_id,
            transition=PIPELINE_PREVIEW_CANCEL_REQUESTED,
            values={"cancel_requested_at": requested_at},
        )
        if not updated:
            return None
        return self.preview_by_id(transaction=transaction, tenant_id=tenant_id, preview_run_id=preview_run_id)

    def insert_node_run(self, *, transaction: Any, record: PipelineNodeRunRecord) -> PipelineNodeRunRow:
        return node_rows.insert_node_run(transaction, record)

    def node_runs_for_run(self, *, transaction: Any, tenant_id: str, run_id: str) -> list[PipelineNodeRunRow]:
        return node_rows.node_runs_for_run(transaction, tenant_id, run_id)

    def node_run_by_run_node(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        run_id: str,
        node_id: str,
    ) -> PipelineNodeRunRow | None:
        return node_rows.node_run_by_run_node(transaction, tenant_id, run_id, node_id)

    def claim_node_run(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        node_run_id: str,
        attempt_number: int,
        input_artifacts: list[dict[str, object]],
        started_at: str,
        updated_at: str,
    ) -> PipelineNodeRunRow | None:
        return node_rows.claim_node_run(
            transaction,
            tenant_id,
            node_run_id,
            attempt_number,
            input_artifacts,
            started_at,
            updated_at,
        )

    def update_node_run_terminal(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        node_run_id: str,
        transition: StatusTransition,
        output_artifacts: list[dict[str, object]],
        error: dict[str, object] | None,
        completed_at: str,
        updated_at: str,
    ) -> PipelineNodeRunRow | None:
        return node_rows.update_node_run_terminal(
            transaction,
            tenant_id,
            node_run_id,
            transition,
            output_artifacts,
            error,
            completed_at,
            updated_at,
        )

    def insert_node_attempt(self, *, transaction: Any, record: PipelineNodeAttemptRecord) -> PipelineNodeAttemptRow:
        return node_rows.insert_node_attempt(transaction, record)

    def attempts_for_node_run(
        self, *, transaction: Any, tenant_id: str, node_run_id: str
    ) -> list[PipelineNodeAttemptRow]:
        return node_rows.attempts_for_node_run(transaction, tenant_id, node_run_id)

    def attempt_by_number(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        node_run_id: str,
        attempt_number: int,
    ) -> PipelineNodeAttemptRow | None:
        return node_rows.attempt_by_number(transaction, tenant_id, node_run_id, attempt_number)

    def update_node_attempt_terminal(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        attempt_id: str,
        transition: StatusTransition,
        output_manifest: dict[str, object],
        error: dict[str, object] | None,
        completed_at: str,
    ) -> PipelineNodeAttemptRow | None:
        return node_rows.update_node_attempt_terminal(
            transaction,
            tenant_id,
            attempt_id,
            transition,
            output_manifest,
            error,
            completed_at,
        )

    def insert_artifact(self, *, transaction: Any, record: PipelineRunArtifactRecord) -> PipelineRunArtifactRow:
        return node_rows.insert_artifact(transaction, record)

    def artifacts_for_run(self, *, transaction: Any, tenant_id: str, run_id: str) -> list[PipelineRunArtifactRow]:
        return node_rows.artifacts_for_run(transaction, tenant_id, run_id)

    def artifact_by_idempotency_key(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        idempotency_key: str,
    ) -> PipelineRunArtifactRow | None:
        return node_rows.artifact_by_key(transaction, tenant_id, idempotency_key)

    def insert_deployment(self, *, transaction: Any, record: PipelineDeploymentRecord) -> PipelineDeploymentRow:
        for attempt in range(_DEPLOYMENT_NUMBER_RETRY_LIMIT):
            deployment_number = self._next_deployment_number(transaction, record.tenant_id, record.pipeline_id)
            savepoint = transaction.begin_nested()
            try:
                self._insert_deployment_row(transaction, record, deployment_number)
            except IntegrityError:
                savepoint.rollback()
                existing = self.deployment_by_idempotency_key(
                    transaction=transaction,
                    tenant_id=record.tenant_id,
                    idempotency_key=record.idempotency_key,
                )
                if existing is not None:
                    return existing
                if attempt + 1 >= _DEPLOYMENT_NUMBER_RETRY_LIMIT or not self._deployment_number_exists(
                    transaction,
                    record.tenant_id,
                    record.pipeline_id,
                    deployment_number,
                ):
                    raise
                continue
            savepoint.commit()
            return self._deployment(transaction, record.tenant_id, record.deployment_id)
        raise RuntimeError("pipeline deployment number retry loop exhausted")

    def _insert_deployment_row(
        self,
        transaction: Any,
        record: PipelineDeploymentRecord,
        deployment_number: int,
    ) -> None:
        transaction.execute(
            insert(db.pipeline_deployments).values(
                id=record.deployment_id,
                tenant_id=record.tenant_id,
                pipeline_id=record.pipeline_id,
                version_id=record.version_id,
                deployment_number=deployment_number,
                status=record.status,
                execution_plan=record.execution_plan,
                plan_fingerprint=record.plan_fingerprint,
                compiler_version=record.compiler_version,
                processor_pins=record.processor_pins,
                model_pins=record.model_pins,
                function_pins=record.function_pins,
                compute_profile=record.compute_profile,
                idempotency_key=record.idempotency_key,
                request_fingerprint=record.request_fingerprint,
                promoted_by=record.promoted_by,
                promoted_at=record.promoted_at,
                rolled_back_from_id=record.rolled_back_from_id,
                created_by=record.created_by,
                created_at=record.created_at,
            )
        )

    def deployment_by_idempotency_key(
        self, *, transaction: Any, tenant_id: str, idempotency_key: str
    ) -> PipelineDeploymentRow | None:
        row = (
            transaction.execute(
                select(db.pipeline_deployments).where(
                    and_(
                        db.pipeline_deployments.c.tenant_id == tenant_id,
                        db.pipeline_deployments.c.idempotency_key == idempotency_key,
                    )
                )
            )
            .mappings()
            .first()
        )
        return _row(row, PipelineDeploymentRow)

    def list_deployments(
        self, *, transaction: Any, tenant_id: str, pipeline_id: str, limit: int
    ) -> list[PipelineDeploymentRow]:
        rows = (
            transaction.execute(
                select(db.pipeline_deployments)
                .where(
                    and_(
                        db.pipeline_deployments.c.tenant_id == tenant_id,
                        db.pipeline_deployments.c.pipeline_id == pipeline_id,
                    )
                )
                .order_by(desc(db.pipeline_deployments.c.deployment_number))
                .limit(limit)
            )
            .mappings()
            .all()
        )
        return [_cast_row(row, PipelineDeploymentRow) for row in rows]

    def _next_deployment_number(self, transaction: Any, tenant_id: str, pipeline_id: str) -> int:
        value = transaction.execute(
            select(func.max(db.pipeline_deployments.c.deployment_number)).where(
                and_(
                    db.pipeline_deployments.c.tenant_id == tenant_id,
                    db.pipeline_deployments.c.pipeline_id == pipeline_id,
                )
            )
        ).scalar_one()
        return int(value or 0) + 1

    def _deployment_number_exists(
        self,
        transaction: Any,
        tenant_id: str,
        pipeline_id: str,
        deployment_number: int,
    ) -> bool:
        row = transaction.execute(
            select(db.pipeline_deployments.c.id).where(
                and_(
                    db.pipeline_deployments.c.tenant_id == tenant_id,
                    db.pipeline_deployments.c.pipeline_id == pipeline_id,
                    db.pipeline_deployments.c.deployment_number == deployment_number,
                )
            )
        ).first()
        return row is not None

    def _deployment(self, transaction: Any, tenant_id: str, deployment_id: str) -> PipelineDeploymentRow:
        row = (
            transaction.execute(
                select(db.pipeline_deployments).where(
                    and_(
                        db.pipeline_deployments.c.tenant_id == tenant_id,
                        db.pipeline_deployments.c.id == deployment_id,
                    )
                )
            )
            .mappings()
            .one()
        )
        return _cast_row(row, PipelineDeploymentRow)


def _row[RowT](row: Any, row_type: type[RowT]) -> RowT | None:
    if row is None:
        return None
    return _cast_row(row, row_type)


def _cast_row[RowT](row: Any, row_type: type[RowT]) -> RowT:
    del row_type
    return cast(RowT, dict(row))
