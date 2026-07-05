from __future__ import annotations

import argparse
import json
import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast
from uuid import uuid4

from foundry_lite.application.foundry import FoundryLite
from foundry_lite.application.ports import (
    StreamAdapter,
    StreamArchiveConfig,
    StreamEvent,
    StreamSchemaStrategy,
    WorkflowLedgerStatus,
    WorkflowRunRecord,
    WorkflowStartRequest,
    workflow_request_fingerprint,
    workflow_run_id,
)
from foundry_lite.application.ports.adapter_failure import AdapterError
from foundry_lite.application.primitives import CommitResult
from foundry_lite.application.services.runtime_error_payloads import runtime_error_payload
from foundry_lite.domain.context import DEFAULT_TENANT_ID, DEMO_ADMIN_ROLES, RequestContext
from foundry_lite.domain.errors import ConflictDetected, FoundryLiteError
from foundry_lite.infrastructure.adapters import (
    DebeziumPostgresSourceConfig,
    DebeziumPostgresStreamAdapter,
    KafkaStreamAdapter,
    KafkaStreamAdapterConfig,
    KafkaStreamSubscription,
)
from foundry_lite.infrastructure.local_runtime import create_local_core_dependencies


@dataclass(frozen=True)
class StreamArchiveWorkerConfig:
    dataset_ref: str
    stream_name: str
    topic: str
    bootstrap_servers: str
    storage_root: Path
    db_url: str | None = None
    adapter_profile: str = "local"
    consumer_group: str = "foundry-lite-archive"
    partition: int = 0
    limit: int = 100
    poll_timeout_seconds: float = 1.0
    max_empty_polls: int = 1
    schema_strategy: StreamSchemaStrategy = "envelope_json"
    time_zone: str | None = None
    cdc_primary_key: tuple[str, ...] = ()
    tenant_id: str = DEFAULT_TENANT_ID
    actor_user_id: str = "worker-stream-archive"
    request_id: str = "req-worker-stream-archive"
    sync_name: str | None = None
    is_continuous: bool = False
    continuous_max_batches: int | None = None
    continuous_max_empty_polls: int = 1
    worker_id: str = "stream-archive-worker"
    lease_ttl_seconds: int = 30

    def request_context(self, *, batch_number: int | None = None) -> RequestContext:
        tenant_id = _required_worker_value("tenant_id", self.tenant_id)
        actor_user_id = _required_worker_value("actor_user_id", self.actor_user_id)
        request_id = self.request_id if batch_number is None else f"{self.request_id}:batch-{batch_number}"
        return RequestContext(
            tenant_id=tenant_id,
            actor_user_id=actor_user_id,
            request_id=request_id,
            roles=DEMO_ADMIN_ROLES,
        )

    def stream_config(self) -> StreamArchiveConfig:
        return StreamArchiveConfig(
            stream_name=self.stream_name,
            topic=self.topic,
            consumer_group=self.consumer_group,
            partition=self.partition,
            limit=self.limit,
            schema_strategy=self.schema_strategy,
            time_zone=self.time_zone,
        )

    def stream_adapter(self) -> StreamAdapter:
        kafka_adapter = KafkaStreamAdapter(self.kafka_config())
        if self.schema_strategy == "cdc_envelope_json":
            return DebeziumPostgresStreamAdapter(
                kafka_adapter,
                DebeziumPostgresSourceConfig(primary_key=self.cdc_primary_key),
            )
        return kafka_adapter

    def kafka_config(self) -> KafkaStreamAdapterConfig:
        return KafkaStreamAdapterConfig(
            bootstrap_servers=self.bootstrap_servers,
            consumer_group=self.consumer_group,
            poll_timeout_seconds=self.poll_timeout_seconds,
            max_empty_polls=self.max_empty_polls,
            subscriptions=(
                KafkaStreamSubscription(
                    stream_name=self.stream_name,
                    topic=self.topic,
                    partition=self.partition,
                    default_tenant_id=self.tenant_id,
                ),
            ),
        )


@dataclass(frozen=True)
class ContinuousStreamArchiveResult:
    iterations: int
    archived_batches: int
    empty_polls: int
    rows_archived: int
    last_version_id: str | None
    stop_reason: str
    workflow_run_id: str | None = None
    lease_owner_id: str | None = None


@dataclass(frozen=True)
class StreamArchiveWorkerRuntime:
    foundry: FoundryLite


