"""SQLAlchemy row operations for Pipeline node, attempt, and artifact evidence."""

from __future__ import annotations

from typing import Any, cast

from sqlalchemy import and_, desc, func, insert, select, update
from sqlalchemy.exc import IntegrityError

from foundry_lite.application.ports.pipeline_execution_repository import (
    PipelineNodeAttemptClaim,
    PipelineNodeAttemptRecord,
    PipelineNodeAttemptRow,
    PipelineNodeRunRecord,
    PipelineNodeRunRow,
    PipelineRunArtifactRecord,
    PipelineRunArtifactRow,
)
from foundry_lite.application.state_transitions import (
    PIPELINE_NODE_ATTEMPT_CANCELLED,
    PIPELINE_NODE_ATTEMPT_FAILED,
    PIPELINE_NODE_ATTEMPT_LOST,
    PIPELINE_NODE_ATTEMPT_SUCCEEDED,
    PIPELINE_NODE_RUN_ATTEMPT_CLAIMED,
    PIPELINE_NODE_RUN_CANCELLED,
    PIPELINE_NODE_RUN_FAILED,
    PIPELINE_NODE_RUN_RETRY_WAIT,
    PIPELINE_NODE_RUN_RUNNING,
    PIPELINE_NODE_RUN_SUCCEEDED,
    StatusTransition,
)
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.repositories.status_cas import (
    cas_status_update,
    cas_status_update_many,
)


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


def cancel_inactive_node_runs(
    transaction: Any,
    tenant_id: str,
    run_id: str,
    completed_at: str,
) -> int:
    return cas_status_update_many(
        transaction,
        db.pipeline_node_runs,
        tenant_id=tenant_id,
        transition=PIPELINE_NODE_RUN_CANCELLED,
        values={"completed_at": completed_at, "updated_at": completed_at},
        conditions=(db.pipeline_node_runs.c.run_id == run_id,),
    )


def cancel_active_node_attempt(
    transaction: Any,
    *,
    tenant_id: str,
    run_id: str,
    node_id: str,
    worker_id: str,
    external_execution_id: str,
    completed_at: str,
) -> PipelineNodeAttemptRow | None:
    node_run = _locked_node_run(transaction, tenant_id, run_id, node_id)
    if node_run is None or str(node_run["status"]) != "RUNNING":
        return None
    attempt = _latest_attempt(transaction, tenant_id, str(node_run["id"]))
    if attempt is None or not _is_cancel_owner(attempt, worker_id, external_execution_id):
        return None
    _cancel_attempt_row(transaction, attempt, completed_at)
    _cancel_node_row(transaction, node_run, completed_at)
    return required_attempt(transaction, tenant_id, str(attempt["id"]))


def active_node_run_count(
    transaction: Any,
    tenant_id: str,
    run_id: str,
) -> int:
    value = transaction.execute(
        select(func.count())
        .select_from(db.pipeline_node_runs)
        .where(
            and_(
                db.pipeline_node_runs.c.tenant_id == tenant_id,
                db.pipeline_node_runs.c.run_id == run_id,
                db.pipeline_node_runs.c.status == "RUNNING",
            )
        )
    ).scalar_one()
    return int(value)


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
                fencing_token=0,
                heartbeat_at=None,
                retry_at=None,
                error_kind=None,
                external_execution_id=None,
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
                attempt_id=record.attempt_id,
                fencing_token=record.fencing_token,
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


def claim_node_attempt(
    transaction: Any,
    claim: PipelineNodeAttemptClaim,
) -> PipelineNodeAttemptRow | None:
    node_run = _locked_claimable_node_run(transaction, claim)
    if node_run is None:
        return None
    coordinates = _next_attempt_coordinates(transaction, claim)
    if coordinates is None:
        return None
    attempt_number, fencing_token = coordinates
    attempt_id = f"{claim.node_run_id}:attempt:{attempt_number}"
    if not _claim_node_for_attempt(transaction, claim, node_run, attempt_number):
        return None
    _insert_claimed_attempt(transaction, claim, attempt_id, attempt_number, fencing_token)
    return required_attempt(transaction, claim.tenant_id, attempt_id)


