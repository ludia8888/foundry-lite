"""SQLAlchemy adapter for Pipeline Builder v2 execution evidence."""

from __future__ import annotations

from typing import Any, cast

from sqlalchemy import and_, desc, func, insert, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

import foundry_lite.infrastructure.repositories.pipeline_execution_node_rows as node_rows
import foundry_lite.infrastructure.repositories.pipeline_preview_rows as preview_rows
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
from foundry_lite.application.state_transitions import StatusTransition
from foundry_lite.infrastructure import schema as db

_DEPLOYMENT_NUMBER_RETRY_LIMIT = 8


class SqlAlchemyPipelineExecutionRepository:
    """Tenant-scoped preview, node-attempt, and artifact rows."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def insert_preview(self, *, transaction: Any, record: PipelinePreviewRunRecord) -> PipelinePreviewRunRow:
        return preview_rows.insert_preview(transaction, record)

    def preview_by_id(self, *, transaction: Any, tenant_id: str, preview_run_id: str) -> PipelinePreviewRunRow | None:
        return preview_rows.preview_by_id(transaction, tenant_id, preview_run_id)

    def preview_by_idempotency_key(
        self, *, transaction: Any, tenant_id: str, idempotency_key: str
    ) -> PipelinePreviewRunRow | None:
        return preview_rows.preview_by_idempotency_key(transaction, tenant_id, idempotency_key)

    def claim_preview(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        preview_run_id: str,
        started_at: str,
        execution_lease_token: str,
        execution_lease_expires_at: str,
        execution_heartbeat_at: str,
    ) -> PipelinePreviewRunRow | None:
        return preview_rows.claim_preview(
            transaction,
            tenant_id,
            preview_run_id,
            started_at,
            execution_lease_token,
            execution_lease_expires_at,
            execution_heartbeat_at,
        )

    def reclaim_expired_preview(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        preview_run_id: str,
        reclaim_before: str,
        execution_lease_token: str,
        execution_lease_expires_at: str,
        execution_heartbeat_at: str,
    ) -> PipelinePreviewRunRow | None:
        return preview_rows.reclaim_expired_preview(
            transaction,
            tenant_id,
            preview_run_id,
            reclaim_before,
            execution_lease_token,
            execution_lease_expires_at,
            execution_heartbeat_at,
        )

    def renew_preview_execution_lease(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        preview_run_id: str,
        execution_lease_token: str,
        execution_lease_expires_at: str,
        execution_heartbeat_at: str,
    ) -> PipelinePreviewRunRow | None:
        return preview_rows.renew_preview_execution_lease(
            transaction,
            tenant_id,
            preview_run_id,
            execution_lease_token,
            execution_lease_expires_at,
            execution_heartbeat_at,
        )

    def recoverable_previews(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        as_of: str,
        limit: int,
    ) -> list[PipelinePreviewRunRow]:
        return preview_rows.recoverable_previews(transaction, tenant_id, as_of, limit)

    def complete_preview_success(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        preview_run_id: str,
        execution_lease_token: str,
        outputs: list[dict[str, object]],
        artifacts: list[dict[str, object]],
        completed_at: str,
    ) -> PipelinePreviewRunRow | None:
        return preview_rows.complete_preview_success(
            transaction,
            tenant_id,
            preview_run_id,
            execution_lease_token,
            outputs,
            artifacts,
            completed_at,
        )

    def complete_preview_failure(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        preview_run_id: str,
        execution_lease_token: str,
        error: dict[str, object],
        completed_at: str,
    ) -> PipelinePreviewRunRow | None:
        return preview_rows.complete_preview_failure(
            transaction,
            tenant_id,
            preview_run_id,
            execution_lease_token,
            error,
            completed_at,
        )

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
        execution_lease_token: str | None,
    ) -> PipelinePreviewRunRow | None:
        return preview_rows.update_preview_terminal(
            transaction,
            tenant_id,
            preview_run_id,
            transition,
            outputs,
            artifacts,
            error,
            completed_at,
            execution_lease_token,
        )

    def request_preview_cancel(
        self, *, transaction: Any, tenant_id: str, preview_run_id: str, requested_at: str
    ) -> PipelinePreviewRunRow | None:
        return preview_rows.request_preview_cancel(transaction, tenant_id, preview_run_id, requested_at)

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

    def promoted_deployment_for_version(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        pipeline_id: str,
        version_id: str,
        plan_fingerprint: str,
    ) -> PipelineDeploymentRow | None:
        row = (
            transaction.execute(
                select(db.pipeline_deployments)
                .where(
                    and_(
                        db.pipeline_deployments.c.tenant_id == tenant_id,
                        db.pipeline_deployments.c.pipeline_id == pipeline_id,
                        db.pipeline_deployments.c.version_id == version_id,
                        db.pipeline_deployments.c.plan_fingerprint == plan_fingerprint,
                        db.pipeline_deployments.c.status == "PROMOTED",
                    )
                )
                .order_by(desc(db.pipeline_deployments.c.deployment_number))
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