@dataclass(frozen=True)
class _WorkerLease:
    workflow_run_id: str
    owner_id: str
    token: str


class _AssignmentRevoked(ConflictDetected):
    pass


def run_stream_archive_once(
    config: StreamArchiveWorkerConfig,
    *,
    stream_adapter: StreamAdapter | None = None,
    assignment_is_current: Callable[[], bool] | None = None,
) -> CommitResult | None:
    runtime = build_stream_archive_runtime(config, stream_adapter=stream_adapter)
    return _run_stream_archive_once(
        config,
        runtime,
        ctx=config.request_context(),
        assignment_is_current=assignment_is_current,
    )


def build_stream_archive_runtime(
    config: StreamArchiveWorkerConfig,
    *,
    stream_adapter: StreamAdapter | None = None,
) -> StreamArchiveWorkerRuntime:
    dependencies = create_local_core_dependencies(
        db_url=config.db_url,
        storage_root=config.storage_root,
        adapter_profile=config.adapter_profile,
    )
    adapter = stream_adapter or config.stream_adapter()
    runtime_dependencies = replace(dependencies.runtime, stream_adapter=adapter)
    foundry = FoundryLite(dependencies=replace(dependencies, runtime=runtime_dependencies))
    ctx = config.request_context()
    foundry.datasets.ensure(config.dataset_ref, ctx=ctx, primary_key=["event_id"])
    return StreamArchiveWorkerRuntime(foundry=foundry)


def _run_stream_archive_once(
    config: StreamArchiveWorkerConfig,
    runtime: StreamArchiveWorkerRuntime,
    *,
    ctx: RequestContext,
    assignment_is_current: Callable[[], bool] | None = None,
) -> CommitResult | None:
    return runtime.foundry.datasets.archive_stream_events(
        config.dataset_ref,
        stream=config.stream_config(),
        ctx=ctx,
        sync_name=config.sync_name or f"kafka:{config.stream_name}:{config.consumer_group}",
        pre_commit_check=_pre_commit_assignment_guard(config, assignment_is_current),
    )


def run_stream_archive_continuously(
    config: StreamArchiveWorkerConfig,
    *,
    stream_adapter: StreamAdapter | None = None,
    should_stop: Callable[[], bool] | None = None,
    assignment_is_current: Callable[[], bool] | None = None,
) -> ContinuousStreamArchiveResult:
    stop_requested = should_stop or _never_stop
    runtime = build_stream_archive_runtime(config, stream_adapter=stream_adapter)
    lease = _claim_worker_lease(config, runtime, ctx=config.request_context())
    iterations = 0
    archived_batches = 0
    empty_polls = 0
    rows_archived = 0
    last_version_id: str | None = None
    while True:
        if stop_requested():
            return _finish_continuous_result(
                config,
                runtime,
                lease,
                iterations,
                archived_batches,
                empty_polls,
                rows_archived,
                last_version_id,
                "stop_requested",
            )
        if _max_batches_reached(archived_batches, config.continuous_max_batches):
            return _finish_continuous_result(
                config,
                runtime,
                lease,
                iterations,
                archived_batches,
                empty_polls,
                rows_archived,
                last_version_id,
                "max_batches",
            )
        if not _worker_lease_is_current(runtime, config.request_context(), lease):
            return _continuous_result(
                iterations, archived_batches, empty_polls, rows_archived, last_version_id, "lease_lost", lease
            )
        try:
            result = _run_stream_archive_once(
                config,
                runtime,
                ctx=config.request_context(batch_number=iterations + 1),
                assignment_is_current=assignment_is_current,
            )
        except _AssignmentRevoked:
            return _finish_continuous_result(
                config,
                runtime,
                lease,
                iterations,
                archived_batches,
                empty_polls,
                rows_archived,
                last_version_id,
                "assignment_revoked",
            )
        iterations += 1
        lease = _claim_worker_lease(config, runtime, ctx=config.request_context(), lease=lease)
        if result is None:
            empty_polls += 1
            if empty_polls >= config.continuous_max_empty_polls:
                return _finish_continuous_result(
                    config,
                    runtime,
                    lease,
                    iterations,
                    archived_batches,
                    empty_polls,
                    rows_archived,
                    last_version_id,
                    "empty_polls",
                )
            continue
        empty_polls = 0
        archived_batches += 1
        rows_archived += result.row_count
        last_version_id = result.version_id


