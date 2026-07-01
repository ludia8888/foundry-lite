"""Application service helpers for webhook ingest workflows."""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import NoReturn, Protocol

from foundry_lite.application.ports import (
    DatasetRow,
    DatasetTransactionRepository,
    TransactionContext,
    TransactionManager,
)
from foundry_lite.application.primitives import CommitResult, _now
from foundry_lite.application.services.dataset.ingest_models import UploadSyncPlan, WebhookIngest
from foundry_lite.application.services.dataset.protocols import (
    DatasetRegistryLookup,
    DatasetRuntimeBoundary,
    DatasetTransactionManager,
    DatasetVersionLookup,
)
from foundry_lite.application.services.dataset.webhook_commit_metadata import (
    WebhookCommitMetadataRecorder,
    finalize_webhook_commit,
)
from foundry_lite.application.services.write_traffic_gate import require_write_open
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import (
    ConflictDetected,
    PermissionDenied,
    ValidationFailed,
)

_WEBHOOK_MAX_CLOCK_SKEW_SECONDS = 300
_WEBHOOK_EVENT_ID_KEYS = ("provider_event_id", "providerEventId", "event_id", "eventId", "delivery_id", "deliveryId")
_WEBHOOK_VOLATILE_KEYS = frozenset(
    {
        "deliveryattempt",
        "receivedat",
        "signature",
        "timestamp",
        "ts",
    }
)
_SERVICE_PRINCIPAL_SIGNING_VERSION = "foundry-lite-webhook-service-principal-v1"


@dataclass(frozen=True)
class WebhookSignatureContext:
    tenant_id: str
    actor_user_id: str
    dataset_ref: str
    connector_name: str
    resource_name: str


class WebhookIngestRuntime(Protocol):
    dataset_transaction_repository: DatasetTransactionRepository
    dataset_registry_service: DatasetRegistryLookup
    dataset_transaction_service: DatasetTransactionManager
    runtime_service: DatasetRuntimeBoundary
    engine: TransactionManager

    def _start_connector_sync_run(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        dataset: DatasetRow,
        connector_name: str,
        resource_name: str,
        sync_name: str | None,
        *,
        tx_type: str = "SNAPSHOT",
        source_type: str | None = None,
    ) -> UploadSyncPlan: ...

    def _rows_to_parquet(
        self, rows: Sequence[Mapping[str, object]], target_path: Path, fieldnames: list[str]
    ) -> None: ...

    def _mark_sync_run_committed(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        run_id: str,
        result: CommitResult,
    ) -> None: ...

    def _abort_connector_after_error(
        self,
        ctx: RequestContext,
        transaction_id: str,
        run_id: str,
        exc: Exception,
    ) -> NoReturn: ...


def ingest_webhook_event(
    runtime: WebhookIngestRuntime,
    dataset_ref: str,
    *,
    dataset_version_service: DatasetVersionLookup,
    connector_name: str,
    resource_name: str,
    payload: Mapping[str, object],
    raw_body: bytes,
    signature: str,
    signature_timestamp: str,
    secret: str,
    event_id: str | None = None,
    signature_context: WebhookSignatureContext | None = None,
    ctx: RequestContext | None = None,
) -> CommitResult:
    ctx = ctx or RequestContext()
    ingest = _prepare_webhook_ingest(
        runtime,
        ctx,
        dataset_ref,
        connector_name,
        resource_name,
        payload,
        raw_body,
        signature,
        signature_timestamp,
        secret,
        event_id,
        signature_context,
    )
    return _commit_or_replay_webhook_event(
        runtime,
        dataset_version_service,
        ctx,
        ingest,
        connector_name,
        resource_name,
        payload,
    )


def _commit_or_replay_webhook_event(
    runtime: WebhookIngestRuntime,
    dataset_version_service: DatasetVersionLookup,
    ctx: RequestContext,
    ingest: WebhookIngest,
    connector_name: str,
    resource_name: str,
    payload: Mapping[str, object],
) -> CommitResult:
    replay = _committed_webhook_event_result(
        runtime,
        dataset_version_service,
        ctx,
        ingest,
        connector_name,
        resource_name,
    )
    if replay is not None:
        return replay
    plan = _start_webhook_sync_run(runtime, ctx, ingest.dataset, connector_name, resource_name)
    staged = runtime.dataset_transaction_service._staging_file(
        ingest.dataset,
        plan.transaction_id,
        "part-00000.parquet",
    )
    return _commit_webhook_event(
        runtime,
        ctx,
        ingest.dataset,
        plan,
        staged,
        connector_name,
        resource_name,
        ingest.event_id,
        ingest.payload_hash,
        payload,
    )