def _locked_claimable_node_run(
    transaction: Any,
    claim: PipelineNodeAttemptClaim,
) -> PipelineNodeRunRow | None:
    row = (
        transaction.execute(
            select(db.pipeline_node_runs)
            .where(
                and_(
                    db.pipeline_node_runs.c.tenant_id == claim.tenant_id,
                    db.pipeline_node_runs.c.id == claim.node_run_id,
                )
            )
            .with_for_update()
        )
        .mappings()
        .first()
    )
    node_run = _optional_row(row, PipelineNodeRunRow)
    allowed = {"PENDING", "READY", "RUNNING", "RETRY_WAIT"}
    return node_run if node_run is not None and str(node_run["status"]).upper() in allowed else None


def _next_attempt_coordinates(
    transaction: Any,
    claim: PipelineNodeAttemptClaim,
) -> tuple[int, int] | None:
    latest = _latest_attempt(transaction, claim.tenant_id, claim.node_run_id)
    if latest is not None and not _can_take_attempt(latest, claim.heartbeat_at):
        return None
    if latest is not None and str(latest["status"]).upper() == "RUNNING":
        _mark_attempt_lost(transaction, latest, claim.heartbeat_at)
    attempt_number = int(latest["attempt_number"]) + 1 if latest is not None else 1
    fencing_token = int(latest["fencing_token"]) + 1 if latest is not None else 1
    return attempt_number, fencing_token


def _claim_node_for_attempt(
    transaction: Any,
    claim: PipelineNodeAttemptClaim,
    node_run: PipelineNodeRunRow,
    attempt_number: int,
) -> bool:
    return cas_status_update(
        transaction,
        db.pipeline_node_runs,
        tenant_id=claim.tenant_id,
        row_id=claim.node_run_id,
        transition=PIPELINE_NODE_RUN_ATTEMPT_CLAIMED,
        values={
            "attempt_count": attempt_number,
            "started_at": node_run["started_at"] or claim.heartbeat_at,
            "updated_at": claim.heartbeat_at,
        },
    )


def _insert_claimed_attempt(
    transaction: Any,
    claim: PipelineNodeAttemptClaim,
    attempt_id: str,
    attempt_number: int,
    fencing_token: int,
) -> None:
    transaction.execute(
        insert(db.pipeline_node_attempts).values(
            id=attempt_id,
            tenant_id=claim.tenant_id,
            node_run_id=claim.node_run_id,
            attempt_number=attempt_number,
            status="RUNNING",
            executor_profile=claim.executor_profile,
            lease_owner=claim.worker_id,
            lease_token=claim.lease_token,
            lease_expires_at=claim.lease_expires_at,
            fencing_token=fencing_token,
            heartbeat_at=claim.heartbeat_at,
            retry_at=None,
            error_kind=None,
            external_execution_id=claim.external_execution_id,
            input_manifest=claim.input_manifest,
            output_manifest={},
            error=None,
            started_at=claim.heartbeat_at,
            completed_at=None,
        )
    )


def heartbeat_node_attempt(
    transaction: Any,
    *,
    tenant_id: str,
    attempt_id: str,
    worker_id: str,
    lease_token: str,
    fencing_token: int,
    lease_expires_at: str,
    heartbeat_at: str,
) -> PipelineNodeAttemptRow | None:
    result = transaction.execute(
        update(db.pipeline_node_attempts)
        .where(
            and_(
                db.pipeline_node_attempts.c.tenant_id == tenant_id,
                db.pipeline_node_attempts.c.id == attempt_id,
                db.pipeline_node_attempts.c.status == "RUNNING",
                db.pipeline_node_attempts.c.lease_owner == worker_id,
                db.pipeline_node_attempts.c.lease_token == lease_token,
                db.pipeline_node_attempts.c.fencing_token == fencing_token,
                db.pipeline_node_attempts.c.lease_expires_at > heartbeat_at,
            )
        )
        .values(lease_expires_at=lease_expires_at, heartbeat_at=heartbeat_at)
    )
    return required_attempt(transaction, tenant_id, attempt_id) if result.rowcount else None