def config_from_env(env: Mapping[str, str] | None = None) -> StreamArchiveWorkerConfig:
    values = env or os.environ
    storage_root = Path(values.get("FOUNDRY_LITE_HOME", ".foundry-lite"))
    return StreamArchiveWorkerConfig(
        dataset_ref=values.get("FOUNDRY_LITE_STREAM_DATASET", "raw.stream_archive"),
        stream_name=values.get("FOUNDRY_LITE_STREAM_NAME", "stream_archive"),
        topic=values.get("FOUNDRY_LITE_KAFKA_TOPIC", "foundry-lite-events"),
        bootstrap_servers=values.get("FOUNDRY_LITE_KAFKA_BOOTSTRAP_SERVERS", "localhost:9092"),
        storage_root=storage_root,
        db_url=values.get("FOUNDRY_LITE_DB_URL"),
        adapter_profile=values.get("FOUNDRY_LITE_ADAPTER_PROFILE", "local"),
        consumer_group=values.get("FOUNDRY_LITE_KAFKA_CONSUMER_GROUP", "foundry-lite-archive"),
        partition=_env_int(values, "FOUNDRY_LITE_KAFKA_PARTITION", 0),
        limit=_env_int(values, "FOUNDRY_LITE_STREAM_ARCHIVE_LIMIT", 100),
        poll_timeout_seconds=_env_float(values, "FOUNDRY_LITE_KAFKA_POLL_TIMEOUT_SECONDS", 1.0),
        max_empty_polls=_env_int(values, "FOUNDRY_LITE_KAFKA_MAX_EMPTY_POLLS", 1),
        schema_strategy=_env_schema_strategy(values),
        time_zone=_env_optional(values, "FOUNDRY_LITE_STREAM_TIME_ZONE"),
        cdc_primary_key=_env_csv_tuple(values, "FOUNDRY_LITE_CDC_PRIMARY_KEY"),
        tenant_id=values.get("FOUNDRY_LITE_TENANT_ID", DEFAULT_TENANT_ID),
        sync_name=values.get("FOUNDRY_LITE_STREAM_SYNC_NAME"),
        is_continuous=_env_bool(values, "FOUNDRY_LITE_STREAM_CONTINUOUS", False),
        continuous_max_batches=_env_optional_int(values, "FOUNDRY_LITE_STREAM_CONTINUOUS_MAX_BATCHES"),
        continuous_max_empty_polls=_env_int(values, "FOUNDRY_LITE_STREAM_CONTINUOUS_MAX_EMPTY_POLLS", 1),
        worker_id=values.get("FOUNDRY_LITE_STREAM_WORKER_ID", "stream-archive-worker"),
        lease_ttl_seconds=_env_int(values, "FOUNDRY_LITE_STREAM_LEASE_TTL_SECONDS", 30),
    )


def _required_worker_value(field: str, value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"stream archive worker requires {field}")
    return normalized


def main(argv: list[str] | None = None) -> int:
    config: StreamArchiveWorkerConfig | None = None
    try:
        parser = _parser()
        args = parser.parse_args(argv)
        config = _config_from_args(args)
        if config.is_continuous:
            continuous_result = run_stream_archive_continuously(config)
            print(_continuous_result_json(continuous_result))
            return 0
        result = run_stream_archive_once(config)
    except (AdapterError, FoundryLiteError, ValueError) as exc:
        print(_failure_json(exc, config))
        return 1
    print(_result_json(result))
    return 0


def _config_from_args(args: argparse.Namespace) -> StreamArchiveWorkerConfig:
    return replace(
        config_from_env(),
        dataset_ref=args.dataset_ref,
        stream_name=args.stream_name,
        topic=args.topic,
        bootstrap_servers=args.bootstrap_servers,
        storage_root=Path(args.storage_root),
        limit=args.limit,
        poll_timeout_seconds=args.poll_timeout_seconds,
        max_empty_polls=args.max_empty_polls,
        schema_strategy=args.schema_strategy,
        time_zone=args.time_zone,
        cdc_primary_key=_csv_tuple(args.cdc_primary_key),
        is_continuous=args.is_continuous,
        continuous_max_batches=args.max_batches,
        continuous_max_empty_polls=args.continuous_max_empty_polls,
        worker_id=args.worker_id,
        lease_ttl_seconds=args.lease_ttl_seconds,
    )


