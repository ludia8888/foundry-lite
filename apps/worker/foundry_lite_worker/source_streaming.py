"""Always-on Source streaming service with durable lease and live telemetry."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event, Thread
from uuid import uuid4

from foundry_lite.application.foundry import FoundryLite
from foundry_lite.application.ports import WorkflowRunRecord, WorkflowRunRow
from foundry_lite.domain.context import DEFAULT_TENANT_ID, DEMO_ADMIN_ROLES, RequestContext
from foundry_lite.domain.errors import ConflictDetected, ValidationFailed
from foundry_lite.infrastructure.adapters import (
    KafkaStreamAdapter,
    KafkaStreamAdapterConfig,
    KafkaStreamSubscription,
    KrakenWebSocketV2Adapter,
    KrakenWebSocketV2Config,
)
from foundry_lite.infrastructure.local_runtime import create_local_core_dependencies

from foundry_lite_worker import source_streaming_support as support
from foundry_lite_worker.kraken_kafka_bridge import (
    KrakenBridgeSnapshot,
    KrakenBridgeTelemetry,
    KrakenKafkaBridgeConfig,
    run_kraken_kafka_bridge,
)

_ACTIVE_STATUSES = frozenset({"requested", "starting", "running", "start_unknown"})


@dataclass(frozen=True)
class SourceStreamingServiceConfig:
    sync_name: str
    storage_root: Path
    db_url: str | None = None
    adapter_profile: str = "local"
    tenant_id: str = DEFAULT_TENANT_ID
    actor_user_id: str = "worker-source-streaming"
    request_id: str = "req-worker-source-streaming"
    worker_id: str = "source-streaming-worker"
    lease_ttl_seconds: int = 30
    retry_base_seconds: float = 1.0
    retry_max_seconds: float = 30.0
    max_iterations: int | None = None

    def request_context(self, *, batch_number: int | None = None) -> RequestContext:
        request_id = self.request_id if batch_number is None else f"{self.request_id}:batch-{batch_number}"
        return RequestContext(
            tenant_id=support.required("tenant_id", self.tenant_id),
            actor_user_id=support.required("actor_user_id", self.actor_user_id),
            request_id=request_id,
            roles=DEMO_ADMIN_ROLES,
        )


@dataclass(frozen=True)
class SourceStreamingServiceResult:
    workflow_run_id: str
    stop_reason: str
    iterations: int
    archived_batches: int
    rows_archived: int
    last_version_id: str | None


@dataclass(frozen=True)
class _WorkerLease:
    workflow_run_id: str
    owner_id: str
    token: str


@dataclass(frozen=True)
class _BridgeHandle:
    stop_event: Event
    telemetry: KrakenBridgeTelemetry
    thread: Thread


@dataclass(frozen=True)
class _LoopState:
    iterations: int
    archived_batches: int
    rows_archived: int
    last_version_id: str | None
    consecutive_failures: int
    started_at: str
    last_source_run_id: str | None
    checkpoint: Mapping[str, object]
    last_checkpoint_at: str | None
    checkpoint_duration_ms: float | None
    output_rate_per_second: float | None
    kafka: Mapping[str, object]


@dataclass(frozen=True)
class _IterationOutcome:
    state: _LoopState
    lease: _WorkerLease
    stop_reason: str | None = None


class _StopRequested(Exception):
    pass


def run_source_streaming_service(
    config: SourceStreamingServiceConfig,
    *,
    foundry: FoundryLite | None = None,
    stop_event: Event | None = None,
    kraken: KrakenWebSocketV2Adapter | None = None,
    kafka: KafkaStreamAdapter | None = None,
) -> SourceStreamingServiceResult:
    runtime = foundry or build_source_streaming_foundry(config)
    is_runtime_owned = foundry is None
    requested_stop = stop_event or Event()
    bridge: _BridgeHandle | None = None
    try:
        ctx = config.request_context()
        sync = runtime.sources.get_managed_sync(config.sync_name, ctx=ctx)
        source = runtime.sources.get_source(str(sync["sourceName"]), ctx=ctx)
        row = _linked_workflow(runtime, config, sync)
        state = _state_from_output(row["output"])
        lease = _claim_lease(runtime, config, row, state, None, None)
        bridge = _start_bridge(config, sync, source, ctx, kraken=kraken, kafka=kafka)
        return _run_loop(runtime, config, sync, lease, state, requested_stop, bridge)
    finally:
        try:
            _stop_bridge(bridge)
        finally:
            if is_runtime_owned:
                runtime.close()


def _run_loop(
    foundry: FoundryLite,
    config: SourceStreamingServiceConfig,
    sync: Mapping[str, object],
    lease: _WorkerLease,
    initial_state: _LoopState,
    stop_event: Event,
    bridge: _BridgeHandle | None,
) -> SourceStreamingServiceResult:
    state = initial_state
    current_lease = lease
    while not stop_event.is_set() and not support.maximum_reached(state.iterations, config.max_iterations):
        outcome = _run_iteration(foundry, config, current_lease, state, stop_event, bridge)
        state, current_lease = outcome.state, outcome.lease
        if outcome.stop_reason is not None:
            return _result(current_lease, state, outcome.stop_reason)
    reason = "shutdown_requested" if stop_event.is_set() else "max_iterations"
    return _result(current_lease, state, reason)


def _run_iteration(
    foundry: FoundryLite,
    config: SourceStreamingServiceConfig,
    lease: _WorkerLease,
    state: _LoopState,
    stop_event: Event,
    bridge: _BridgeHandle | None,
) -> _IterationOutcome:
    if _workflow_was_stopped(foundry, config, lease):
        return _IterationOutcome(state, lease, "stop_requested")
    try:
        run = _run_managed_batch(foundry, config, lease, state, bridge, state.iterations + 1)
    except _StopRequested:
        return _IterationOutcome(state, lease, "stop_requested")
    except Exception as exc:
        return _recover_failed_iteration(foundry, config, lease, state, stop_event, bridge, exc)
    next_state = _state_after_run(state, run)
    try:
        next_lease = _refresh_lease(foundry, config, lease, next_state, bridge, _run_error(run))
    except _StopRequested:
        return _IterationOutcome(next_state, lease, "stop_requested")
    if run.get("status") == "failed" and stop_event.wait(support.retry_delay(config, next_state.consecutive_failures)):
        return _IterationOutcome(next_state, next_lease, "shutdown_requested")
    return _IterationOutcome(next_state, next_lease)


def _recover_failed_iteration(
    foundry: FoundryLite,
    config: SourceStreamingServiceConfig,
    lease: _WorkerLease,
    state: _LoopState,
    stop_event: Event,
    bridge: _BridgeHandle | None,
    exc: Exception,
) -> _IterationOutcome:
    next_state = _state_after_failure(state)
    try:
        next_lease = _refresh_lease(foundry, config, lease, next_state, bridge, support.worker_error_payload(exc))
    except _StopRequested:
        return _IterationOutcome(next_state, lease, "stop_requested")
    stop_reason = (
        "shutdown_requested" if stop_event.wait(support.retry_delay(config, next_state.consecutive_failures)) else None
    )
    return _IterationOutcome(next_state, next_lease, stop_reason)


def _run_managed_batch(
    foundry: FoundryLite,
    config: SourceStreamingServiceConfig,
    lease: _WorkerLease,
    state: _LoopState,
    bridge: _BridgeHandle | None,
    batch_number: int,
) -> Mapping[str, object]:
    return support.run_with_heartbeat(
        operation=lambda: foundry.sources.start_managed_sync_run(
            config.sync_name,
            idempotency_key=f"{lease.workflow_run_id}:batch:{batch_number}",
            trigger_type="manual",
            ctx=config.request_context(batch_number=batch_number),
        ),
        heartbeat=lambda: _refresh_lease(foundry, config, lease, state, bridge, None),
        interval_seconds=max(1.0, min(5.0, config.lease_ttl_seconds / 3)),
    )


def build_source_streaming_foundry(config: SourceStreamingServiceConfig) -> FoundryLite:
    dependencies = create_local_core_dependencies(
        db_url=config.db_url,
        storage_root=config.storage_root,
        adapter_profile=config.adapter_profile,
    )
    return FoundryLite(dependencies=dependencies)


def _linked_workflow(
    foundry: FoundryLite,
    config: SourceStreamingServiceConfig,
    sync: Mapping[str, object],
) -> WorkflowRunRow:
    workflow_run_id = sync.get("lastWorkflowRunId")
    if not isinstance(workflow_run_id, str) or not workflow_run_id:
        raise ValidationFailed(
            "streaming sync has no requested lifecycle run",
            details={"syncName": config.sync_name, "operatorAction": "Start the stream from Data Connection."},
        )
    row = _workflow_row(foundry, config.tenant_id, workflow_run_id)
    if row["status"] not in _ACTIVE_STATUSES:
        raise ConflictDetected(
            "streaming sync lifecycle run is not active",
            details={"syncName": config.sync_name, "status": row["status"]},
        )
    return row


def _workflow_row(foundry: FoundryLite, tenant_id: str, workflow_run_id: str) -> WorkflowRunRow:
    with foundry.engine.begin() as transaction:
        row = foundry.runtime_repository.workflow_run_by_id(
            transaction=transaction, tenant_id=tenant_id, workflow_run_id=workflow_run_id
        )
    if row is None:
        raise ValidationFailed("streaming workflow run was not found", details={"workflowRunId": workflow_run_id})
    return row


def _claim_lease(
    foundry: FoundryLite,
    config: SourceStreamingServiceConfig,
    row: WorkflowRunRow,
    state: _LoopState,
    lease: _WorkerLease | None,
    bridge: _BridgeHandle | None,
    error: Mapping[str, object] | None = None,
) -> _WorkerLease:
    now = datetime.now(UTC)
    token = lease.token if lease is not None else f"lease-{uuid4().hex}"
    output = _workflow_output(config, state, token, now, bridge, error)
    record = _lease_record(row, config, output, now)
    with foundry.engine.begin() as transaction:
        claimed = foundry.runtime_repository.claim_workflow_run_lease(
            transaction=transaction,
            record=record,
            lease_owner_id=config.worker_id,
            now=now.isoformat(),
        )
    if claimed is None:
        raise ConflictDetected(
            "streaming sync worker lease is owned by another worker",
            details={"syncName": config.sync_name, "workerId": config.worker_id},
        )
    return _WorkerLease(workflow_run_id=claimed["id"], owner_id=config.worker_id, token=token)


def _refresh_lease(
    foundry: FoundryLite,
    config: SourceStreamingServiceConfig,
    lease: _WorkerLease,
    state: _LoopState,
    bridge: _BridgeHandle | None,
    error: Mapping[str, object] | None,
) -> _WorkerLease:
    row = _workflow_row(foundry, config.tenant_id, lease.workflow_run_id)
    if row["status"] not in _ACTIVE_STATUSES:
        raise _StopRequested
    return _claim_lease(foundry, config, row, state, lease, bridge, error)


def _lease_record(
    row: WorkflowRunRow,
    config: SourceStreamingServiceConfig,
    output: Mapping[str, object],
    observed_at: datetime,
) -> WorkflowRunRecord:
    return WorkflowRunRecord(
        workflow_run_id=row["id"],
        tenant_id=row["tenant_id"],
        workflow_name=row["workflow_name"],
        workflow_profile=row["workflow_profile"],
        status="running",
        idempotency_key=row["idempotency_key"],
        request_fingerprint=row["request_fingerprint"],
        input=dict(row["input"]),
        output=dict(output),
        error=None,
        dataset_id=row["dataset_id"],
        audit_event_id=row["audit_event_id"],
        attempts=int(row["attempts"]),
        created_at=row["created_at"],
        started_at=row["started_at"] or observed_at.isoformat(),
        completed_at=None,
    )


def _workflow_output(
    config: SourceStreamingServiceConfig,
    state: _LoopState,
    token: str,
    observed_at: datetime,
    bridge: _BridgeHandle | None,
    error: Mapping[str, object] | None,
) -> dict[str, object]:
    bridge_snapshot = bridge.telemetry.snapshot() if bridge is not None else None
    return {
        "desiredState": "RUNNING",
        "lifecycleState": _lifecycle_state(state, bridge_snapshot),
        "workerLease": {
            "ownerId": config.worker_id,
            "leaseToken": token,
            "leaseExpiresAt": (observed_at + timedelta(seconds=config.lease_ttl_seconds)).isoformat(),
            "lastHeartbeatAt": observed_at.isoformat(),
        },
        "telemetry": _telemetry_payload(config, state, observed_at, bridge_snapshot, error),
    }


def _telemetry_payload(
    config: SourceStreamingServiceConfig,
    state: _LoopState,
    observed_at: datetime,
    bridge: KrakenBridgeSnapshot | None,
    error: Mapping[str, object] | None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "syncName": config.sync_name,
        "startedAt": state.started_at,
        "lastHeartbeatAt": observed_at.isoformat(),
        "iterations": state.iterations,
        "archivedBatches": state.archived_batches,
        "outputRecords": state.rows_archived,
        "lastDatasetVersionId": state.last_version_id,
        "lastSourceRunId": state.last_source_run_id,
        "checkpoint": dict(state.checkpoint),
        "lastCheckpointAt": state.last_checkpoint_at,
        "checkpointDurationMs": state.checkpoint_duration_ms,
        "outputRatePerSecond": state.output_rate_per_second,
        "kafka": dict(state.kafka),
        "consecutiveFailures": state.consecutive_failures,
        "lastError": dict(error) if error is not None else None,
    }
    if bridge is not None:
        payload["inputRecords"] = bridge.published_records
        payload["inputRatePerSecond"] = bridge.input_rate_per_second
        payload["upstream"] = support.bridge_payload(bridge)
    return payload


def _state_from_output(output: Mapping[str, object]) -> _LoopState:
    telemetry = support.mapping(output.get("telemetry"))
    return _LoopState(
        iterations=support.integer(telemetry.get("iterations")),
        archived_batches=support.integer(telemetry.get("archivedBatches")),
        rows_archived=support.integer(telemetry.get("outputRecords")),
        last_version_id=support.optional_text(telemetry.get("lastDatasetVersionId")),
        consecutive_failures=support.integer(telemetry.get("consecutiveFailures")),
        started_at=support.optional_text(telemetry.get("startedAt")) or datetime.now(UTC).isoformat(),
        last_source_run_id=support.optional_text(telemetry.get("lastSourceRunId")),
        checkpoint=support.mapping(telemetry.get("checkpoint")),
        last_checkpoint_at=support.optional_text(telemetry.get("lastCheckpointAt")),
        checkpoint_duration_ms=support.optional_float(telemetry.get("checkpointDurationMs")),
        output_rate_per_second=support.optional_float(telemetry.get("outputRatePerSecond")),
        kafka=support.mapping(telemetry.get("kafka")),
    )


def _state_after_run(state: _LoopState, run: Mapping[str, object]) -> _LoopState:
    summary = support.mapping(run.get("resultSummary"))
    failed = run.get("status") == "failed"
    rows = support.integer(summary.get("eventCount"))
    version_id = support.optional_text(run.get("datasetVersionId"))
    elapsed_ms = support.optional_float(summary.get("elapsedMs"))
    committed_partitions = support.integer(summary.get("committedPartitionCount"))
    committed_batches = committed_partitions or (1 if version_id is not None else 0)
    return _LoopState(
        iterations=state.iterations + 1,
        archived_batches=state.archived_batches + committed_batches,
        rows_archived=state.rows_archived + rows,
        last_version_id=version_id or state.last_version_id,
        consecutive_failures=state.consecutive_failures + 1 if failed else 0,
        started_at=state.started_at,
        last_source_run_id=support.optional_text(run.get("runId")) or state.last_source_run_id,
        checkpoint=support.mapping(run.get("checkpointEnd")) or dict(state.checkpoint),
        last_checkpoint_at=datetime.now(UTC).isoformat() if not failed else state.last_checkpoint_at,
        checkpoint_duration_ms=elapsed_ms if not failed else state.checkpoint_duration_ms,
        output_rate_per_second=support.output_rate(rows, elapsed_ms) if not failed else state.output_rate_per_second,
        kafka=support.kafka_telemetry(summary, run, state.kafka),
    )


def _state_after_failure(state: _LoopState) -> _LoopState:
    return _LoopState(
        iterations=state.iterations + 1,
        archived_batches=state.archived_batches,
        rows_archived=state.rows_archived,
        last_version_id=state.last_version_id,
        consecutive_failures=state.consecutive_failures + 1,
        started_at=state.started_at,
        last_source_run_id=state.last_source_run_id,
        checkpoint=dict(state.checkpoint),
        last_checkpoint_at=state.last_checkpoint_at,
        checkpoint_duration_ms=state.checkpoint_duration_ms,
        output_rate_per_second=state.output_rate_per_second,
        kafka=dict(state.kafka),
    )


def _start_bridge(
    config: SourceStreamingServiceConfig,
    sync: Mapping[str, object],
    source: Mapping[str, object],
    ctx: RequestContext,
    *,
    kraken: KrakenWebSocketV2Adapter | None,
    kafka: KafkaStreamAdapter | None,
) -> _BridgeHandle | None:
    summary = {**support.mapping(source.get("configSummary")), **support.mapping(sync.get("configSummary"))}
    if summary.get("upstreamProvider") != "kraken_websocket_v2":
        return None
    stream_name = support.required_config(summary, "streamName")
    kraken_adapter = kraken or _kraken_adapter(summary)
    kafka_adapter = kafka or _kafka_adapter(config, summary, stream_name)
    owned_kafka = kafka_adapter if kafka is None else None
    stop_event = Event()
    telemetry = KrakenBridgeTelemetry()
    thread = Thread(
        target=_run_bridge,
        kwargs={
            "config": KrakenKafkaBridgeConfig(stream_name=stream_name),
            "kraken": kraken_adapter,
            "kafka": kafka_adapter,
            "ctx": ctx,
            "stop_event": stop_event,
            "telemetry": telemetry,
            "owned_kafka": owned_kafka,
        },
        name=f"kraken-kafka-{config.sync_name}",
        daemon=True,
    )
    try:
        thread.start()
    except Exception:
        if owned_kafka is not None:
            owned_kafka.close()
        raise
    return _BridgeHandle(stop_event=stop_event, telemetry=telemetry, thread=thread)


def _run_bridge(
    *,
    config: KrakenKafkaBridgeConfig,
    kraken: KrakenWebSocketV2Adapter,
    kafka: KafkaStreamAdapter,
    ctx: RequestContext,
    stop_event: Event,
    telemetry: KrakenBridgeTelemetry,
    owned_kafka: KafkaStreamAdapter | None,
) -> None:
    try:
        run_kraken_kafka_bridge(
            config,
            kraken=kraken,
            kafka=kafka,
            ctx=ctx,
            stop_event=stop_event,
            telemetry=telemetry,
        )
    finally:
        if owned_kafka is not None:
            owned_kafka.close()


def _kraken_adapter(summary: Mapping[str, object]) -> KrakenWebSocketV2Adapter:
    return KrakenWebSocketV2Adapter(
        KrakenWebSocketV2Config(
            websocket_url=support.optional_text(summary.get("upstreamWebsocketUrl")) or "wss://ws.kraken.com/v2",
            symbol=support.optional_text(summary.get("upstreamSymbol")) or "BTC/USD",
        )
    )


def _kafka_adapter(
    config: SourceStreamingServiceConfig,
    summary: Mapping[str, object],
    stream_name: str,
) -> KafkaStreamAdapter:
    subscription = KafkaStreamSubscription(
        stream_name=stream_name,
        topic=support.required_config(summary, "topic"),
        partition=support.integer(summary.get("partition")),
        default_tenant_id=config.tenant_id,
    )
    return KafkaStreamAdapter(
        KafkaStreamAdapterConfig(
            bootstrap_servers=support.required_config(summary, "bootstrapServers"),
            consumer_group=support.required_config(summary, "consumerGroup"),
            subscriptions=(subscription,),
        )
    )


def _stop_bridge(bridge: _BridgeHandle | None) -> None:
    if bridge is None:
        return
    bridge.stop_event.set()
    bridge.thread.join(timeout=5.0)
    if bridge.thread.is_alive():
        raise RuntimeError("Kraken Kafka bridge did not stop within 5 seconds")


def _workflow_was_stopped(foundry: FoundryLite, config: SourceStreamingServiceConfig, lease: _WorkerLease) -> bool:
    return _workflow_row(foundry, config.tenant_id, lease.workflow_run_id)["status"] not in _ACTIVE_STATUSES


def _lifecycle_state(state: _LoopState, bridge: KrakenBridgeSnapshot | None) -> str:
    if bridge is not None and bridge.connection_state in {"RECONNECTING", "FAILED"}:
        return "DEGRADED"
    return "RUNNING" if state.iterations > 0 else "STARTING"


def _run_error(run: Mapping[str, object]) -> Mapping[str, object] | None:
    return support.mapping(run.get("error")) or None


def _result(lease: _WorkerLease, state: _LoopState, reason: str) -> SourceStreamingServiceResult:
    return SourceStreamingServiceResult(
        workflow_run_id=lease.workflow_run_id,
        stop_reason=reason,
        iterations=state.iterations,
        archived_batches=state.archived_batches,
        rows_archived=state.rows_archived,
        last_version_id=state.last_version_id,
    )
