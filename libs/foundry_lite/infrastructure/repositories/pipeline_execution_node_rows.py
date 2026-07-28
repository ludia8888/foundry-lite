"""SQLAlchemy row operations for Pipeline node, attempt, and artifact evidence."""

from __future__ import annotations

from typing import Any, cast

from sqlalchemy import and_, insert, select
from sqlalchemy.exc import IntegrityError

from foundry_lite.application.ports.pipeline_execution_repository import (
    PipelineNodeAttemptRecord,
    PipelineNodeAttemptRow,
    PipelineNodeRunRecord,
    PipelineNodeRunRow,
    PipelineRunArtifactRecord,
    PipelineRunArtifactRow,
)
from foundry_lite.application.state_transitions import PIPELINE_NODE_RUN_RUNNING, StatusTransition
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.repositories.status_cas import cas_status_update


def insert_node_run(transaction: Any, record: PipelineNodeRunRecord) -> PipelineNodeRunRow:
    savepoint = transaction.begin_nested()
    try:
        transaction.execute(
            insert(db.pipeline_node_runs).values(
                id=record.node_run_id,
                tenant_id=record.tenant_id,
                run_id=record.run_id,
                node_id=record.node_id,
                descriptor_id=record.descriptor_id,
                spec_version=record.spec_version,
                status="PENDING",
                attempt_count=0,
                input_artifacts=record.input_artifacts,
                output_artifacts=[],
                error=None,
                started_at=None,
                completed_at=None,
                created_at=record.created_at,
                updated_at=record.created_at,
            )
        )
    except IntegrityError:
        savepoint.rollback()
        existing = node_run_by_run_node(transaction, record.tenant_id, record.run_id, record.node_id)
        if existing is not None:
            return existing
        raise
    savepoint.commit()
    return required_node_run(transaction, record.tenant_id, record.node_run_id)


def node_runs_for_run(transaction: Any, tenant_id: str, run_id: str) -> list[PipelineNodeRunRow]:
    rows = (
        transaction.execute(
            select(db.pipeline_node_runs)
            .where(and_(db.pipeline_node_runs.c.tenant_id == tenant_id, db.pipeline_node_runs.c.run_id == run_id))
            .order_by(db.pipeline_node_runs.c.created_at, db.pipeline_node_runs.c.id)
        )
        .mappings()
        .all()
    )
    return [_cast_row(row, PipelineNodeRunRow) for row in rows]


def node_run_by_run_node(
    transaction: Any,
    tenant_id: str,
    run_id: str,
    node_id: str,
) -> PipelineNodeRunRow | None:
    row = (
        transaction.execute(
            select(db.pipeline_node_runs).where(
                and_(
                    db.pipeline_node_runs.c.tenant_id == tenant_id,
                    db.pipeline_node_runs.c.run_id == run_id,
                    db.pipeline_node_runs.c.node_id == node_id,
                )
            )
        )
        .mappings()
        .first()
    )
    return _optional_row(row, PipelineNodeRunRow)


def claim_node_run(
    transaction: Any,
    tenant_id: str,
    node_run_id: str,
    attempt_number: int,
    input_artifacts: list[dict[str, object]],
    started_at: str,
    updated_at: str,
) -> PipelineNodeRunRow | None:
    updated = cas_status_update(
        transaction,
        db.pipeline_node_runs,
        tenant_id=tenant_id,
        row_id=node_run_id,
        transition=PIPELINE_NODE_RUN_RUNNING,
        values={
            "attempt_count": attempt_number,
            "input_artifacts": input_artifacts,
            "started_at": started_at,
            "updated_at": updated_at,
        },
    )
    return required_node_run(transaction, tenant_id, node_run_id) if updated else None


def update_node_run_terminal(
    transaction: Any,
    tenant_id: str,
    node_run_id: str,
    transition: StatusTransition,
    output_artifacts: list[dict[str, object]],
    error: dict[str, object] | None,
    completed_at: str,
    updated_at: str,
) -> PipelineNodeRunRow | None:
    updated = cas_status_update(
        transaction,
        db.pipeline_node_runs,
        tenant_id=tenant_id,
        row_id=node_run_id,
        transition=transition,
        values={
            "output_artifacts": output_artifacts,
            "error": error,
            "completed_at": completed_at,
            "updated_at": updated_at,
        },
    )
    return required_node_run(transaction, tenant_id, node_run_id) if updated else None


def insert_node_attempt(transaction: Any, record: PipelineNodeAttemptRecord) -> PipelineNodeAttemptRow:
    savepoint = transaction.begin_nested()
    try:
        transaction.execute(
            insert(db.pipeline_node_attempts).values(
                id=record.attempt_id,
                tenant_id=record.tenant_id,
                node_run_id=record.node_run_id,
                attempt_number=record.attempt_number,
                status="RUNNING",
                executor_profile=record.executor_profile,
                lease_owner=None,
                lease_token=None,
                lease_expires_at=None,
                input_manifest=record.input_manifest,
                output_manifest={},
                error=None,
                started_at=record.started_at,
                completed_at=None,
            )
        )
    except IntegrityError:
        savepoint.rollback()
        existing = attempt_by_number(transaction, record.tenant_id, record.node_run_id, record.attempt_number)
        if existing is not None:
            return existing
        raise
    savepoint.commit()
    return required_attempt(transaction, record.tenant_id, record.attempt_id)


