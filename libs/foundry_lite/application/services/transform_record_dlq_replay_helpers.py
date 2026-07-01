"""Application service helpers for transform record dlq replay helpers workflows."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Protocol

from foundry_lite.application.ports import (
    DatasetTransactionRepository,
    DeadLetterRecordRow,
    TransactionManager,
    TransformRecordDlqRetryResult,
    TransformRepository,
)
from foundry_lite.application.primitives import CommitResult, _now
from foundry_lite.application.services.runtime_error_payloads import scrub_error_text
from foundry_lite.application.services.transform_runs import TransformRunPlan
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected, NotFound, ValidationFailed


class TransformReplayRuntime(Protocol):
    dataset_transaction_repository: DatasetTransactionRepository
    engine: TransactionManager
    transform_repository: TransformRepository

    def _abort_transform_run(self, ctx: RequestContext, plan: TransformRunPlan, exc: Exception) -> None: ...


def _retry_result(row: DeadLetterRecordRow, *, is_idempotent_replay: bool) -> TransformRecordDlqRetryResult:
    return {
        "deadLetterRecordId": row["id"],
        "status": row["status"],
        "replayStatus": row["replay_status"],
        "replayRunId": _replay_run_id(row),
        "transformRunId": _metadata_transform_run_id(row),
        "isIdempotentReplay": is_idempotent_replay,
        "replayDatasetVersionId": _replay_dataset_version_id(row),
        "rowCount": _replay_row_count(row),
        "error": _metadata_mapping(row, "lastReplayError"),
        "downstreamBackfillPlan": _backfill_plan(row),
    }


def _success_metadata(
    row: DeadLetterRecordRow,
    ctx: RequestContext,
    plan: TransformRunPlan,
    result: CommitResult,
) -> Mapping[str, object]:
    metadata = dict(row["metadata"])
    metadata["lastOperation"] = _operation(ctx, "transform_replay_succeeded")
    metadata["replayResult"] = {
        "datasetVersionId": result.version_id,
        "transactionId": result.transaction_id,
        "rowCount": result.row_count,
    }
    metadata["transformReplay"] = _transform_replay_metadata(row, plan)
    return metadata


def _failure_metadata(
    row: DeadLetterRecordRow,
    ctx: RequestContext,
    exc: Exception,
    plan: TransformRunPlan | None,
) -> Mapping[str, object]:
    metadata = dict(row["metadata"])
    metadata["lastOperation"] = _operation(ctx, "transform_replay_failed")
    metadata["lastReplayError"] = {"type": exc.__class__.__name__, "message": _safe_error_message(exc)}
    if plan is not None:
        metadata["transformReplay"] = _transform_replay_metadata(row, plan)
    return metadata


def _request_metadata(row: DeadLetterRecordRow, ctx: RequestContext) -> Mapping[str, object]:
    metadata = dict(row["metadata"])
    metadata["lastOperation"] = _operation(ctx, "transform_replay_requested")
    metadata["transformReplayPolicy"] = {
        "mode": "snapshot_full_rebuild",
        "inputSelection": "latest_committed_input_versions",
    }
    return metadata


def _lease_metadata(
    row: DeadLetterRecordRow,
    ctx: RequestContext,
    token: str,
    started_at: str,
) -> Mapping[str, object]:
    metadata = dict(row["metadata"])
    metadata["replayLease"] = {"token": token, "requestId": ctx.request_id, "startedAt": started_at}
    return metadata


def _transform_replay_metadata(row: DeadLetterRecordRow, plan: TransformRunPlan) -> Mapping[str, object]:
    return {
        "originTransformRunId": _origin_transform_run_id(row),
        "transformRunId": plan.run_id,
        "transformApiName": _transform_api_name(row),
        "outputDatasetRef": str(plan.definition_snapshot["output_dataset_ref"]),
        "inputVersions": dict(plan.input_versions),
    }


def _backfill_plan(row: DeadLetterRecordRow) -> Mapping[str, object]:
    return {
        "affectedDatasetVersionId": row["source_dataset_version_id"],
        "is_closed_partition_affected": row["is_closed_partition_affected"],
        "nextStep": "Rerun the owning snapshot transform with the current definition and latest input versions.",
    }


def _transform_mode(runtime: TransformReplayRuntime, ctx: RequestContext, row: DeadLetterRecordRow) -> str:
    with runtime.engine.begin() as conn:
        transform = runtime.transform_repository.transform_by_api_name(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            api_name=_transform_api_name(row),
        )
    if transform is None:
        raise NotFound("transform not found", details={"api_name": _transform_api_name(row)})
    return str(transform["mode"])


def _row_from_result(
    runtime: TransformReplayRuntime,
    ctx: RequestContext,
    result: TransformRecordDlqRetryResult,
) -> DeadLetterRecordRow:
    with runtime.engine.begin() as conn:
        row = runtime.dataset_transaction_repository.dead_letter_record_by_id(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            record_id=result["deadLetterRecordId"],
        )
    if row is None:
        raise NotFound("dead-letter record not found", details={"record_id": result["deadLetterRecordId"]})
    return row


def _abort_plan(
    runtime: TransformReplayRuntime,
    ctx: RequestContext,
    plan: TransformRunPlan,
    exc: Exception,
) -> None:
    try:
        runtime._abort_transform_run(ctx, plan, exc)
    except ConflictDetected:
        return


def _required_idempotency_key(idempotency_key: str) -> str:
    if not idempotency_key:
        raise ValidationFailed("Idempotency-Key header is required")
    return idempotency_key


def _is_terminal_idempotent(row: DeadLetterRecordRow, idempotency_key: str) -> bool:
    return row.get("replay_idempotency_key") == idempotency_key and row["replay_status"] in {"SUCCEEDED", "FAILED"}


def _record_kind(row: DeadLetterRecordRow) -> str | None:
    value = row["metadata"].get("recordDlqKind")
    return value if isinstance(value, str) else None


def _transform_api_name(row: DeadLetterRecordRow) -> str:
    return _metadata_string(row, "transformApiName")


def _origin_transform_run_id(row: DeadLetterRecordRow) -> str:
    return _metadata_string(row, "transformRunId")


def _replay_run_id(row: DeadLetterRecordRow) -> str:
    value = row.get("replay_run_id")
    if isinstance(value, str) and value:
        return value
    raise ValidationFailed("dead-letter record replay run is missing", details={"record_id": row["id"]})


def _lease_token(row: DeadLetterRecordRow) -> str:
    value = _optional_lease_token(row)
    if value is None:
        raise ValidationFailed("dead-letter record replay lease is missing", details={"record_id": row["id"]})
    return value


def _optional_lease_token(row: DeadLetterRecordRow) -> str | None:
    value = row.get("replay_lease_token")
    return value if isinstance(value, str) and value else None


def _metadata_string(row: DeadLetterRecordRow, key: str) -> str:
    value = row["metadata"].get(key)
    if isinstance(value, str) and value:
        return value
    raise ValidationFailed("transform DLQ metadata is incomplete", details={"record_id": row["id"], "field": key})


def _metadata_transform_run_id(row: DeadLetterRecordRow) -> str | None:
    replay = _metadata_mapping(row, "transformReplay")
    value = replay.get("transformRunId") if replay is not None else None
    return value if isinstance(value, str) else None


def _replay_dataset_version_id(row: DeadLetterRecordRow) -> str | None:
    result = _metadata_mapping(row, "replayResult")
    value = result.get("datasetVersionId") if result is not None else None
    return value if isinstance(value, str) else None


def _replay_row_count(row: DeadLetterRecordRow) -> int | None:
    result = _metadata_mapping(row, "replayResult")
    value = result.get("rowCount") if result is not None else None
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _metadata_mapping(row: DeadLetterRecordRow, key: str) -> Mapping[str, object] | None:
    value = row["metadata"].get(key)
    return value if isinstance(value, Mapping) else None


def _operation(ctx: RequestContext, operation: str) -> Mapping[str, object]:
    return {"operation": operation, "requestId": ctx.request_id, "actorUserId": ctx.actor_user_id, "at": _now()}


def _safe_error_message(exc: Exception) -> str:
    return scrub_error_text(str(exc))


def _lease_expires_at() -> str:
    return (datetime.now().astimezone() + timedelta(minutes=15)).isoformat()
