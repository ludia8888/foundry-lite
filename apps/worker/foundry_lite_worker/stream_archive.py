from __future__ import annotations

import argparse
import json
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import cast

from foundry_lite.application.foundry import FoundryLite
from foundry_lite.application.ports import StreamAdapter, StreamArchiveConfig
from foundry_lite.application.ports.adapter_failure import AdapterError
from foundry_lite.application.ports.stream_adapter import StreamSchemaStrategy
from foundry_lite.application.primitives import CommitResult
from foundry_lite.application.services.runtime_error_payloads import runtime_error_payload
from foundry_lite.domain.context import DEFAULT_TENANT_ID, DEMO_ADMIN_ROLES, RequestContext
from foundry_lite.domain.errors import FoundryLiteError
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

    def request_context(self) -> RequestContext:
        tenant_id = _required_worker_value("tenant_id", self.tenant_id)
        actor_user_id = _required_worker_value("actor_user_id", self.actor_user_id)
        return RequestContext(
            tenant_id=tenant_id,
            actor_user_id=actor_user_id,
            request_id=self.request_id,
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


def run_stream_archive_once(
    config: StreamArchiveWorkerConfig,
    *,
    stream_adapter: StreamAdapter | None = None,
) -> CommitResult | None:
    dependencies = create_local_core_dependencies(
        db_url=config.db_url,
        storage_root=config.storage_root,
        adapter_profile=config.adapter_profile,
    )
    adapter = stream_adapter or config.stream_adapter()
    foundry = FoundryLite(dependencies=replace(dependencies, stream_adapter=adapter))
    ctx = config.request_context()
    foundry.datasets.ensure(config.dataset_ref, ctx=ctx, primary_key=["event_id"])
    return foundry.datasets.archive_stream_events(
        config.dataset_ref,
        stream=config.stream_config(),
        ctx=ctx,
        sync_name=config.sync_name or f"kafka:{config.stream_name}:{config.consumer_group}",
    )


def run_stream_archive_continuously(
    config: StreamArchiveWorkerConfig,
    *,
    stream_adapter: StreamAdapter | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> ContinuousStreamArchiveResult:
    stop_requested = should_stop or _never_stop
    iterations = 0
    archived_batches = 0
    empty_polls = 0
    rows_archived = 0
    last_version_id: str | None = None
    while True:
        if stop_requested():
            return _continuous_result(
                iterations, archived_batches, empty_polls, rows_archived, last_version_id, "stop_requested"
            )
        if _max_batches_reached(archived_batches, config.continuous_max_batches):
            return _continuous_result(
                iterations, archived_batches, empty_polls, rows_archived, last_version_id, "max_batches"
            )
        result = run_stream_archive_once(config, stream_adapter=stream_adapter)
        iterations += 1
        if result is None:
            empty_polls += 1
            if empty_polls >= config.continuous_max_empty_polls:
                return _continuous_result(
                    iterations, archived_batches, empty_polls, rows_archived, last_version_id, "empty_polls"
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
    return json.dumps(
        {
            "status": "STOPPED",
            "stopReason": result.stop_reason,
            "iterations": result.iterations,
            "archivedBatches": result.archived_batches,
            "emptyPolls": result.empty_polls,
            "rowsArchived": result.rows_archived,
            "lastVersionId": result.last_version_id,
        },
        sort_keys=True,
    )


def _continuous_result(
    iterations: int,
    archived_batches: int,
    empty_polls: int,
    rows_archived: int,
    last_version_id: str | None,
    stop_reason: str,
) -> ContinuousStreamArchiveResult:
    return ContinuousStreamArchiveResult(
        iterations=iterations,
        archived_batches=archived_batches,
        empty_polls=empty_polls,
        rows_archived=rows_archived,
        last_version_id=last_version_id,
        stop_reason=stop_reason,
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
