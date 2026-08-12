"""Durable exactly-once receipts for proposal-driven ontology activation."""

from __future__ import annotations

from collections.abc import Mapping
from typing import cast

from foundry_lite.application.ports import OntologyApplyResult, TransactionContext
from foundry_lite.application.primitives import _json_hash
from foundry_lite.application.services.ontology_protocols import OntologyRuntimeBoundary
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected, ValidationFailed

JsonObject = Mapping[str, object]


def required_activation_key(value: str) -> str:
    key = value.strip()
    if not key:
        raise ValidationFailed("ontology activation idempotency key is required")
    return f"ontology.activation:{key}"


def activation_fingerprint(definition: JsonObject) -> str:
    return _json_hash(definition)


def replay_activation(
    runtime: OntologyRuntimeBoundary,
    conn: TransactionContext,
    ctx: RequestContext,
    receipt_key: str,
    fingerprint: str,
) -> OntologyApplyResult | None:
    row = runtime._outbox_event_by_idempotency_key(conn, ctx, receipt_key)
    payload = row.get("payload") if isinstance(row, Mapping) else None
    if not isinstance(payload, Mapping):
        return None
    if payload.get("definitionFingerprint") != fingerprint:
        raise ConflictDetected("ontology activation receipt is bound to a different definition")
    result = payload.get("result")
    if not isinstance(result, Mapping):
        raise ConflictDetected("ontology activation receipt is malformed")
    required = ("ontology_version_id", "version_number", "migration_plan")
    if any(key not in result for key in required):
        raise ConflictDetected("ontology activation receipt is malformed")
    return cast(OntologyApplyResult, dict(result))


def record_activation_receipt(
    runtime: OntologyRuntimeBoundary,
    conn: TransactionContext,
    ctx: RequestContext,
    receipt_key: str,
    fingerprint: str,
    result: OntologyApplyResult,
) -> None:
    runtime._outbox(
        conn,
        ctx,
        "ontology.activation.completed",
        "ontology_version",
        result["ontology_version_id"],
        {"definitionFingerprint": fingerprint, "result": dict(result)},
        idempotency_key=receipt_key,
        correlation_id=receipt_key,
    )


__all__ = [
    "activation_fingerprint",
    "record_activation_receipt",
    "replay_activation",
    "required_activation_key",
]
