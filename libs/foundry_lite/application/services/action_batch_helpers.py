"""Pure helpers for batch action apply.

Split from ``action_batch_service`` so the service module stays within the
application module-size gate; everything here is side-effect free except the
outbox publisher, which only writes through the injected runtime boundary.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import cast

from foundry_lite.application.action_types import (
    ActionBatchApplyResponse,
    ActionBatchTarget,
    ActionBatchTargetResult,
    ActionCacheRefreshHint,
)
from foundry_lite.application.ports import ActionRunRow, ActionTypeRow, ObjectRecordRow, TransactionContext
from foundry_lite.application.primitives import _json_hash
from foundry_lite.application.safe_expression import validate_action_request
from foundry_lite.application.services.action_protocols import ActionRuntimeBoundary
from foundry_lite.application.services.runtime_error_payloads import scrub_error_mapping, scrub_error_text
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected, FoundryLiteError, NotFound, ValidationFailed

# Palantir allows up to 10,000 objects per action; foundry-lite caps lower
# because every target is validated and mutated synchronously in one
# transaction and the whole batch response is persisted on the run row.
BATCH_TARGET_LIMIT = 1_000
# action_runs.target_object_id is NOT NULL; a batch run has no single target,
# so a sentinel keeps the schema additive-free while per-target evidence lives
# in the run result, object_edits rows, and audit events.
BATCH_TARGET_OBJECT_ID = "__batch__"
BATCH_EXPECTED_OBJECT_VERSION = 0

TargetFailure = dict[str, object]


def batch_targets_from_request(raw_targets: Sequence[Mapping[str, object]]) -> tuple[ActionBatchTarget, ...]:
    """Parse and cap targets before any authorization or database work.

    Rejecting empty/duplicate/oversized batches up front keeps the failure
    cheap and deterministic — no run row, no audit noise for malformed input.
    """
    if not raw_targets:
        raise ValidationFailed("batch apply requires at least one target")
    if len(raw_targets) > BATCH_TARGET_LIMIT:
        raise ValidationFailed(
            "batch apply target limit exceeded",
            details={"targetCount": len(raw_targets), "targetLimit": BATCH_TARGET_LIMIT},
        )
    targets = tuple(_batch_target(index, raw) for index, raw in enumerate(raw_targets))
    seen: set[str] = set()
    duplicates: set[str] = set()
    for target in targets:
        if target.object_id in seen:
            duplicates.add(target.object_id)
        seen.add(target.object_id)
    if duplicates:
        raise ValidationFailed("batch apply targets must be unique", details={"duplicateObjectIds": sorted(duplicates)})
    return targets


def _batch_target(index: int, raw: Mapping[str, object]) -> ActionBatchTarget:
    object_id = raw.get("object_id")
    expected_object_version = raw.get("expected_object_version")
    if not isinstance(object_id, str) or not object_id:
        raise ValidationFailed("batch target requires object_id", details={"targetIndex": index})
    if not isinstance(expected_object_version, int) or isinstance(expected_object_version, bool):
        raise ValidationFailed("batch target requires expected_object_version", details={"targetIndex": index})
    return ActionBatchTarget(object_id=object_id, expected_object_version=expected_object_version)


def batch_request_fingerprint(
    *,
    action_api_name: str,
    object_type: str,
    targets: Sequence[ActionBatchTarget],
    params: Mapping[str, object],
) -> str:
    """Fingerprint the full batch body so a reused key with drifted targets conflicts."""
    return _json_hash(
        {
            "version": 1,
            "kind": "actionBatchApply",
            "actionApiName": action_api_name,
            "objectType": object_type,
            "targets": [{"objectId": t.object_id, "expectedObjectVersion": t.expected_object_version} for t in targets],
            "params": dict(params),
        }
    )


def require_batch_supported_action(action_type: ActionTypeRow) -> None:
    """Reject batch apply for writeback-declaring actions (documented limitation).

    The external-writeback saga (before-commit call, outcome-unknown
    reconciliation, compensation) is defined for exactly one target; running it
    across N objects atomically is a different protocol. Keeping those actions
    single-target preserves the saga's invariants.
    """
    definition = action_type.get("definition")
    writebacks = definition.get("writebacks") if isinstance(definition, Mapping) else None
    if writebacks:
        raise ValidationFailed(
            "batch apply does not support actions with declared writebacks",
            details={"actionApiName": str(action_type["api_name"])},
        )


def batch_failure_error(failures: Sequence[TargetFailure], target_count: int) -> FoundryLiteError:
    """Fold per-target failures into ONE typed error; conflicts outrank validation."""
    details: dict[str, object] = {"targetCount": target_count, "failures": list(failures)}
    if any(failure.get("code") == ConflictDetected.code for failure in failures):
        return ConflictDetected("batch action apply rejected: target conflicts", details=details)
    return ValidationFailed("batch action apply rejected: target validation failed", details=details)


def batch_replay_response(existing: ActionRunRow) -> ActionBatchApplyResponse:
    """Rebuild the stored batch response for an idempotent replay (mirrors single apply)."""
    result = dict(existing["result"] or {})
    response: ActionBatchApplyResponse = {
        "actionRunId": existing["id"],
        "status": existing["status"],
        "objectType": existing["target_object_type_api_name"],
        "results": [],
        "idempotentReplay": True,
    }
    results = result.get("results")
    if isinstance(results, list):
        response["results"] = cast(
            "list[ActionBatchTargetResult]", [item for item in results if isinstance(item, dict)]
        )
    cache_refresh = result.get("cacheRefresh")
    if isinstance(cache_refresh, dict):
        response["cacheRefresh"] = cast(ActionCacheRefreshHint, cache_refresh)
    return response


def batch_success_response(
    action_run_id: str,
    object_type: str,
    results: list[ActionBatchTargetResult],
) -> ActionBatchApplyResponse:
    return {
        "actionRunId": action_run_id,
        "status": "succeeded",
        "objectType": object_type,
        "results": results,
        "cacheRefresh": {
            "objectKeys": [f"objects:{object_type}:{item['objectId']}" for item in results],
            "objectTypeKeys": [f"objects:{object_type}:query"],
            "actionRunKeys": [f"actions:runs:{action_run_id}"],
        },
    }


def target_request_error(
    action_type: ActionTypeRow,
    record: ObjectRecordRow | None,
    expected_object_version: int,
    params: Mapping[str, object],
    ctx: RequestContext,
) -> Exception | None:
    """Same per-target checks as single apply: existence, version CAS read, preconditions/params."""
    if record is None:
        return NotFound("target object not found")
    if record["object_version"] != expected_object_version:
        return ConflictDetected(
            "object version conflict",
            details={
                "currentObjectVersion": record["object_version"],
                "expectedObjectVersion": expected_object_version,
            },
        )
    return validate_action_request(action_type, record, params, ctx)


def target_failure(object_id: str, error: Exception) -> TargetFailure:
    failure: TargetFailure = {"objectId": object_id, "message": scrub_error_text(str(error))}
    if isinstance(error, FoundryLiteError):
        failure["code"] = error.code
        if error.details:
            failure["details"] = scrub_error_mapping(error.details)
    else:
        failure["code"] = "VALIDATION_FAILED"
    return failure


def publish_batch_commit_events(
    runtime_service: ActionRuntimeBoundary,
    conn: TransactionContext,
    ctx: RequestContext,
    action_run_id: str,
    object_type: str,
    results: list[ActionBatchTargetResult],
) -> None:
    """One run-level event plus per-object edit/change events (same topics as single apply).

    Per-object outbox idempotency keys carry the object id: the run-level key
    alone would dedupe every target after the first one away.
    """
    run_payload = {
        "actionRunId": action_run_id,
        "objectType": object_type,
        "objectIds": [item["objectId"] for item in results],
        "targetCount": len(results),
    }
    _publish_batch_event(
        runtime_service,
        conn,
        ctx,
        action_run_id,
        "action.run.committed",
        "action_run",
        action_run_id,
        run_payload,
        idempotency_key=f"action.run.committed:{action_run_id}",
        edit_id=None,
    )
    for item in results:
        _publish_target_events(runtime_service, conn, ctx, action_run_id, object_type, item)


def _publish_target_events(
    runtime_service: ActionRuntimeBoundary,
    conn: TransactionContext,
    ctx: RequestContext,
    action_run_id: str,
    object_type: str,
    item: ActionBatchTargetResult,
) -> None:
    payload = {
        "actionRunId": action_run_id,
        "objectType": object_type,
        "objectId": item["objectId"],
        "editId": item["objectEditId"],
    }
    for event_type in ("object.edit.committed", "object.changed"):
        _publish_batch_event(
            runtime_service,
            conn,
            ctx,
            action_run_id,
            event_type,
            "object",
            item["objectId"],
            payload,
            idempotency_key=f"{event_type}:{action_run_id}:{item['objectId']}",
            edit_id=item["objectEditId"],
        )


def _publish_batch_event(
    runtime_service: ActionRuntimeBoundary,
    conn: TransactionContext,
    ctx: RequestContext,
    action_run_id: str,
    event_type: str,
    aggregate_type: str,
    aggregate_id: str,
    payload: Mapping[str, object],
    *,
    idempotency_key: str,
    edit_id: str | None,
) -> None:
    outbox_event_id = runtime_service._outbox(
        conn,
        ctx,
        event_type,
        aggregate_type,
        aggregate_id,
        payload,
        idempotency_key=idempotency_key,
        correlation_id=action_run_id,
    )
    if outbox_event_id is None:
        return
    metadata: dict[str, object] = {
        "eventType": event_type,
        "aggregateType": aggregate_type,
        "aggregateId": aggregate_id,
    }
    if edit_id is not None:
        metadata["objectEditId"] = edit_id
    _record_emitted_relation(runtime_service, conn, ctx, action_run_id, outbox_event_id, metadata)


def _record_emitted_relation(
    runtime_service: ActionRuntimeBoundary,
    conn: TransactionContext,
    ctx: RequestContext,
    action_run_id: str,
    outbox_event_id: str,
    metadata: Mapping[str, object],
) -> None:
    runtime_service._run_relation(
        conn,
        ctx,
        source_run_type="action",
        source_run_id=action_run_id,
        target_run_type="outbox",
        target_run_id=outbox_event_id,
        relation="emitted",
        resource_type="outbox_event",
        resource_id=outbox_event_id,
        metadata=metadata,
    )