def _prepare_webhook_ingest(
    runtime: WebhookIngestRuntime,
    ctx: RequestContext,
    dataset_ref: str,
    connector_name: str,
    resource_name: str,
    payload: Mapping[str, object],
    raw_body: bytes,
    signature: str,
    signature_timestamp: str,
    secret: str,
    event_id: str | None,
    signature_context: WebhookSignatureContext | None,
) -> WebhookIngest:
    _require_valid_webhook_signature(
        runtime,
        ctx,
        dataset_ref,
        connector_name,
        resource_name,
        raw_body,
        signature,
        signature_timestamp,
        secret,
        signature_context,
    )
    runtime.runtime_service._require_or_audit(ctx, "dataset:webhook_ingest", "dataset", dataset_ref)
    require_write_open(runtime.runtime_service, ctx, "ingest_webhook_event", "dataset", dataset_ref)
    dataset = runtime.dataset_registry_service.get_dataset(dataset_ref, ctx=ctx)
    normalized_event_id = _webhook_event_id(connector_name, resource_name, payload, event_id)
    payload_hash = _webhook_payload_hash(payload)
    return WebhookIngest(dataset=dataset, event_id=normalized_event_id, payload_hash=payload_hash)


def _require_valid_webhook_signature(
    runtime: WebhookIngestRuntime,
    ctx: RequestContext,
    dataset_ref: str,
    connector_name: str,
    resource_name: str,
    raw_body: bytes,
    signature: str,
    signature_timestamp: str,
    secret: str,
    signature_context: WebhookSignatureContext | None,
) -> None:
    try:
        if _webhook_signature_valid(raw_body, signature, signature_timestamp, secret, signature_context):
            return
    except PermissionDenied:
        _audit_webhook_signature_denied(runtime, ctx, dataset_ref, connector_name, resource_name)
        raise
    _audit_webhook_signature_denied(runtime, ctx, dataset_ref, connector_name, resource_name)
    raise PermissionDenied("invalid webhook signature", details={"connector": connector_name})


def _start_webhook_sync_run(
    runtime: WebhookIngestRuntime,
    ctx: RequestContext,
    dataset: DatasetRow,
    connector_name: str,
    resource_name: str,
) -> UploadSyncPlan:
    with runtime.engine.begin() as conn:
        return runtime._start_connector_sync_run(
            conn,
            ctx,
            dataset,
            connector_name,
            resource_name,
            f"webhook:{connector_name}:{resource_name}",
            tx_type="APPEND",
            source_type=f"webhook.{connector_name}",
        )


def _committed_webhook_event_result(
    runtime: WebhookIngestRuntime,
    dataset_version_service: DatasetVersionLookup,
    ctx: RequestContext,
    ingest: WebhookIngest,
    connector_name: str,
    resource_name: str,
) -> CommitResult | None:
    with runtime.engine.begin() as conn:
        tx = runtime.dataset_transaction_repository.committed_webhook_transaction_by_event(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            dataset_id=ingest.dataset["id"],
            connector_name=connector_name,
            resource_name=resource_name,
            event_id=ingest.event_id,
        )
    if tx is None or tx["committed_version_id"] is None:
        return None
    _ensure_webhook_payload_matches(tx["metadata"], ingest.event_id, ingest.payload_hash)
    version = dataset_version_service._get_version(ingest.dataset["id"], tx["committed_version_id"], ctx=ctx)
    schema = dataset_version_service._schema_for_version(ingest.dataset["id"], version["schema_version"])
    return CommitResult(
        dataset_id=ingest.dataset["id"],
        dataset_ref=f"{ingest.dataset['namespace']}.{ingest.dataset['name']}",
        transaction_id=tx["id"],
        version_id=version["id"],
        version_number=version["version_number"],
        row_count=version["row_count"],
        manifest_uri=version["manifest_uri"],
        schema_hash=str(schema["schema_hash"]),
    )


def _commit_webhook_event(
    runtime: WebhookIngestRuntime,
    ctx: RequestContext,
    dataset: DatasetRow,
    plan: UploadSyncPlan,
    staged: Path,
    connector_name: str,
    resource_name: str,
    event_id: str,
    payload_hash: str,
    payload: Mapping[str, object],
) -> CommitResult:
    try:
        row = _webhook_event_row(connector_name, resource_name, event_id, payload)
        metadata = _webhook_transaction_metadata(connector_name, resource_name, event_id, payload)
        runtime._rows_to_parquet([row], staged, list(row))
        return finalize_webhook_commit(
            runtime,
            ctx,
            dataset,
            plan,
            staged,
            metadata,
            WebhookCommitMetadataRecorder(
                runtime,
                ctx,
                plan.run_id,
                dataset,
                connector_name,
                resource_name,
                event_id,
                payload_hash,
            ),
        )
    except Exception as exc:
        runtime._abort_connector_after_error(ctx, plan.transaction_id, plan.run_id, exc)


def _audit_webhook_signature_denied(
    runtime: WebhookIngestRuntime,
    ctx: RequestContext,
    dataset_ref: str,
    connector_name: str,
    resource_name: str,
) -> None:
    with runtime.engine.begin() as conn:
        runtime.runtime_service._audit(
            conn,
            ctx,
            event_type="permission.denied",
            resource_type="webhook",
            resource_id=f"{connector_name}:{resource_name}",
            action="webhook:ingest",
            decision="deny",
            before_ref={"dataset_ref": dataset_ref},
        )