def _parser() -> argparse.ArgumentParser:
    defaults = config_from_env()
    parser = argparse.ArgumentParser(description="Archive one Kafka/Redpanda stream micro-batch into a raw dataset.")
    parser.add_argument("--dataset-ref", default=defaults.dataset_ref)
    parser.add_argument("--stream-name", default=defaults.stream_name)
    parser.add_argument("--topic", default=defaults.topic)
    parser.add_argument("--bootstrap-servers", default=defaults.bootstrap_servers)
    parser.add_argument("--storage-root", default=str(defaults.storage_root))
    parser.add_argument("--limit", type=int, default=defaults.limit)
    parser.add_argument("--poll-timeout-seconds", type=float, default=defaults.poll_timeout_seconds)
    parser.add_argument("--max-empty-polls", type=int, default=defaults.max_empty_polls)
    parser.add_argument(
        "--schema-strategy", choices=["envelope_json", "cdc_envelope_json"], default=defaults.schema_strategy
    )
    parser.add_argument("--time-zone", default=defaults.time_zone)
    parser.add_argument("--cdc-primary-key", default=",".join(defaults.cdc_primary_key))
    parser.add_argument("--continuous", action="store_true", default=defaults.is_continuous, dest="is_continuous")
    parser.add_argument("--max-batches", type=int, default=defaults.continuous_max_batches)
    parser.add_argument("--continuous-max-empty-polls", type=int, default=defaults.continuous_max_empty_polls)
    parser.add_argument("--worker-id", default=defaults.worker_id)
    parser.add_argument("--lease-ttl-seconds", type=int, default=defaults.lease_ttl_seconds)
    return parser


def _failure_json(exc: Exception, config: StreamArchiveWorkerConfig | None) -> str:
    return json.dumps(_failure_payload(exc, config), sort_keys=True)


def _failure_payload(exc: Exception, config: StreamArchiveWorkerConfig | None) -> Mapping[str, object]:
    if isinstance(exc, ValueError):
        payload: dict[str, object] = {"type": "CONFIGURATION_ERROR", "message": str(exc), "details": {}}
        if trace := _failure_trace(config):
            payload["trace"] = trace
        return payload
    return runtime_error_payload(exc, _failure_context(config), adapter="stream_archive_worker")


def _failure_trace(config: StreamArchiveWorkerConfig | None) -> Mapping[str, str]:
    ctx = _failure_context(config)
    if ctx is None:
        return {"adapter": "stream_archive_worker"}
    return {
        "tenant_id": ctx.tenant_id,
        "actor_user_id": ctx.actor_user_id,
        "request_id": ctx.request_id,
        "correlation_id": ctx.request_id,
        "adapter": "stream_archive_worker",
    }


def _failure_context(config: StreamArchiveWorkerConfig | None) -> RequestContext | None:
    if config is None:
        return None
    try:
        return config.request_context()
    except ValueError:
        return None


def _result_json(result: CommitResult | None) -> str:
    payload: Mapping[str, object]
    if result is None:
        payload = {"status": "NO_EVENTS"}
    else:
        payload = {
            "status": "ARCHIVED",
            "versionId": result.version_id,
            "versionNumber": result.version_number,
            "rowCount": result.row_count,
        }
    return json.dumps(payload, sort_keys=True)


def _continuous_result_json(result: ContinuousStreamArchiveResult) -> str:
    payload: dict[str, object] = {
        "status": "STOPPED",
        "stopReason": result.stop_reason,
        "iterations": result.iterations,
        "archivedBatches": result.archived_batches,
        "emptyPolls": result.empty_polls,
        "rowsArchived": result.rows_archived,
        "lastVersionId": result.last_version_id,
    }
    if result.workflow_run_id is not None:
        payload["workflowRunId"] = result.workflow_run_id
    if result.lease_owner_id is not None:
        payload["leaseOwnerId"] = result.lease_owner_id
    return json.dumps(payload, sort_keys=True)