def update_fenced_node_attempt_terminal(
    transaction: Any,
    *,
    tenant_id: str,
    attempt_id: str,
    worker_id: str,
    lease_token: str,
    fencing_token: int,
    status: str,
    output_manifest: dict[str, object],
    error: dict[str, object] | None,
    error_kind: str | None,
    completed_at: str,
    retry_at: str | None,
) -> PipelineNodeAttemptRow | None:
    transition = _attempt_terminal_transition(status)
    updated = cas_status_update(
        transaction,
        db.pipeline_node_attempts,
        tenant_id=tenant_id,
        row_id=attempt_id,
        transition=transition,
        conditions=(
            db.pipeline_node_attempts.c.lease_owner == worker_id,
            db.pipeline_node_attempts.c.lease_token == lease_token,
            db.pipeline_node_attempts.c.fencing_token == fencing_token,
            db.pipeline_node_attempts.c.lease_expires_at >= completed_at,
        ),
        values={
            "output_manifest": output_manifest,
            "error": error,
            "error_kind": error_kind,
            "retry_at": retry_at,
            "heartbeat_at": completed_at,
            "completed_at": completed_at,
        },
    )
    if not updated:
        return None
    row = required_attempt(transaction, tenant_id, attempt_id)
    _update_node_after_attempt(transaction, row, status, error, completed_at, retry_at)
    return row


def schedule_node_retry(
    transaction: Any,
    *,
    tenant_id: str,
    node_run_id: str,
    attempt_id: str,
    fencing_token: int,
    retry_at: str,
    error_kind: str,
) -> bool:
    attempt_result = transaction.execute(
        update(db.pipeline_node_attempts)
        .where(
            and_(
                db.pipeline_node_attempts.c.tenant_id == tenant_id,
                db.pipeline_node_attempts.c.id == attempt_id,
                db.pipeline_node_attempts.c.node_run_id == node_run_id,
                db.pipeline_node_attempts.c.fencing_token == fencing_token,
                db.pipeline_node_attempts.c.status == "FAILED",
            )
        )
        .values(retry_at=retry_at, error_kind=error_kind)
    )
    if not attempt_result.rowcount:
        return False
    node_result = cas_status_update(
        transaction,
        db.pipeline_node_runs,
        tenant_id=tenant_id,
        row_id=node_run_id,
        transition=PIPELINE_NODE_RUN_RETRY_WAIT,
        values={"completed_at": None, "updated_at": retry_at},
    )
    return node_result


def insert_fenced_artifact(
    transaction: Any,
    record: PipelineRunArtifactRecord,
    worker_id: str,
    lease_token: str,
) -> PipelineRunArtifactRow | None:
    if record.attempt_id is None or record.fencing_token is None:
        return None
    owner = transaction.execute(
        select(db.pipeline_node_attempts.c.id).where(
            and_(
                db.pipeline_node_attempts.c.tenant_id == record.tenant_id,
                db.pipeline_node_attempts.c.id == record.attempt_id,
                db.pipeline_node_attempts.c.status == "RUNNING",
                db.pipeline_node_attempts.c.lease_owner == worker_id,
                db.pipeline_node_attempts.c.lease_token == lease_token,
                db.pipeline_node_attempts.c.fencing_token == record.fencing_token,
                db.pipeline_node_attempts.c.lease_expires_at >= record.created_at,
            )
        )
    ).first()
    return insert_artifact(transaction, record) if owner is not None else None


def _latest_attempt(transaction: Any, tenant_id: str, node_run_id: str) -> PipelineNodeAttemptRow | None:
    row = (
        transaction.execute(
            select(db.pipeline_node_attempts)
            .where(
                and_(
                    db.pipeline_node_attempts.c.tenant_id == tenant_id,
                    db.pipeline_node_attempts.c.node_run_id == node_run_id,
                )
            )
            .order_by(desc(db.pipeline_node_attempts.c.attempt_number))
            .limit(1)
            .with_for_update()
        )
        .mappings()
        .first()
    )
    return _optional_row(row, PipelineNodeAttemptRow)