def _webhook_signature_valid(
    raw_body: bytes,
    signature: str,
    signature_timestamp: str,
    secret: str,
    signature_context: WebhookSignatureContext | None = None,
) -> bool:
    if not secret:
        raise ValidationFailed("webhook secret is not configured")
    _require_fresh_webhook_timestamp(signature_timestamp)
    signed_payload = _webhook_signed_payload(raw_body, signature_timestamp, signature_context)
    expected = hmac.new(secret.encode("utf-8"), signed_payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature, f"sha256={expected}")


def _webhook_signed_payload(
    raw_body: bytes,
    signature_timestamp: str,
    signature_context: WebhookSignatureContext | None,
) -> bytes:
    if signature_context is None:
        return signature_timestamp.encode("utf-8") + b"." + raw_body
    fields = (
        _SERVICE_PRINCIPAL_SIGNING_VERSION,
        signature_timestamp,
        signature_context.tenant_id,
        signature_context.actor_user_id,
        signature_context.dataset_ref,
        signature_context.connector_name,
        signature_context.resource_name,
    )
    return b"\0".join(field.encode("utf-8") for field in fields) + b"\0" + raw_body


def _require_fresh_webhook_timestamp(signature_timestamp: str) -> None:
    try:
        parsed = datetime.fromisoformat(signature_timestamp.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PermissionDenied("invalid webhook timestamp", details={"timestamp": signature_timestamp}) from exc
    if parsed.tzinfo is None:
        raise PermissionDenied("invalid webhook timestamp", details={"timestamp": signature_timestamp})
    skew = abs((datetime.now(UTC) - parsed.astimezone(UTC)).total_seconds())
    if skew > _WEBHOOK_MAX_CLOCK_SKEW_SECONDS:
        raise PermissionDenied(
            "webhook timestamp outside replay window",
            details={"max_clock_skew_seconds": _WEBHOOK_MAX_CLOCK_SKEW_SECONDS},
        )


def _webhook_event_row(
    connector_name: str,
    resource_name: str,
    event_id: str,
    payload: Mapping[str, object],
) -> Mapping[str, object]:
    return {
        "event_id": event_id,
        "connector": connector_name,
        "resource": resource_name,
        "received_at": f"ts:{_now()}",
        "payload_json": _webhook_payload_json(payload),
    }


def _webhook_event_id(
    connector_name: str,
    resource_name: str,
    payload: Mapping[str, object],
    event_id: str | None,
) -> str:
    if event_id:
        return event_id
    payload_event_id = _webhook_payload_event_id(payload)
    payload_identity = payload_event_id if payload_event_id is not None else _webhook_canonical_payload_json(payload)
    digest = hashlib.sha256(f"{connector_name}\0{resource_name}\0{payload_identity}".encode()).hexdigest()
    return f"weh_{digest}"


def _webhook_payload_json(payload: Mapping[str, object]) -> str:
    return json.dumps(dict(payload), sort_keys=True, separators=(",", ":"), default=str)


def _webhook_payload_hash(payload: Mapping[str, object]) -> str:
    return hashlib.sha256(_webhook_canonical_payload_json(payload).encode()).hexdigest()


def _webhook_payload_event_id(payload: Mapping[str, object]) -> str | None:
    for key in _WEBHOOK_EVENT_ID_KEYS:
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _webhook_canonical_payload_json(payload: Mapping[str, object]) -> str:
    return json.dumps(_stable_webhook_payload(payload), sort_keys=True, separators=(",", ":"), default=str)


def _stable_webhook_payload(value: object) -> object:
    if isinstance(value, Mapping):
        return {
            str(key): _stable_webhook_payload(item)
            for key, item in value.items()
            if _webhook_key_signature(str(key)) not in _WEBHOOK_VOLATILE_KEYS
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_stable_webhook_payload(item) for item in value]
    return value


def _webhook_key_signature(key: str) -> str:
    return key.replace("_", "").replace("-", "").lower()


def _webhook_transaction_metadata(
    connector_name: str,
    resource_name: str,
    event_id: str,
    payload: Mapping[str, object],
) -> Mapping[str, object]:
    return {
        "webhookEvent": {
            "connectorName": connector_name,
            "resourceName": resource_name,
            "eventId": event_id,
            "payloadHash": _webhook_payload_hash(payload),
        }
    }


def _ensure_webhook_payload_matches(metadata: Mapping[str, object], event_id: str, payload_hash: str) -> None:
    event = metadata.get("webhookEvent")
    if not isinstance(event, Mapping) or event.get("payloadHash") == payload_hash:
        return
    raise ConflictDetected("webhook event id already exists with a different payload", details={"event_id": event_id})