def _claim_worker_lease(
    config: StreamArchiveWorkerConfig,
    runtime: StreamArchiveWorkerRuntime,
    *,
    ctx: RequestContext,
    lease: _WorkerLease | None = None,
) -> _WorkerLease:
    now = _utc_now()
    token = lease.token if lease is not None else f"lease-{uuid4().hex}"
    record = _worker_lease_record(config, ctx, token, now)
    with runtime.foundry.engine.begin() as transaction:
        row = runtime.foundry.runtime_repository.claim_workflow_run_lease(
            transaction=transaction,
            record=record,
            lease_owner_id=config.worker_id,
            now=_utc_iso(now),
        )
    if row is None:
        raise ConflictDetected("stream archive worker lease is owned by another worker", details=_lease_details(config))
    return _WorkerLease(workflow_run_id=row["id"], owner_id=config.worker_id, token=token)


def _worker_lease_is_current(
    runtime: StreamArchiveWorkerRuntime,
    ctx: RequestContext,
    lease: _WorkerLease,
) -> bool:
    with runtime.foundry.engine.begin() as transaction:
        row = runtime.foundry.runtime_repository.workflow_run_by_id(
            transaction=transaction,
            tenant_id=ctx.tenant_id,
            workflow_run_id=lease.workflow_run_id,
        )
    return row is not None and _row_has_worker_lease(row["output"], lease)


def _finish_continuous_result(
    config: StreamArchiveWorkerConfig,
    runtime: StreamArchiveWorkerRuntime,
    lease: _WorkerLease,
    iterations: int,
    archived_batches: int,
    empty_polls: int,
    rows_archived: int,
    last_version_id: str | None,
    stop_reason: str,
) -> ContinuousStreamArchiveResult:
    result = _continuous_result(
        iterations, archived_batches, empty_polls, rows_archived, last_version_id, stop_reason, lease
    )
    _release_worker_lease(config, runtime, lease, result)
    return result


def _release_worker_lease(
    config: StreamArchiveWorkerConfig,
    runtime: StreamArchiveWorkerRuntime,
    lease: _WorkerLease,
    result: ContinuousStreamArchiveResult,
) -> None:
    status: WorkflowLedgerStatus = (
        "cancelled" if result.stop_reason in {"assignment_revoked", "stop_requested"} else "succeeded"
    )
    with runtime.foundry.engine.begin() as transaction:
        runtime.foundry.runtime_repository.release_workflow_run_lease(
            transaction=transaction,
            tenant_id=config.tenant_id,
            workflow_run_id=lease.workflow_run_id,
            lease_owner_id=lease.owner_id,
            lease_token=lease.token,
            status=status,
            output=_continuous_output(result),
            error=None,
            completed_at=_utc_iso(_utc_now()),
        )


def _worker_lease_record(
    config: StreamArchiveWorkerConfig,
    ctx: RequestContext,
    lease_token: str,
    now: datetime,
) -> WorkflowRunRecord:
    request = _worker_lease_request(config, ctx)
    return WorkflowRunRecord(
        workflow_run_id=workflow_run_id(request),
        tenant_id=ctx.tenant_id,
        workflow_name=request.workflow_name,
        workflow_profile="stream-archive-worker",
        status="running",
        idempotency_key=request.idempotency_key,
        request_fingerprint=workflow_request_fingerprint(request),
        input=dict(request.input),
        output=_worker_lease_output(config, lease_token, now),
        error=None,
        dataset_id=None,
        audit_event_id=None,
        attempts=1,
        created_at=_utc_iso(now),
        started_at=_utc_iso(now),
        completed_at=None,
    )


def _worker_lease_request(config: StreamArchiveWorkerConfig, ctx: RequestContext) -> WorkflowStartRequest:
    return WorkflowStartRequest(
        workflow_name="ContinuousStreamArchiveWorker",
        tenant_id=ctx.tenant_id,
        request_id=ctx.request_id,
        idempotency_key=_worker_lease_key(config),
        input=_worker_lease_input(config),
    )


def _worker_lease_output(
    config: StreamArchiveWorkerConfig,
    lease_token: str,
    now: datetime,
) -> dict[str, object]:
    return {
        "workerLease": {
            "ownerId": config.worker_id,
            "leaseToken": lease_token,
            "leaseExpiresAt": _utc_iso(now + timedelta(seconds=config.lease_ttl_seconds)),
            "lastHeartbeatAt": _utc_iso(now),
        },
        "streamArchive": _worker_lease_input(config),
    }


def _continuous_output(result: ContinuousStreamArchiveResult) -> dict[str, object]:
    return {
        "stopReason": result.stop_reason,
        "iterations": result.iterations,
        "archivedBatches": result.archived_batches,
        "emptyPolls": result.empty_polls,
        "rowsArchived": result.rows_archived,
        "lastVersionId": result.last_version_id,
    }


