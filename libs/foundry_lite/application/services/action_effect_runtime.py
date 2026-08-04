"""Pure payload helpers shared by before- and after-commit Action effects."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta

from foundry_lite.application.action_async_execution_types import (
    ActionAsyncRunRow,
    ActionEffectClaim,
    ActionEffectReceiptRecord,
    ActionEffectReceiptRow,
)
from foundry_lite.application.ports.action_effect_executor import ActionEffectExecutionRequest
from foundry_lite.application.primitives import _new_id, _now
from foundry_lite.domain.action_runtime.action_effects import (
    ActionEffectV3,
    action_effect_payload,
    compile_action_effects,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import InvariantViolation


def effect_receipt_record(
    ctx: RequestContext,
    row: ActionAsyncRunRow,
    effect: ActionEffectV3,
    *,
    committed_result: Mapping[str, object] | None = None,
    outbox_event_id: str | None = None,
) -> ActionEffectReceiptRecord:
    """Create the deterministic durable receipt record for one planned effect."""
    created_at = _now()
    return ActionEffectReceiptRecord(
        receipt_id=f"{row['id']}:effect:{effect.effect_id}",
        tenant_id=ctx.tenant_id,
        action_run_id=str(row["id"]),
        effect_id=effect.effect_id,
        phase=effect.phase,
        effect_kind=effect.kind,
        target_ref=effect.target_ref,
        idempotency_key=f"action-effect:{row['id']}:{effect.effect_id}",
        max_attempts=effect.max_attempts,
        request={
            "effect": action_effect_payload(effect),
            "actorUserId": row["actor_user_id"],
            "requestId": ctx.request_id,
            "parameters": dict(row["parameters"]),
            "committedResult": dict(committed_result or {}),
        },
        created_at=created_at,
        outbox_event_id=outbox_event_id,
    )


def effect_claim(
    receipt: ActionEffectReceiptRow,
    worker_id: str,
    *,
    lease_seconds: int = 30,
    is_reconciliation: bool = False,
) -> ActionEffectClaim:
    """Create a fresh lease and fencing claim for an effect receipt."""
    claimed = datetime.now(UTC)
    return ActionEffectClaim(
        tenant_id=receipt["tenant_id"],
        receipt_id=receipt["id"],
        worker_id=worker_id,
        lease_token=_new_id("effect_lease"),
        lease_expires_at=(claimed + timedelta(seconds=lease_seconds)).isoformat(),
        claimed_at=claimed.isoformat(),
        is_reconciliation=is_reconciliation,
    )


def effect_request(receipt: ActionEffectReceiptRow, request_id: str) -> ActionEffectExecutionRequest:
    """Rehydrate a typed adapter request from a persisted receipt."""
    request = receipt["request"]
    return ActionEffectExecutionRequest(
        tenant_id=receipt["tenant_id"],
        action_run_id=receipt["action_run_id"],
        actor_user_id=_required_text(request, "actorUserId"),
        request_id=request_id,
        idempotency_key=receipt["idempotency_key"],
        effect=_effect_from_request(request),
        parameters=_mapping(request.get("parameters")),
        committed_result=_mapping(request.get("committedResult")),
    )


def effect_retry_at(attempt_number: int) -> str:
    """Return deterministic bounded exponential backoff for an effect attempt."""
    delay = min(30, 2 ** max(0, attempt_number - 1))
    return (datetime.now(UTC) + timedelta(seconds=delay)).isoformat()


def _effect_from_request(request: Mapping[str, object]) -> ActionEffectV3:
    effect = request.get("effect")
    if not isinstance(effect, Mapping):
        raise InvariantViolation("Action effect receipt is missing its immutable effect contract")
    return compile_action_effects({"effects": [dict(effect)]})[0]


def _required_text(value: Mapping[str, object], key: str) -> str:
    raw = value.get(key)
    if not isinstance(raw, str) or not raw:
        raise InvariantViolation("Action effect receipt is missing execution identity", details={"field": key})
    return raw


def _mapping(raw: object) -> Mapping[str, object]:
    return raw if isinstance(raw, Mapping) else {}