def attempts_for_node_run(transaction: Any, tenant_id: str, node_run_id: str) -> list[PipelineNodeAttemptRow]:
    rows = (
        transaction.execute(
            select(db.pipeline_node_attempts)
            .where(
                and_(
                    db.pipeline_node_attempts.c.tenant_id == tenant_id,
                    db.pipeline_node_attempts.c.node_run_id == node_run_id,
                )
            )
            .order_by(db.pipeline_node_attempts.c.attempt_number)
        )
        .mappings()
        .all()
    )
    return [_cast_row(row, PipelineNodeAttemptRow) for row in rows]


def attempt_by_number(
    transaction: Any,
    tenant_id: str,
    node_run_id: str,
    attempt_number: int,
) -> PipelineNodeAttemptRow | None:
    row = (
        transaction.execute(
            select(db.pipeline_node_attempts).where(
                and_(
                    db.pipeline_node_attempts.c.tenant_id == tenant_id,
                    db.pipeline_node_attempts.c.node_run_id == node_run_id,
                    db.pipeline_node_attempts.c.attempt_number == attempt_number,
                )
            )
        )
        .mappings()
        .first()
    )
    return _optional_row(row, PipelineNodeAttemptRow)


def update_node_attempt_terminal(
    transaction: Any,
    tenant_id: str,
    attempt_id: str,
    transition: StatusTransition,
    output_manifest: dict[str, object],
    error: dict[str, object] | None,
    completed_at: str,
) -> PipelineNodeAttemptRow | None:
    updated = cas_status_update(
        transaction,
        db.pipeline_node_attempts,
        tenant_id=tenant_id,
        row_id=attempt_id,
        transition=transition,
        values={"output_manifest": output_manifest, "error": error, "completed_at": completed_at},
    )
    return required_attempt(transaction, tenant_id, attempt_id) if updated else None


def insert_artifact(transaction: Any, record: PipelineRunArtifactRecord) -> PipelineRunArtifactRow:
    savepoint = transaction.begin_nested()
    try:
        transaction.execute(
            insert(db.pipeline_run_artifacts).values(
                id=record.artifact_id,
                tenant_id=record.tenant_id,
                run_id=record.run_id,
                node_run_id=record.node_run_id,
                node_id=record.node_id,
                port_id=record.port_id,
                artifact_kind=record.artifact_kind,
                plane=record.plane,
                artifact_ref=record.artifact_ref,
                manifest=record.manifest,
                content_fingerprint=record.content_fingerprint,
                security_envelope=record.security_envelope,
                status=record.status,
                is_serving=record.is_serving,
                idempotency_key=record.idempotency_key,
                committed_at=record.committed_at,
                created_at=record.created_at,
            )
        )
    except IntegrityError:
        savepoint.rollback()
        existing = artifact_by_key(transaction, record.tenant_id, record.idempotency_key)
        if existing is not None:
            return existing
        raise
    savepoint.commit()
    return required_artifact(transaction, record.tenant_id, record.artifact_id)


def artifacts_for_run(transaction: Any, tenant_id: str, run_id: str) -> list[PipelineRunArtifactRow]:
    rows = (
        transaction.execute(
            select(db.pipeline_run_artifacts)
            .where(
                and_(
                    db.pipeline_run_artifacts.c.tenant_id == tenant_id,
                    db.pipeline_run_artifacts.c.run_id == run_id,
                )
            )
            .order_by(db.pipeline_run_artifacts.c.created_at, db.pipeline_run_artifacts.c.id)
        )
        .mappings()
        .all()
    )
    return [_cast_row(row, PipelineRunArtifactRow) for row in rows]


def artifact_by_key(transaction: Any, tenant_id: str, idempotency_key: str) -> PipelineRunArtifactRow | None:
    row = (
        transaction.execute(
            select(db.pipeline_run_artifacts).where(
                and_(
                    db.pipeline_run_artifacts.c.tenant_id == tenant_id,
                    db.pipeline_run_artifacts.c.idempotency_key == idempotency_key,
                )
            )
        )
        .mappings()
        .first()
    )
    return _optional_row(row, PipelineRunArtifactRow)


def required_node_run(transaction: Any, tenant_id: str, node_run_id: str) -> PipelineNodeRunRow:
    row = (
        transaction.execute(
            select(db.pipeline_node_runs).where(
                and_(db.pipeline_node_runs.c.tenant_id == tenant_id, db.pipeline_node_runs.c.id == node_run_id)
            )
        )
        .mappings()
        .one()
    )
    return _cast_row(row, PipelineNodeRunRow)


def required_attempt(transaction: Any, tenant_id: str, attempt_id: str) -> PipelineNodeAttemptRow:
    row = (
        transaction.execute(
            select(db.pipeline_node_attempts).where(
                and_(
                    db.pipeline_node_attempts.c.tenant_id == tenant_id,
                    db.pipeline_node_attempts.c.id == attempt_id,
                )
            )
        )
        .mappings()
        .one()
    )
    return _cast_row(row, PipelineNodeAttemptRow)


def required_artifact(transaction: Any, tenant_id: str, artifact_id: str) -> PipelineRunArtifactRow:
    row = (
        transaction.execute(
            select(db.pipeline_run_artifacts).where(
                and_(
                    db.pipeline_run_artifacts.c.tenant_id == tenant_id,
                    db.pipeline_run_artifacts.c.id == artifact_id,
                )
            )
        )
        .mappings()
        .one()
    )
    return _cast_row(row, PipelineRunArtifactRow)


def _optional_row[RowT](row: Any, row_type: type[RowT]) -> RowT | None:
    return _cast_row(row, row_type) if row is not None else None


def _cast_row[RowT](row: Any, row_type: type[RowT]) -> RowT:
    del row_type
    return cast(RowT, dict(row))