def _row_has_worker_lease(output: Mapping[str, object], lease: _WorkerLease) -> bool:
    raw_lease = output.get("workerLease")
    if not isinstance(raw_lease, Mapping):
        return False
    return raw_lease.get("ownerId") == lease.owner_id and raw_lease.get("leaseToken") == lease.token


def _pre_commit_assignment_guard(
    config: StreamArchiveWorkerConfig,
    assignment_is_current: Callable[[], bool] | None,
) -> Callable[[Sequence[StreamEvent]], None] | None:
    if assignment_is_current is None:
        return None

    def guard(events: Sequence[StreamEvent]) -> None:
        if assignment_is_current():
            return
        raise _AssignmentRevoked(
            "stream archive partition assignment revoked before commit",
            details=_assignment_revoked_details(config, events),
        )

    return guard


def _assignment_revoked_details(
    config: StreamArchiveWorkerConfig,
    events: Sequence[StreamEvent],
) -> dict[str, object]:
    details = _worker_lease_input(config)
    if events:
        details["batchFirstOffset"] = events[0].offset
        details["batchLastOffset"] = events[-1].offset
    return details


def _worker_lease_input(config: StreamArchiveWorkerConfig) -> dict[str, object]:
    return {
        "datasetRef": config.dataset_ref,
        "streamName": config.stream_name,
        "topic": config.topic,
        "consumerGroup": config.consumer_group,
        "partition": config.partition,
    }


def _worker_lease_key(config: StreamArchiveWorkerConfig) -> str:
    return (
        f"stream-archive:{config.dataset_ref}:{config.stream_name}:"
        f"{config.topic}:{config.consumer_group}:{config.partition}"
    )


def _lease_details(config: StreamArchiveWorkerConfig) -> dict[str, object]:
    return {**_worker_lease_input(config), "workerId": config.worker_id}


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _utc_iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _continuous_result(
    iterations: int,
    archived_batches: int,
    empty_polls: int,
    rows_archived: int,
    last_version_id: str | None,
    stop_reason: str,
    lease: _WorkerLease | None = None,
) -> ContinuousStreamArchiveResult:
    return ContinuousStreamArchiveResult(
        iterations=iterations,
        archived_batches=archived_batches,
        empty_polls=empty_polls,
        rows_archived=rows_archived,
        last_version_id=last_version_id,
        stop_reason=stop_reason,
        workflow_run_id=lease.workflow_run_id if lease is not None else None,
        lease_owner_id=lease.owner_id if lease is not None else None,
    )


def _max_batches_reached(archived_batches: int, max_batches: int | None) -> bool:
    return max_batches is not None and archived_batches >= max_batches


def _never_stop() -> bool:
    return False


def _env_int(values: Mapping[str, str], name: str, default: int) -> int:
    raw_value = values.get(name)
    return default if raw_value is None else int(raw_value)


def _env_optional_int(values: Mapping[str, str], name: str) -> int | None:
    raw_value = values.get(name)
    return None if raw_value is None or raw_value == "" else int(raw_value)


def _env_float(values: Mapping[str, str], name: str, default: float) -> float:
    raw_value = values.get(name)
    return default if raw_value is None else float(raw_value)


def _env_bool(values: Mapping[str, str], name: str, is_default_enabled: bool) -> bool:
    raw_value = values.get(name)
    if raw_value is None:
        return is_default_enabled
    return raw_value.strip().lower() in {"1", "true", "yes", "on"}


def _env_optional(values: Mapping[str, str], name: str) -> str | None:
    value = values.get(name)
    return value if value else None


def _env_schema_strategy(values: Mapping[str, str]) -> StreamSchemaStrategy:
    raw_value = values.get("FOUNDRY_LITE_STREAM_SCHEMA_STRATEGY", "envelope_json")
    if raw_value in {"envelope_json", "cdc_envelope_json"}:
        return cast(StreamSchemaStrategy, raw_value)
    raise ValueError(f"unsupported stream schema strategy: {raw_value}")


def _env_csv_tuple(values: Mapping[str, str], name: str) -> tuple[str, ...]:
    return _csv_tuple(values.get(name, ""))


def _csv_tuple(value: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in value.split(",") if part.strip())


if __name__ == "__main__":
    raise SystemExit(main())