def _locked_node_run(
    transaction: Any,
    tenant_id: str,
    run_id: str,
    node_id: str,
) -> PipelineNodeRunRow | None:
    row = (
        transaction.execute(
            select(db.pipeline_node_runs)
            .where(
                and_(
                    db.pipeline_node_runs.c.tenant_id == tenant_id,
                    db.pipeline_node_runs.c.run_id == run_id,
                    db.pipeline_node_runs.c.node_id == node_id,
                )
            )
            .with_for_update()
        )
        .mappings()
        .first()
    )
    return _optional_row(row, PipelineNodeRunRow)


def _is_cancel_owner(
    attempt: PipelineNodeAttemptRow,
    worker_id: str,
    external_execution_id: str,
) -> bool:
    return (
        attempt["status"] == "RUNNING"
        and attempt["lease_owner"] == worker_id
        and attempt["external_execution_id"] == external_execution_id
    )


def _cancel_attempt_row(
    transaction: Any,
    attempt: PipelineNodeAttemptRow,
    completed_at: str,
) -> None:
    cas_status_update(
        transaction,
        db.pipeline_node_attempts,
        tenant_id=str(attempt["tenant_id"]),
        row_id=str(attempt["id"]),
        transition=PIPELINE_NODE_ATTEMPT_CANCELLED,
        values={
            "error_kind": "cancellation",
            "error": {"kind": "cancellation"},
            "heartbeat_at": completed_at,
            "completed_at": completed_at,
        },
    )


def _cancel_node_row(
    transaction: Any,
    node_run: PipelineNodeRunRow,
    completed_at: str,
) -> None:
    cas_status_update(
        transaction,
        db.pipeline_node_runs,
        tenant_id=str(node_run["tenant_id"]),
        row_id=str(node_run["id"]),
        transition=PIPELINE_NODE_RUN_CANCELLED,
        values={
            "error": {"kind": "cancellation"},
            "completed_at": completed_at,
            "updated_at": completed_at,
        },
    )


def _can_take_attempt(attempt: PipelineNodeAttemptRow, claimed_at: str) -> bool:
    if str(attempt["status"]).upper() != "RUNNING":
        return True
    expires_at = attempt["lease_expires_at"]
    return expires_at is not None and expires_at <= claimed_at


def _mark_attempt_lost(transaction: Any, attempt: PipelineNodeAttemptRow, lost_at: str) -> None:
    cas_status_update(
        transaction,
        db.pipeline_node_attempts,
        tenant_id=str(attempt["tenant_id"]),
        row_id=str(attempt["id"]),
        transition=PIPELINE_NODE_ATTEMPT_LOST,
        conditions=(db.pipeline_node_attempts.c.fencing_token == attempt["fencing_token"],),
        values={
            "error_kind": "worker_lost",
            "error": {"kind": "worker_lost", "message": "node attempt lease expired"},
            "completed_at": lost_at,
        },
    )


def _update_node_after_attempt(
    transaction: Any,
    attempt: PipelineNodeAttemptRow,
    status: str,
    error: dict[str, object] | None,
    completed_at: str,
    retry_at: str | None,
) -> None:
    values: dict[str, object] = {
        "error": error,
        "updated_at": completed_at,
    }
    if retry_at is None:
        values["completed_at"] = completed_at
    cas_status_update(
        transaction,
        db.pipeline_node_runs,
        tenant_id=str(attempt["tenant_id"]),
        row_id=str(attempt["node_run_id"]),
        transition=(PIPELINE_NODE_RUN_RETRY_WAIT if retry_at is not None else _node_terminal_transition(status)),
        conditions=(db.pipeline_node_runs.c.attempt_count == attempt["attempt_number"],),
        values=values,
    )


def _attempt_terminal_transition(status: str) -> StatusTransition:
    transitions = {
        "SUCCEEDED": PIPELINE_NODE_ATTEMPT_SUCCEEDED,
        "FAILED": PIPELINE_NODE_ATTEMPT_FAILED,
        "CANCELLED": PIPELINE_NODE_ATTEMPT_CANCELLED,
    }
    return transitions[status]


def _node_terminal_transition(status: str) -> StatusTransition:
    transitions = {
        "SUCCEEDED": PIPELINE_NODE_RUN_SUCCEEDED,
        "FAILED": PIPELINE_NODE_RUN_FAILED,
        "CANCELLED": PIPELINE_NODE_RUN_CANCELLED,
    }
    return transitions[status]


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
